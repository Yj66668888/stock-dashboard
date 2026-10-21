#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
K 区间平移扫描 + 资金阈值扫描 —— 回答用户 10/21 的新要求：
  「K 值放宽到 10-45，资金回流改为 5-10 亿」

复用 backtest_kdj30_40_recall.py 的同一套数据口径（30分K 640根重放 KDJ9,3,3
+ 东财沪市单市场日线），保证与已有回测结论可比。

⚠️ 口径限制（必须注明）：
  分钟级资金流无历史 → 「日内回补 N 亿」用日线代理：
    Δ       = 当日主力净 − 前一日主力净
    recall5 = 当日主力净 − 近5日谷值
  日线量级约为盘中同口径的 12 倍，绝对额不能直接跨尺度套；
  但本脚本只做「同一代理口径下的相对比较」，结论成立。
"""
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

from collections import OrderedDict  # noqa: E402

from backtest_kdj30_40_recall import fetch_m30, replay_kdj, load_fund, stats  # noqa: E402


def build_days():
    """组装逐日特征（K 收盘/盘中极值 + 资金 + 未来 1/3/5 日收益）"""
    kdj = replay_kdj(fetch_m30())
    bydate = OrderedDict()
    for x in kdj:
        bydate.setdefault(x['date'], []).append(x)
    daily_k = {}
    for dt, xs in bydate.items():
        xs = sorted(xs, key=lambda z: z['bar'])
        daily_k[dt] = {'k_close': xs[-1]['k'], 'd_close': xs[-1]['d'],
                       'k_min': min(z['k'] for z in xs),
                       'k_max': max(z['k'] for z in xs)}
    days = load_fund()
    for d in days:
        k = daily_k.get(d['date'])
        if k:
            d.update(k)
    days = [d for d in days if 'k_close' in d]
    base = {k: stats(days, k) for k in ('r1', 'r3', 'r5')}
    return days, base


def show(title, s, base):
    n1, a1, w1, _, _ = stats(s, 'r1')
    n3, a3, w3, _, _ = stats(s, 'r3')
    n5, a5, w5, _, _ = stats(s, 'r5')
    if n1 == 0:
        print(f'{title:30s} 无样本')
        return
    e1 = a1 - base['r1'][1]
    e5 = (a5 - base['r5'][1]) if a5 is not None else None
    tail = f'{a5:+6.2f}% 胜{w5:5.1f}%' if a5 is not None else '       —       '
    et = f'{e5:+6.2f}%' if e5 is not None else '   —   '
    print(f'{title:30s} {n1:3d} | {a1:+6.2f}% 胜{w1:5.1f}% ({e1:+5.2f}) | '
          f'{a3:+6.2f}% 胜{w3:5.1f}% | {tail} {et}')


def main():
    days, base = build_days()
    N = len(days)
    print(f'可回测交易日 {N} 天（{days[0]["date"]} ~ {days[-1]["date"]}），'
          f'区间涨跌 {(days[-1]["close"]/days[0]["close"]-1)*100:+.2f}%')
    print(f'基准：次日 {base["r1"][1]:+.2f}% / 胜率 {base["r1"][2]:.1f}%   '
          f'3日 {base["r3"][1]:+.2f}%   5日 {base["r5"][1]:+.2f}% / 胜率 {base["r5"][2]:.1f}%\n')
    hdr = (f"{'收盘30分K 区间':30s} {'样本':>3s} | {'次日(超额)':>20s} | "
           f"{'3日':>16s} | {'5日(超额)':>24s}")
    line = '=' * 108

    print(line)
    print('【A】候选区间平移扫描（收盘 K 口径，不加资金条件）—— 纯看 K 位置本身')
    print(line)
    print(hdr)
    combos = [
        ('现用 K 20~40', 20, 40),
        ('用户新提 K 10~45 ★', 10, 45),
        ('K 10~40', 10, 40),
        ('K 15~45', 15, 45),
        ('K 15~40', 15, 40),
        ('K 10~50', 10, 50),
        ('K 20~45', 20, 45),
        ('K 30~45', 30, 45),
        ('对照 K 30~40（原 v2）', 30, 40),
    ]
    for label, lo, hi in combos:
        s = [d for d in days if lo <= d['k_close'] < hi]
        show(label, s, base)

    print('\n' + line)
    print('【B】拆解 K10~45 由哪些部分拼成 —— 谁在贡献、谁在拖后腿')
    print(line)
    print(hdr)
    for label, lo, hi in [('K 10~20（深度超卖）', 10, 20), ('K 20~30', 20, 30),
                          ('K 30~40', 30, 40), ('K 40~45', 40, 45)]:
        s = [d for d in days if lo <= d['k_close'] < hi]
        show(label, s, base)
    print('-' * 108)
    s = [d for d in days if 10 <= d['k_close'] < 45]
    show('合计 K 10~45', s, base)

    print('\n' + line)
    print('【C】在区间 K10~45 内，资金阈值扫描（Δ = 日环比改善，代理口径）')
    print(line)
    print(hdr)
    inr = [d for d in days if 10 <= d['k_close'] < 45]
    show('K10~45 不加资金条件', inr, base)
    for th in (0, 5, 8, 10, 15, 20, 30):
        s = [d for d in inr if d['delta'] is not None and d['delta'] >= th]
        show(f'K10~45 ∧ Δ≥{th}亿', s, base)
    s = [d for d in inr if d['delta'] is not None and d['delta'] > 0]
    show('K10~45 ∧ Δ>0（资金转正）', s, base)

    print('\n' + line)
    print('【D】对照：老区间 K20~40 内同样扫一遍资金阈值')
    print(line)
    print(hdr)
    inr2 = [d for d in days if 20 <= d['k_close'] < 40]
    show('K20~40 不加资金条件', inr2, base)
    for th in (0, 5, 10, 20):
        s = [d for d in inr2 if d['delta'] is not None and d['delta'] >= th]
        show(f'K20~40 ∧ Δ≥{th}亿', s, base)

    print('\n' + line)
    print('【E】触发频率与额度核算（按交易日估算）')
    print(line)
    for label, lo, hi, th in [('K20~40 ∧ Δ≥10亿（现用）', 20, 40, 10),
                              ('K10~45 ∧ Δ≥5亿（新提）', 10, 45, 5),
                              ('K10~45 ∧ Δ≥10亿', 10, 45, 10),
                              ('K10~45 ∧ Δ≥0亿', 10, 45, 0)]:
        s = [d for d in days if lo <= d['k_close'] < hi
             and d['delta'] is not None and d['delta'] >= th]
        n1, a1, w1, _, _ = stats(s, 'r1')
        print(f'{label:26s} 触发 {len(s):3d}/{N} = {len(s)/N*100:5.1f}% 的天'
              f'（约每 {N/max(len(s),1):.1f} 个交易日一次）'
              f'｜有效样本 {n1}，次日胜 {w1:.1f}%')

    print('\n' + line)
    print('【F】K10~45 ∧ Δ≥5亿 逐笔明细')
    print(line)
    s = [d for d in inr if d['delta'] is not None and d['delta'] >= 5]
    print(f"{'日期':12s} {'K收盘':>7s} {'D':>6s} {'Δ(亿)':>9s} {'次日%':>8s} {'3日%':>8s} {'5日%':>8s}")
    for d in sorted(s, key=lambda x: x['date']):
        r = lambda v: f'{v:+7.2f}' if v is not None else '     —'
        print(f"{d['date']:12s} {d['k_close']:7.1f} {d['d_close']:6.1f} "
              f"{d['delta']:+9.2f} {r(d['r1'])} {r(d['r3'])} {r(d['r5'])}")


if __name__ == '__main__':
    main()
