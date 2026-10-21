#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
短线信号横向选型 —— 回答用户 10/21「现在玩的基本上都是短线，推荐用哪个比较好」

把所有在系统上跑过/测过的信号口径，放在**同一份数据（7-10月 74 个交易日）、
同一套度量（次日/3日/5日收盘收益）**下横向排名。

口径（与 backtest_kdj30_40_recall.py 完全一致，保证可比）
--------------------------------------------------------
K 值   ：腾讯 mkline sh000001 m30，640 根重放 KDJ(9,3,3)，K/D 初值 50
         取每日**收盘 bar** 的 K/D（历史证明收盘口径远强于盘中触及口径）
资金   ：东财沪市单市场日线 market_fund_flow_daily.csv
         Δ       = 当日主力净 − 前一日主力净
         recall5 = 当日主力净 − 近 5 日谷值
         ratio   = recall5 / |近5日谷值|
收益   ：信号日 d 收盘确认 → d+N 日收盘

⚠️ 限制：分钟级资金流无历史，日内「回补 N 亿」只能用日线代理；
        日线 recall5 中位数 233 亿，故「≥5 亿 / ≥10 亿」在日线尺度无区分度，
        本脚本同时列出纯 K 位置口径，避免把"资金门槛"的功劳算错。
"""
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

from backtest_kdj30_40_recall import (  # noqa: E402
    fetch_m30, replay_kdj, load_fund,
)


def build():
    kdj = replay_kdj(fetch_m30())
    bydate = {}
    for x in kdj:
        bydate.setdefault(x['date'], []).append(x)
    daily = {}
    for dt, xs in bydate.items():
        xs.sort(key=lambda z: z['bar'])
        last = xs[-1]
        daily[dt] = {'k': last['k'], 'd': last['d'],
                     'j': 3 * last['k'] - 2 * last['d'],   # 原脚本只返回 K/D，J 现算
                     'k_min': min(z['k'] for z in xs),
                     'k_max': max(z['k'] for z in xs),
                     'prev_k': xs[-2]['k'] if len(xs) > 1 else last['k']}
    days = load_fund()
    for x in days:
        x.update(daily.get(x['date'], {}))
    return [x for x in days if 'k' in x]


def st(s, key):
    v = [x[key] for x in s if x.get(key) is not None]
    if not v:
        return 0, 0.0, 0.0, 0.0, 0.0
    wins = [x for x in v if x > 0]
    losses = [x for x in v if x <= 0]
    return (len(v), sum(v) / len(v),
            len(wins) / len(v) * 100,
            sum(losses) / len(losses) if losses else 0.0,
            max(losses) if losses else 0.0)   # 平均亏损 / 最大单笔亏损


def evaluate(days, signals, base):
    rows = []
    for name, fn in signals:
        sel = [x for x in days if fn(x)]
        if not sel:
            rows.append({'name': name, 'n': 0})
            continue
        n1, a1, w1, al1, ml1 = st(sel, 'r1')
        n3, a3, w3, _, _ = st(sel, 'r3')
        n5, a5, w5, _, _ = st(sel, 'r5')
        rows.append({
            'name': name, 'n': len(sel),
            'freq': len(sel) / len(days) * 100,
            'r1': a1, 'w1': w1, 'e1': a1 - base['r1'],
            'avg_loss1': al1, 'max_loss1': ml1,
            'r3': a3, 'w3': w3, 'e3': a3 - base['r3'],
            'r5': a5, 'w5': w5, 'e5': a5 - base['r5'],
        })
    return rows


def main():
    days = build()
    n_all = len(days)
    base = {'r1': st(days, 'r1')[1], 'r3': st(days, 'r3')[1], 'r5': st(days, 'r5')[1]}
    b1, b3, b5 = base['r1'], base['r3'], base['r5']
    bw1, bw3, bw5 = st(days, 'r1')[2], st(days, 'r3')[2], st(days, 'r5')[2]

    print('=' * 128)
    print(f'短线信号横向选型｜样本 {n_all} 天（{days[0]["date"]} ~ {days[-1]["date"]}）｜沪指同期单边下行')
    print(f'基准：次日 {b1:+.2f}% / 胜 {bw1:.1f}%　3日 {b3:+.2f}% / 胜 {bw3:.1f}%　5日 {b5:+.2f}% / 胜 {bw5:.1f}%')
    print('=' * 128)

    K = lambda lo, hi: (lambda x, lo=lo, hi=hi: lo <= x['k'] < hi)  # noqa: E731
    signals = [
        ('① 资金转正 net>0',            lambda x: x['net'] > 0),
        ('② 日环比改善 Δ≥0',            lambda x: x['delta'] is not None and x['delta'] >= 0),
        ('③ recall5 比例≥40%',          lambda x: x['ratio'] >= 0.40),
        ('④ K∈[10,25) 深超卖',          K(10, 25)),
        ('⑤ K∈[10,40)',                 K(10, 40)),
        ('⑥ K∈[10,45) 现网 v2.2',        K(10, 45)),
        ('⑦ K∈[20,40) v2.1',            K(20, 40)),
        ('⑧ K∈[30,40) 你最初的档',       K(30, 40)),
        ('⑨ K<25 真到位大盘版',          lambda x: x['k'] < 25),
        ('⑩ K≥70 且 K>D 高位强势',       lambda x: x['k'] >= 70 and x['k'] > x['d']),
        ('⑪ K∈[10,45) ∧ net>0',         lambda x: 10 <= x['k'] < 45 and x['net'] > 0),
        ('⑫ K∈[20,40) ∧ net>0',         lambda x: 20 <= x['k'] < 40 and x['net'] > 0),
        ('⑬ K∈[10,45) ∧ Δ≥0',           lambda x: 10 <= x['k'] < 45 and x['delta'] is not None and x['delta'] >= 0),
        ('⑭ K<25 ∧ Δ≥0',                lambda x: x['k'] < 25 and x['delta'] is not None and x['delta'] >= 0),
        ('⑮ K∈[10,45) ∧ Δ≥0 ∧ 比例≥40%', lambda x: 10 <= x['k'] < 45 and x['delta'] is not None
                                                  and x['delta'] >= 0 and x['ratio'] >= 0.40),
        # —— 第二轮：把「稳位置」×「资金确认」交叉，找最优组合 ——
        ('⑯ K∈[20,40) ∧ Δ≥0',           lambda x: 20 <= x['k'] < 40 and x['delta'] is not None and x['delta'] >= 0),
        ('⑰ K∈[20,40) ∧ 比例≥40%',        lambda x: 20 <= x['k'] < 40 and x['ratio'] >= 0.40),
        ('⑱ K∈[20,45) ∧ Δ≥0',           lambda x: 20 <= x['k'] < 45 and x['delta'] is not None and x['delta'] >= 0),
        ('⑲ K∈[25,42) ',                K(25, 42)),
        ('⑳ K∈[25,42) ∧ Δ≥0',           lambda x: 25 <= x['k'] < 42 and x['delta'] is not None and x['delta'] >= 0),
        ('㉑ K∈[20,40) ∧ K>D',           lambda x: 20 <= x['k'] < 40 and x['k'] > x['d']),
        ('㉒ K∈[20,40) ∧ 拐头(K>prevK)',   lambda x: 20 <= x['k'] < 40 and x['k'] > x['prev_k']),
    ]

    rows = evaluate(days, signals, base)
    print(f'{"信号":30s} {"样本":>4s} {"频率":>6s} | {"次日%":>7s} {"胜率":>6s} {"超额":>7s} '
          f'{"均亏":>7s} {"最大亏":>7s} | {"3日%":>7s} {"胜率":>6s} | {"5日%":>7s} {"胜率":>6s}')
    print('-' * 128)
    for r in rows:
        if r['n'] == 0:
            print(f'{r["name"]:30s}     0   无样本')
            continue
        print(f'{r["name"]:30s} {r["n"]:4d} {r["freq"]:5.1f}% | '
              f'{r["r1"]:+7.2f} {r["w1"]:5.1f}% {r["e1"]:+7.2f} '
              f'{r["avg_loss1"]:+7.2f} {r["max_loss1"]:+7.2f} | '
              f'{r["r3"]:+7.2f} {r["w3"]:5.1f}% | {r["r5"]:+7.2f} {r["w5"]:5.1f}%')
    print('-' * 128)

    # 短线排名：只看次日超额，样本 ≥ 8 才进榜
    print('\n【短线推荐榜】判定 = 次日超额收益（样本≥8），并附最大单笔亏损做风控参考')
    cand = sorted([r for r in rows if r['n'] >= 8], key=lambda z: -z['e1'])
    print(f'{"排名":4s} {"信号":30s} {"次日超额":>9s} {"次日胜率":>8s} {"触发频率":>8s} {"几天一次":>8s} {"最大单笔亏":>10s}')
    for i, r in enumerate(cand, 1):
        gap = f'{100 / r["freq"]:.1f}' if r['freq'] else '—'
        print(f'{i:<4d} {r["name"]:30s} {r["e1"]:+9.2f} {r["w1"]:8.1f}% '
              f'{r["freq"]:7.1f}% {gap:>8s} {r["max_loss1"]:+10.2f}')

    # 小样本但值得看的
    print('\n【样本不足 8 但方向值得注意（仅供参考，不足以定策略）】')
    for r in sorted([r for r in rows if 0 < r['n'] < 8], key=lambda z: -z['e1']):
        print(f'  {r["name"]:30s} n={r["n"]:2d} 次日 {r["r1"]:+.2f}% 胜 {r["w1"]:.1f}% '
              f'超额 {r["e1"]:+.2f}｜3日 {r["r3"]:+.2f}%｜5日 {r["r5"]:+.2f}%')

    return days, rows, base


if __name__ == '__main__':
    main()
