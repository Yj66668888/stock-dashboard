# -*- coding: utf-8 -*-
"""
隔夜跳空质量 —— 有效性检验（2026-11-03）
================================================================================
问题：过去一周反复「低开 + 当天回不到前收」的票，下一周还会这样吗？
      如果不会（没有持续性），这套过滤就是自欺欺人。

设计（无未来函数）
--------------------------------------------------------------------------------
  在每个交易日 t 收盘后，只用 bars[≤t] 算隔夜质量分组（window 5 日）；
  然后看 t+1 日：
      next_gap   t+1 开盘相对 t 收盘的跳空（%）
      next_rec   t+1 盘中最高是否 ≥ t 收盘（"白天摸不摸得回前收"）
      next_hold  t 收盘买入、t+1 收盘卖出（%）
      next_intra t+1 开盘买入、t+1 收盘卖出（%）

  统计按交易日聚类（有效样本 = 交易日数），不做逐股独立。

用法：python3 validate_gap_quality.py
"""
import json
import sys
from collections import defaultdict

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

DAILY = '.cache_daily_universe.json'
WIN = 5
GAP_TH = -1.0
BAD_N = 2
REC_MIN = 0.5      # 仅作展示，不参与规则
HARD_GAP = -3.0


def cluster_t(per_day_means):
    a = np.array([x for x in per_day_means if x is not None and not np.isnan(x)], dtype=float)
    n = len(a)
    if n < 5:
        return dict(n=n, mean=float('nan'), t=float('nan'))
    m = a.mean()
    sd = a.std(ddof=1)
    t = m / (sd / np.sqrt(n)) if sd > 0 else float('nan')
    return dict(n=n, mean=float(m), t=float(t))


def main():
    raw = json.load(open(DAILY, encoding='utf-8'))
    bars_all = raw['bars']

    # label -> {metric: {date: [values]}}
    acc = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    exclude_rate = []

    for code, bars in bars_all.items():
        if len(bars) < 60:
            continue
        try:
            arr = np.array([[float(b[1]), float(b[2]), float(b[3])] for b in bars], dtype=float)
            dates = [b[0] for b in bars]
        except Exception:
            continue
        op, cl, hi = arr[:, 0], arr[:, 1], arr[:, 2]
        n = len(cl)

        prev_cl = np.concatenate(([np.nan], cl[:-1]))
        gaps = op / prev_cl - 1
        gaps = gaps * 100
        rec = (hi >= prev_cl).astype(float)      # 当日是否摸到前收
        rec[0] = np.nan

        # t 的窗口 = gap[t-4..t]；valid t from 5 to n-2
        wg = sliding_window_view(gaps, WIN)      # j -> gaps[j..j+4]
        wr = sliding_window_view(rec, WIN)

        bad = (wg <= GAP_TH)
        bad_cnt = bad.sum(axis=1)
        bad_rec_cnt = (bad & (wr == 1)).sum(axis=1)
        with np.errstate(invalid='ignore'):
            rec_rate = np.where(bad_cnt > 0, bad_rec_cnt / np.maximum(bad_cnt, 1), np.nan)
        worst = np.nanmin(wg, axis=1)
        # 窗口内最差那天是否摸回前收（R2 要用"那一条"的回补情况，不是末日）
        with np.errstate(invalid='ignore'):
            imin = np.nanargmin(wg, axis=1)
        worst_rec = np.take_along_axis(wr, imin[:, None], axis=1)[:, 0]

        for j in range(1, len(wg)):
            t = j + WIN - 1                  # 窗口末日
            tp = t + 1                       # 次日
            if tp >= n:
                break
            if np.isnan(worst[j]) or np.isnan(gaps[tp]):
                continue

            bc = int(bad_cnt[j])
            rr = rec_rate[j]
            wst = worst[j]

            flags = []
            if bc >= BAD_N:              # 反复低开（不要求回补，实测更强）
                flags.append('R1')
            if wst <= HARD_GAP and worst_rec[j] == 0:
                flags.append('R2')
            if flags:
                label = 'R2深坑未补' if 'R2' in flags else 'R1反复低开'
            elif bc >= 1:
                label = '偶发低开'
            else:
                label = '隔夜稳'
            label = label + '|' + ('排除' if flags else '保留')

            d = dates[tp]
            acc[label]['gap'][d].append(gaps[tp])                    # 次日跳空
            acc[label]['rec'][d].append(rec[tp])                     # 次日是否摸回前收
            acc[label]['hold'][d].append((cl[tp] / cl[t] - 1) * 100)  # T收→T+1收
            acc[label]['intra'][d].append((cl[tp] / op[tp] - 1) * 100)

    print('=' * 96)
    print('隔夜质量分组 → 次日表现（全市场 %d 只，窗口 %d 日，按交易日聚类）' % (len(bars_all), WIN))
    print('=' * 96)
    print('%-20s %6s %9s %9s %9s %9s %9s %8s'
          % ('分组', '样本日', '次日跳空', '低开>1%', '摸回前收', 'T收→T+1收', '开盘→收盘', 't(hold)'))
    print('-' * 96)

    order = ['隔夜稳|保留', '偶发低开|保留', 'R1反复低开|排除', 'R2深坑未补|排除']
    out = {}
    for lb in order:
        if lb not in acc:
            continue
        m = acc[lb]
        days = sorted(set(m['gap']))
        allgap = [x for d in days for x in m['gap'][d]]
        allrec = [x for d in days for x in m['rec'][d]]
        dayhold = [float(np.mean(m['hold'][d])) for d in days if m['hold'][d]]
        allintra = [x for d in days for x in m['intra'][d]]
        ct = cluster_t(dayhold)
        stat = dict(
            days=len(days), n=len(allgap),
            gap=float(np.mean(allgap)),
            low1=100.0 * np.mean([g <= -1 for g in allgap]),
            rec=100.0 * np.mean([r == 1 for r in allrec]),
            hold=float(np.mean([x for d in days for x in m['hold'][d]])),
            hold_t=ct['t'], intra=float(np.mean(allintra)),
        )
        out[lb] = stat
        print('%-20s %6d %+8.3f%% %8.1f%% %8.1f%% %+8.3f%% %+8.3f%% %8.2f'
              % (lb.split('|')[0], stat['days'], stat['gap'], stat['low1'], stat['rec'],
                 stat['hold'], stat['intra'], stat['hold_t']))

    # 排除 vs 保留 的日度差
    keep = [lb for lb in acc if lb.endswith('|保留')]
    drop = [lb for lb in acc if lb.endswith('|排除')]
    if keep and drop:
        kg = defaultdict(list)
        dg = defaultdict(list)
        kh = defaultdict(list)
        dh = defaultdict(list)
        for lb in keep:
            for d, v in acc[lb]['gap'].items():
                kg[d].extend(v)
            for d, v in acc[lb]['hold'].items():
                kh[d].extend(v)
        for lb in drop:
            for d, v in acc[lb]['gap'].items():
                dg[d].extend(v)
            for d, v in acc[lb]['hold'].items():
                dh[d].extend(v)
        dates = sorted(set(kg) & set(dg))
        gdiff = [float(np.mean(kg[d])) - float(np.mean(dg[d])) for d in dates]
        hdiff = [float(np.mean(kh[d])) - float(np.mean(dh[d])) for d in dates]
        cg, ch = cluster_t(gdiff), cluster_t(hdiff)
        print('-' * 96)
        print('保留组 − 排除组： 次日跳空差 %+.3f%%（t=%.2f，%d 天） ｜ T收→T+1收差 %+.3f%%（t=%.2f）'
              % (cg['mean'], cg['t'], cg['n'], ch['mean'], ch['t']))
        out['diff'] = dict(gap=cg, hold=ch)
        # 排除组在次日继续"低开不回"的概率
        dr = [x for lb in drop for d in acc[lb]['rec'] for x in acc[lb]['rec'][d]]
        dg2 = [x for lb in drop for d in acc[lb]['gap'] for x in acc[lb]['gap'][d]]
        kr = [x for lb in keep for d in acc[lb]['rec'] for x in acc[lb]['rec'][d]]
        kg2 = [x for lb in keep for d in acc[lb]['gap'] for x in acc[lb]['gap'][d]]
        print('排除组次日：跳空≤−1%% 占 %.1f%%、摸回前收 %.1f%% ｜ 保留组：%.1f%% / %.1f%%'
              % (100 * np.mean([g <= -1 for g in dg2]), 100 * np.mean([r == 1 for r in dr]),
                 100 * np.mean([g <= -1 for g in kg2]), 100 * np.mean([r == 1 for r in kr])))
        out['rates'] = dict(
            drop_low1=100 * float(np.mean([g <= -1 for g in dg2])),
            drop_rec=100 * float(np.mean([r == 1 for r in dr])),
            keep_low1=100 * float(np.mean([g <= -1 for g in kg2])),
            keep_rec=100 * float(np.mean([r == 1 for r in kr])),
        )

    json.dump(out, open('_gap_quality_validation.json', 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    print('\n已存 _gap_quality_validation.json')


if __name__ == '__main__':
    main()
