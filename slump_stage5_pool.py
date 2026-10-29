#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
slump_stage5_pool.py —— 用**用户自己的 40 只票池**重验「跌到位 vs 继续跌」的个股结论

================================================================================
为什么必须补这一步（2026-10-28 用户提出的最大疑点）
================================================================================
slump_stage4 的 Q3 用的是 **全市场 5020 只 30 分 bar 的等权基准**，那不是用户
实际会买的票。用户交易的是 docs/index.html 里那 40 只（每天由 dynamic selector
滚动刷新，波动明显高于全市场 —— 见 build_matrix_excel「K<20 时票池 −1.014% vs
全市场 −0.575%」）。β 更高 ⇒ 同一信号幅度被放大，**方向也可能不同**。

================================================================================
口径（尽量与 stage4 对齐，保证可比）
================================================================================
信号日期   ：.cache_slump_panel.npz（127 个交易日，30 分口径，决策点 1430）
个股收益   ：日线：t 日收盘买（≈14:50 尾盘买）→ t+1 日收盘卖（14:50 尾盘卖）
             与已确认的出场纪律一致（次日 14:50 尾盘卖，不加止损止盈）
组合口径   ：每天票池内等权求均值 → 再按交易日聚合（天然按日聚类，
             规避「同一天几十只同涨同跌」的伪独立）
可买性过滤 ：t 日涨幅 ≥ 9.8%（涨停）剔除 —— 买不进（本项目 10-28 血泪教训）
对照组     ：同一批日期、同一口径下的全市场 899 只，用来判断结论是「票池特有」
             还是「全市场共性」
成本       ：0.30% 往返（理想 0.15% / 5 万分 20 只 0.45%）

================================================================================
⚠️ 无法回避的偏差（读结论前必看）
================================================================================
1) 成分选择偏差：今天这 40 只是**今天**选出来的，回溯过去属反事实推演。
   它回答的是「假如我那阵子就持有今天这批票」，不是「这个信号的历史收益率」。
2) 未复权：腾讯 kline 不支持 qfq（返回空），小额除权会把个别票当天收益打歪。
3) 样本量：信号日 19~33 天，MDE 约 60~90bp/天，策略差距小于这个量级都不算数。
"""
import json
import os
import sys
import numpy as np
from math import sqrt, erf

BASE = os.path.dirname(os.path.abspath(__file__))
POOL_CACHE = os.path.join(BASE, '.cache_pool_daily.json')
MKT_CACHE = os.path.join(BASE, '_archive_20261028', 'cache', '.cache_kdaily_now.json')
COST = 0.0030
LIMIT_UP = 0.098
PINNED = ['603067', '600105', '601126', '000688']


def load_pool_codes():
    """从 docs/index.html 解析 const STOCKS=[...]（与 index_kdj_watch 同源）"""
    html = open(os.path.join(BASE, 'docs', 'index.html'), encoding='utf-8').read()
    i = html.find('const STOCKS')
    if i < 0:
        return []
    s = html.index('[', i)
    depth = 0
    for j in range(s, len(html)):
        if html[j] == '[':
            depth += 1
        elif html[j] == ']':
            depth -= 1
            if depth == 0:
                try:
                    arr = json.loads(html[s:j + 1])
                    return [str(x['code'])[2:] for x in arr if 'code' in x]
                except json.JSONDecodeError:
                    return []
    return []


def to_series(raw):
    return {c: {r[0]: float(r[2]) for r in rows} for c, rows in raw.items()}


def cross_ret(series_map, d_prev, d0, d1):
    """返回 d0→d1 的截面收益 dict；剔除 d_prev→d0 涨停(d0 买不进)的票"""
    out = {}
    for c, s in series_map.items():
        p0, p1 = s.get(d0), s.get(d1)
        if not p0 or p1 is None or p0 <= 0:
            continue
        if d_prev:
            pv = s.get(d_prev)
            if pv and pv > 0 and p0 / pv - 1.0 >= LIMIT_UP:
                continue                       # t 日涨停 → 买不进
        out[c] = p1 / p0 - 1.0
    return out


def welch(a, b):
    a = np.asarray(a, float); b = np.asarray(b, float)
    a = a[np.isfinite(a)]; b = b[np.isfinite(b)]
    if len(a) < 3 or len(b) < 3:
        return np.nan, np.nan, np.nan
    va, vb = np.var(a, ddof=1), np.var(b, ddof=1)
    se = sqrt(va / len(a) + vb / len(b))
    if se == 0:
        return np.nan, np.nan, np.nan
    diff = float(np.mean(a) - np.mean(b))
    t = diff / se
    if t == t:
        p = 2 * (1 - 0.5 * (1 + erf(abs(t) / sqrt(2))))
    else:
        p = np.nan
    return t, diff, p


def main():
    P = np.load(os.path.join(BASE, '.cache_slump_panel.npz'), allow_pickle=True)
    days = [str(d) for d in P['days']]
    iso = [f'{d[:4]}-{d[4:6]}-{d[6:]}' for d in days]
    K1430 = P['K1430'].astype(float)
    F = {k[2:]: P[k].astype(bool) for k in P.files if k.startswith('F_')}

    pool_raw = json.load(open(POOL_CACHE))
    mkt_raw = json.load(open(MKT_CACHE))
    codes = load_pool_codes()
    pool = to_series(pool_raw)
    mkt = to_series(mkt_raw)
    pinned = {c: mkt[c] for c in PINNED if c in mkt}

    print('=' * 106)
    print('slump_stage5 —— 用户自有票池（40 只）复盘，对比全市场 899 只')
    print('=' * 106)
    print('票池 %d 只，取到日线 %d 只（覆盖率 %.0f%%）｜全市场对照 %d 只｜PINNED 对照 %d 只'
          % (len(codes), len(pool), 100.0 * len(pool) / max(1, len(codes)),
             len(mkt), len(pinned)))

    not_broke = F['F6_假跌破收复']
    grp = [
        ('① K<30 且未破位（超卖信号）', (K1430 < 30) & not_broke),
        ('② K<30 且破位（风险组）', (K1430 < 30) & ~not_broke),
        ('③ K<40 且未破位（报告主信号）', (K1430 < 40) & not_broke),
        ('④ 全部破位日（不限 K）', ~not_broke),
        ('⑤ 其余交易日（基线）', (K1430 >= 40) & not_broke),
    ]
    BASE_KEY = '⑤ 其余交易日（基线）'
    SIG_KEYS = ('③ K<40 且未破位（报告主信号）',
                '① K<30 且未破位（超卖信号）',
                '④ 全部破位日（不限 K）')

    rows = {k: [] for k, _ in grp}
    rows['⑩ 全部交易日'] = []
    d_pool = np.full(len(days), np.nan)
    d_mkt = np.full(len(days), np.nan)
    d_pin = np.full(len(days), np.nan)

    for i in range(len(days) - 1):
        prev = iso[i - 1] if i >= 1 else None
        rp = cross_ret(pool, prev, iso[i], iso[i + 1])
        rm = cross_ret(mkt, prev, iso[i], iso[i + 1])
        rn = cross_ret(pinned, None, iso[i], iso[i + 1])
        if rp:
            d_pool[i] = float(np.mean(list(rp.values())))
        if rm:
            d_mkt[i] = float(np.mean(list(rm.values())))
        if rn:
            d_pin[i] = float(np.mean(list(rn.values())))
        if not np.isfinite(d_pool[i]):
            continue
        rec = (d_pool[i], d_mkt[i], d_pin[i], len(rp))
        for k, mask in grp:
            if mask[i]:
                rows[k].append(rec)
        rows['⑩ 全部交易日'].append(rec)

    print()
    print('-' * 106)
    print('%-30s %4s %10s %8s %11s %9s %10s %8s'
          % ('日期分组', '天数', '票池均值', '票池胜率', '全市场均值', '市场胜率',
             'PINNED均值', '池−市场'))
    print('-' * 106)
    summary = {}
    for k in [x[0] for x in grp] + ['⑩ 全部交易日']:
        v = rows[k]
        if not v:
            print('%-30s %4d   %s' % (k, 0, '无样本'))
            continue
        a = np.array([x[0] for x in v]) * 100
        b = np.array([x[1] for x in v], float) * 100
        c = np.array([x[2] for x in v], float) * 100
        s = dict(n=len(v), pool=float(np.mean(a)), win=float(np.mean(a > 0)),
                 mkt=float(np.nanmean(b)), win_mkt=float(np.nanmean(b > 0)),
                 pin=float(np.nanmean(c)),
                 diff=float(np.mean(a) - np.nanmean(b)))
        summary[k] = s
        print('%-30s %4d %+9.3f%% %7.1f%% %+10.3f%% %8.1f%% %+9.3f%% %+7.1fbp'
              % (k, s['n'], s['pool'], s['win'] * 100, s['mkt'], s['win_mkt'] * 100,
                 s['pin'], s['diff'] * 100))

    print()
    print('-' * 106)
    print('显著性：分组 vs 基线（Welch on 日均值，已按交易日聚类）')
    print('-' * 106)
    base_arr = np.array([x[0] for x in rows.get(BASE_KEY, [])], float)
    TESTS = []
    for k in SIG_KEYS:
        v = np.array([x[0] for x in rows.get(k, [])], float)
        t, d, p = welch(v, base_arr)
        TESTS.append(dict(lab=k, n=len(v), t=t, diff=d, p=p))
        if len(v) < 3 or len(base_arr) < 3:
            print('  %-30s 样本不足 n=%d' % (k, len(v)))
            continue
        flag = '✅ p<0.05' if p == p and p < 0.05 else '⚠️不显著'
        print('  %-30s n=%2d  差 %+7.1fbp  t=%+5.2f  p=%.3f  %s'
              % (k, len(v), d * 1e4, t, p, flag))

    print()
    print('-' * 106)
    print('账户口径（每天等权持有票池，非出手日空仓，往返成本 %.2f%%）' % (COST * 100))
    print('-' * 106)
    fin = np.isfinite(d_pool)

    ACCT_OUT = []

    def acct(mask, lab):
        m = mask & fin
        v = np.where(m, d_pool - COST, 0.0)
        eq = np.cumprod(1 + v)
        mdd = np.max(np.maximum.accumulate(eq) - eq) / np.max(eq) if len(eq) else 0.0
        pos = v[m]
        rtn_days = d_pool[m]
        ACCT_OUT.append(dict(lab=lab, n=int(m.sum()),
                             cum=float(np.prod(1 + v) - 1),
                             mean=float(np.mean(rtn_days)) if len(rtn_days) else 0.0,
                             mdd=float(mdd),
                             win=float(np.mean(pos > 0)) if len(pos) else 0.0))
        print('  %-26s 出手%3d天  累计%+7.2f%%  日均%+.3f%%  回撤%5.1f%%  扣费胜率%5.1f%%'
              % (lab, int(m.sum()), (np.prod(1 + v) - 1) * 100,
                 float(np.mean(rtn_days)) * 100 if len(rtn_days) else 0.0,
                 mdd * 100, float(np.mean(pos > 0)) * 100 if len(pos) else 0.0))

    acct(np.ones(len(d_pool), bool), '① 天天买')
    acct(not_broke, '② 排除全部破位日')
    acct((K1430 < 30) & not_broke, '③ 只买 K<30 + 未破位')
    acct((K1430 < 40) & not_broke, '④ 只买 K<40 + 未破位')

    # ---------------- 结论 ----------------
    print()
    print('=' * 106)
    print('★ 结论对照：自有票池 vs stage4 全市场口径')
    print('=' * 106)
    sig = summary.get('③ K<40 且未破位（报告主信号）')
    base = summary.get(BASE_KEY)
    brk = summary.get('④ 全部破位日（不限 K）')
    if sig and base:
        print('[主信号 K<40 且未破位]  n=%d 天' % sig['n'])
        print('   自有票池 %+.3f%%/天，胜率 %.1f%%；同批日期全市场 %+.3f%%/天，胜率 %.1f%%'
              % (sig['pool'], sig['win'] * 100, sig['mkt'], sig['win_mkt'] * 100))
        print('   自有池基线（其余交易日）%+.3f%% → 差 %+.1f bp'
              % (base['pool'], (sig['pool'] - base['pool']) * 100))
        print('   stage4 全市场 30 分口径的旧结论：信号日 −0.194% < 基线 +0.179%（信号无效）')
        print('   本次票池口径：%s' % ('信号日优于基线 ✅' if sig['pool'] > base['pool']
                                  else '信号日仍差于基线 ❌'))
    if brk and base:
        print('[破位日]  n=%d 天  票池 %+.3f%%/天 vs 基线 %+.3f%%/天 → %s'
              % (brk['n'], brk['pool'], base['pool'],
                 '破位确实是更差的出手日 ✅' if brk['pool'] < base['pool'] else '未支持 ❌'))
    print()
    print('⚠️ 三条偏差声明：')
    print('   1) 40 只取自今天的 index.html → 回溯有成分选择偏差，属反事实推演')
    print('   2) 腾讯日线不支持复权 → 小额除权会污染个别票的单日收益')
    print('   3) 信号日只有 %d 天，MDE≈60~90bp/天；差距小于这个量级的不算数'
          % (sig['n'] if sig else 0))

    # 落盘供 Excel 报告使用（不在报告里另算一遍，避免口径漂移）
    dump = dict(summary=summary, groups=list(summary.keys()),
                sig_keys=list(SIG_KEYS), base_key=BASE_KEY,
                tests=TESTS, acct=ACCT_OUT, meta=dict(
                    n_days=int(np.isfinite(d_pool).sum()),
                    d0=iso[0], d1=iso[-1],
                    pool_n=len(pool), mkt_n=len(mkt), pinned_n=len(pinned),
                    cost=COST, limit_up=LIMIT_UP))
    json.dump(dump, open(os.path.join(BASE, '.cache_slump_pool5.json'), 'w'),
              ensure_ascii=False)
    print('\n（已落盘 .cache_slump_pool5.json）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
