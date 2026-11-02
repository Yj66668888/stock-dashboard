#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
validate_day_trend.py —— 日K趋势档（方向轴）的横截面有效性检验

================================================================================
为什么要有这个脚本
================================================================================
2026-11-02 用户反馈「推的票日K好多在走下坡」，于是双名单加了日K优先层。
但既有归因（2025-10~2026-10）有一条与它直接打架的结论：
    **MACD 有效的是水位不是形态：DIF<0 组 +0.39%/天，DIF>0 组 −1.56%/天**
→ 也就是「水下抄底」样本里，金叉/水上反而是减分的。
所以必须实测：日K档位到底有没有超额，还是只是看着舒服？

================================================================================
方法（与 analyze_contrarian.py 同一套口径，保证可比）
================================================================================
· 因子用 **T-1 收盘** 的数据算（严格无未来函数），收益看 **第 T 天**
· 横截面分组：不排序分位，直接按日K四档分组（档位本身是离散的）
    G1 ✦刚金叉 / ↑上涨（ok 层）  G2 →横盘 / ✦金叉·仍弱  G3 ↓下坡
· 指标：平均超额收益 mean(r - r_mkt)、真实红盘率 P(r>0)
· 显著性：**按交易日聚类** t 检验（有效样本量 = 交易日数，不是行数）
· 场景分组：跌日(mkt≤-0.5%) / 涨日(mkt≥+0.5%) / 平淡日，与双名单阈值一致
· 清洗：|当日收益| > 10.5% 剔除；历史不足 60 根的票跳过

用法: python3 validate_day_trend.py
"""
import json
import os
import random
from collections import defaultdict

import numpy as np

BASE = os.path.dirname(os.path.abspath(__file__))
DAILY = os.path.join(BASE, '.cache_daily_universe.json')
IDX = os.path.join(BASE, '_index_ohlc.json')

ABS_RET_MAX = 0.105     # 除权/异常剔除
DOWN_TH, UP_TH = -0.005, 0.005
HIST_MIN = 60
GOLD_WIN, HIST_TURN_WIN = 5, 2
NDAYS = 243             # 与既有归因同窗口（2025-10-30 ~ 2026-10-30）


def ema(vals, n):
    k = 2.0 / (n + 1)
    out, e = [], vals[0]
    for v in vals:
        e = v * k + e * (1 - k)
        out.append(e)
    return out


def tier_series(c):
    """对整条收盘序列算日K档位，返回与 c 等长的 list[int]
    3 = ✦刚金叉/↑上涨（ok 层）  1 = →横盘/✦金叉·仍弱  0 = ↓下坡"""
    n = len(c)
    dif, dea, hist = None, None, None
    ef, es = ema(c, 12), ema(c, 26)
    dif = [a - b for a, b in zip(ef, es)]
    dea = ema(dif, 9)
    hist = [a - b for a, b in zip(dif, dea)]

    # 前缀和，O(1) 求任意窗口均值
    pre = [0.0]
    for x in c:
        pre.append(pre[-1] + x)

    def ma(i, w):        # 截至 i（含）的 w 日均值
        s = max(0, i - w + 1)
        return (pre[i + 1] - pre[s]) / (i - s + 1)

    out = [None] * n
    for i in range(n):
        if i < 30:
            continue
        ma10 = ma(i, 10)
        ma20 = ma(i, 20)
        ma10_prev = ma(i - 1, 10)
        r5 = (c[i] / c[i - 5] - 1) if (i >= 5 and c[i - 5] > 0) else None
        r10 = (c[i] / c[i - 10] - 1) if (i >= 10 and c[i - 10] > 0) else None

        base = 0
        if c[i] > ma20:
            base += 1
        if ma10 > ma10_prev:
            base += 1
        if r5 is not None and r5 > 0:
            base += 1
        if r10 is not None and r10 > 0:
            base += 1

        gold = False
        lo = max(1, i - GOLD_WIN)
        for j in range(i, lo - 1, -1):
            if dif[j] > dea[j] and dif[j - 1] <= dea[j - 1]:
                gold = True
                break
        if not gold:
            for j in range(i, max(0, i - HIST_TURN_WIN) - 1, -1):
                if hist[j] > 0 and hist[j - 1] <= 0:
                    gold = True
                    break

        if (gold and base >= 2) or base >= 3:
            out[i] = 3
        elif base == 2 or gold:
            out[i] = 1
        else:
            out[i] = 0
    return out


def cluster_t(diffs):
    a = np.array(diffs, dtype=float)
    a = a[~np.isnan(a)]
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
    idx = json.load(open(IDX, encoding='utf-8'))
    rows = idx['data']['sh000001']['rows']

    # ---- 市场收益（按日期） ----
    mkt = {}
    dates = []
    for k in range(1, len(rows)):
        d, pc, cc = rows[k][0], float(rows[k - 1][2]), float(rows[k][2])
        if pc > 0:
            mkt[d] = cc / pc - 1
            dates.append(d)
    dates = dates[-NDAYS:]
    print('交易日 %d 天：%s ~ %s' % (len(dates), dates[0], dates[-1]))

    # ---- 预计算每只票的档位序列（date → tier） ----
    print('预计算 %d 只票的日K档位…' % len(bars_all))
    tier = {}
    close_of = {}
    for code, bs in bars_all.items():
        if len(bs) < HIST_MIN + 5:
            continue
        c = [float(b[2]) for b in bs]
        ts = tier_series(c)
        m = {}
        cm = {}
        for b, t in zip(bs, ts):
            if t is not None:
                m[b[0]] = t
                cm[b[0]] = float(b[2])
        tier[code] = m
        close_of[code] = cm
    print('  可用 %d 只' % len(tier))

    # ---- 逐日横截面 ----
    # acc[scene][g] = {'ex': [], 'up': []}（每天一个均值）
    acc = defaultdict(lambda: defaultdict(lambda: {'ex': [], 'up': []}))
    day_ex = defaultdict(lambda: defaultdict(list))   # scene -> g -> [每日均值]
    day_up = defaultdict(lambda: defaultdict(list))
    cnt = defaultdict(int)

    for di, d in enumerate(dates):
        if d not in mkt:
            continue
        mr = mkt[d]
        scene = 'down' if mr <= DOWN_TH else ('up' if mr >= UP_TH else 'flat')
        tmp_ex = defaultdict(list)
        tmp_up = defaultdict(list)
        for code, tm in tier.items():
            cm = close_of[code]
            if d not in tm or d not in cm:
                continue
            prev_d = None
            # 找 d 的前一个交易日（用该票自己的序列顺序不可靠，用 index 日期列表）
            if di > 0:
                prev_d = dates[di - 1]
            if prev_d is None or prev_d not in tm or prev_d not in cm:
                continue
            g = tm[prev_d]                     # ★ T-1 档位，无未来函数
            r = cm[d] / cm[prev_d] - 1
            if abs(r) > ABS_RET_MAX:
                continue
            tmp_ex[g].append(r - mr)
            tmp_up[g].append(1.0 if r > 0 else 0.0)
            cnt[(scene, g)] += 1
        for g in (0, 1, 3):
            if tmp_ex[g]:
                day_ex[scene][g].append(float(np.mean(tmp_ex[g])))
                day_up[scene][g].append(float(np.mean(tmp_up[g])))
    print()

    GN = {3: 'G1 ✦/↑（在涨）', 1: 'G2 →/✦仍弱', 0: 'G3 ↓下坡'}
    print('=' * 92)
    print('日K档位 → 次日（当日）超额收益 / 红盘率　【因子用 T-1 数据，严格无未来函数】')
    print('=' * 92)
    for scene in ('down', 'up', 'flat', 'all'):
        if scene == 'all':
            ex = defaultdict(list)
            up = defaultdict(list)
            for s in ('down', 'up', 'flat'):
                for g in (0, 1, 3):
                    ex[g] += day_ex[s][g]
                    up[g] += day_up[s][g]
        else:
            ex, up = day_ex[scene], day_up[scene]
        nd = len(ex.get(3, []))
        print('\n【%s】%d 个交易日' % (
            {'down': '跌日', 'up': '涨日', 'flat': '平淡日', 'all': '全样本'}[scene], nd))
        print('  %-16s %10s %8s %10s %8s %8s' % ('档位', '超额%/天', 't(聚类)', '红盘率', 'vs G3', '样本'))
        base_up = None
        for g in (3, 1, 0):
            if not ex[g]:
                continue
            st = cluster_t(ex[g])
            mu = float(np.mean(up[g]))
            if g == 0:
                base_up = mu
            print('  %-16s %10.4f %8.2f %9.2f%% %8s %8d'
                  % (GN[g], st['mean'] * 100, st['t'], mu * 100,
                     '-', cnt[(scene, g)] if scene != 'all' else sum(
                         cnt[(s, g)] for s in ('down', 'up', 'flat'))))
        # 组间差（G1 - G3），按交易日逐日配对
        if ex[3] and ex[0]:
            n = min(len(ex[3]), len(ex[0]))
            diff = [ex[3][i] - ex[0][i] for i in range(n)]
            du = [up[3][i] - up[0][i] for i in range(n)]
            s1, s2 = cluster_t(diff), cluster_t(du)
            print('  %-16s %10.4f %8.2f %9.2fpp %8s'
                  % ('★ G1 − G3', s1['mean'] * 100, s1['t'], s2['mean'] * 100, '(配对日 %d)' % n))
    print()
    print('口径：超额 = 个股当日收益 − 上证当日收益；红盘率 = 收益>0 的比例；'
          't 为按交易日聚类的 t 值（|t|>2 才谈得上显著）')
    print('⚠️ 数据截至 2026-10-30（未复权），只用于因子检验，不用于当日决策')


if __name__ == '__main__':
    main()
