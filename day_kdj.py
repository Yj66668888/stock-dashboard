#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
day_kdj.py —— 日线 KDJ 状态判定（2026-11-05，仪表盘出票 KDJ 准入门）

★ 需求（用户拍板 2026-11-05）
  仪表盘出票时，日K KDJ 或 60分钟 KDJ 必须处于「刚启动 / 上升状态」，
  且 K 值 ≤ 60。与 h60_kdj.py 同一套状态机，只是数据换成日线。

数据输入：scan_macd 实时拉的日线 bars（[date, open, close, high, low, vol] 元组列表），
  零额外请求（复用已有数据，与 DT.calc / GQ.calc 同一来源）。

★ KDJ(9,3,3) 交付口径（与 h60_kdj.py / 仪表盘一致）
  RSV = (C - LLV9) / (HHV9 - LLV9) * 100
  K = 2/3 * prevK + 1/3 * RSV     （初值 50）
  D = 2/3 * prevD + 1/3 * K       （初值 50）
  J = 3K - 2D

⚠️ 口径声明（2026-11-06审计B4）：本模块 RSV 的 HHV/LLV 取「收盘价」的 9 期高低
  （非通达信标准的 HIGH/LOW）。仪表盘展示列的 K 值（scan_macd.kdj_k）是
  HIGH/LOW 标准口径，两者数值略有差异；KDJ 准入门的 K≤60 判定以本模块为准
  （K≤60 的切片验证 validate_h60_kdj_k60.py 即基于本口径，勿混改）。

★ 五档（与 h60_kdj.py 完全同构，无未来函数：只用截至最新一根）
  ✦刚金叉 rank=3 / ↑上行 rank=2 / ⚡将金叉 rank=2 / ↘高位回落 rank=0 / ↓下行 rank=0
  ok = rank >= 2

⚠️ 实测提醒（诚实记录，别装看不见）
  60分KDJ 准入组在 validate_h60_kdj.py（300只×69天）里方向与直觉相反且不显著；
  「日K KDJ 或 60分 KDJ 二选一」+「K≤60」是新的未验证切片（验证中）。
  本模块按用户拍板实现为硬门槛，K≤60 上限是防追高保护；开关在 scan_macd.KDJ_GATE。

统计归因，不构成投资建议。
"""

K_N = 9
GOLD_WIN = 3            # 最近几根内金叉算「刚金叉」
NEAR_TH = 3.0           # D−K 差值小于此且收窄才算「将金叉」
MIN_BARS = 40           # 少于这个根数不判定（≈2 个月日线）


def kdj(closes, n=K_N):
    """日线收盘序列 -> (K序列, D序列)。数据不足返回 ([], [])。"""
    c = closes
    if len(c) < n + 5:
        return [], []
    K, D, pk, pd = [], [], 50.0, 50.0
    for i in range(len(c)):
        lo = min(c[max(0, i - n + 1): i + 1])
        hi = max(c[max(0, i - n + 1): i + 1])
        rsv = 50.0 if hi == lo else (c[i] - lo) / (hi - lo) * 100.0
        pk = 2.0 / 3.0 * pk + 1.0 / 3.0 * rsv
        pd = 2.0 / 3.0 * pd + 1.0 / 3.0 * pk
        K.append(pk)
        D.append(pd)
    return K, D


def calc(bs):
    """bs: [(date, open, close, high, low, vol), ...] 末根为最新（可为盘中快照）。
    返回 dict / None（数据不足）。"""
    if not bs or len(bs) < MIN_BARS:
        return None
    try:
        c = [float(b[2]) for b in bs]
    except Exception:
        return None
    K, D = kdj(c)
    if not K:
        return None
    i = len(K) - 1
    k0, k1, d0, d1 = K[i], K[i - 1], D[i], D[i - 1]
    gap = k0 - d0
    gap_prev = k1 - d1

    gold_ago = None
    for j in range(i, max(i - GOLD_WIN, 0) - 1, -1):
        if K[j] > D[j] and K[j - 1] <= D[j - 1]:
            gold_ago = i - j
            break

    if gold_ago is not None:
        state, label, rank = 'gold', '✦刚金叉', 3
    elif k0 > d0 and d0 > d1:
        state, label, rank = 'up', '↑上行', 2
    elif k0 < d0 and gap > gap_prev and k0 > k1 and (-gap) <= NEAR_TH:
        state, label, rank = 'near', '⚡将金叉', 2
    elif k0 > d0:
        state, label, rank = 'fade', '↘高位回落', 0
    else:
        state, label, rank = 'down', '↓下行', 0

    return {
        'state': state, 'label': label, 'rank': rank, 'ok': rank >= 2,
        'K': round(k0, 1), 'D': round(d0, 1), 'J': round(3 * k0 - 2 * d0, 1),
        'gap': round(gap, 2), 'gold_ago': gold_ago, 'n': len(K),
    }


def short(st):
    """紧凑串（表格用）：'↑K42' / '✦K38' / '—'"""
    if not st:
        return '—'
    ch = {'gold': '✦', 'up': '↑', 'near': '⚡', 'fade': '↘', 'down': '↓'}.get(st['state'], '—')
    return '%sK%.0f' % (ch, st['K'])


def txt(st):
    if not st:
        return '无数据'
    base = '%s K%.1f D%.1f' % (st['label'], st['K'], st['D'])
    if st.get('gold_ago') is not None:
        base += '（%d根前金叉）' % st['gold_ago']
    return base


if __name__ == '__main__':
    import sys
    sys.path.insert(0, '.')
    from scan_newlow import fetch_day
    for code in (sys.argv[1:] or ['sh600105']):
        bs = fetch_day(code, 120)
        st = calc(bs)
        print('%-10s %s' % (code, txt(st) if st else '（数据不足）'))
