# -*- coding: utf-8 -*-
"""
日K趋势模块（2026-11-02 建立）
================================================================================
用户诉求：推的票日K很多在走下坡 —— 30 分钟 MACD 不变，但双名单要优先挑
「日线在上涨」或「日线刚金叉」的票。

★ 为什么单独建模块
--------------------------------------------------------------------------------
· 双名单原来只按「活跃度 ACT」排序取两端：跌日取低端（冷门低弹性）→ 结果长期阴跌票扎堆。
· 活跃度轴回答的是「大盘涨/跌时哪类票弹性大」，它**不管方向**；
  日K趋势轴回答的是「这只票本身在往上还是往下」，两者正交，必须叠加。
· 与 30 分钟 MACD（仪表盘那列）解耦：这里只用**日线**，不动 30 分钟口径。

★ 判定：趋势打分 + 金叉（只用「截至最新一根」的数据，无未来函数）
--------------------------------------------------------------------------------
  base 趋势分（0~4，各 1 分）：
     收盘 > MA20 ｜ MA10 上行 ｜ 近5日涨幅 > 0 ｜ 近10日涨幅 > 0
  gold 金叉：近 GOLD_WIN 根内 DIF 上穿 DEA，或 MACD 柱由负转正 ≤ HIST_TURN_WIN 根

  ✦刚金叉    rank=3   gold 且 base >= 2   ← 真底部启动（不是深水反抽）
  ↑上涨      rank=3   base >= 3
  →横盘      rank=1   base == 2
  ✦金叉·仍弱 rank=1   gold 且 base <= 1   ← 下跌途中的反抽柱翻正，❌不算数
  ↓下坡      rank=0   base <= 1 且无金叉

  ok = (rank >= 2)   ←「上涨或刚金叉」，双名单优先层的准入

⚠️ 2026-11-02 实测血泪：第一版只看「柱翻正 ≤2 根」就把金叉放进去，
   结果票池里 8 只「金叉」有 6 只 5 日涨幅 −6%~−16%（DIF 仍在深水的下跌反抽）。
   → 金叉必须叠加趋势分（base>=2），否则「刚金叉」全是下跌中继。

⚠️ 与既有回测结论的冲突（必须知道，别装作没看见）
--------------------------------------------------------------------------------
既有归因（2025-10~2026-10，T+1 抄底样本）显示：
  · MACD 有效的是**水位**不是形态：DIF<0 组 +0.39%/天，DIF>0 组 −1.56%/天
  · 位置 P≥75 一票否决；「创新低」在 T+1 是减分项
→ 也就是说在「水下抄底」那批样本里，日线金叉/水上反而是**减分**的。
  但那批样本本身就是 DIF<0 缩量票，和本模块要挑的「已经在涨/刚启动」不是同一类。
  两套逻辑服务的是不同场景，本模块只做**优先层排序 + 显式标注**，不做硬门槛否决
  （硬门槛会与用户「买不买看我自己」的长期约束冲突，也回避开上面的口径冲突）。

⚠️ 盘中跑 ≠ 收盘跑：今日 bar 是快照补的，MA/ret5 会随当日价格变动。
   决策一律以收盘定稿为准。
================================================================================
"""
import math

MA_FAST, MA_MID, MA_SLOW = 5, 10, 20
RET_WIN = 5             # ★ 近几日涨幅（默认 5 日）
GOLD_WIN = 5            # ★ 几根之内算「刚」金叉
HIST_TURN_WIN = 2       # ★ MACD 柱由负转正几根之内算「刚」


def ema(vals, n):
    k = 2.0 / (n + 1)
    out, e = [], vals[0]
    for v in vals:
        e = v * k + e * (1 - k)
        out.append(e)
    return out


def macd_line(c):
    """与 scan_macd.macd_line 同口径（12,26,9）"""
    ef, es = ema(c, 12), ema(c, 26)
    dif = [a - b for a, b in zip(ef, es)]
    dea = ema(dif, 9)
    return dif, dea, [a - b for a, b in zip(dif, dea)]


def _ma(a, n):
    return sum(a[-n:]) / n if len(a) >= n else None


def calc(bars, ret_win=RET_WIN, gold_win=GOLD_WIN):
    """bars: [(date, open, close, high, low, vol)]，末根为最新（可为盘中快照）

    返回 dict（数据不足返回 None）：
      state  'gold' / 'up' / 'flat' / 'gold_weak' / 'down'
      label  中文短标（✦刚金叉 / ↑上涨 / →横盘 / ✦金叉·仍弱 / ↓下坡）
      rank   3 / 2 / 1 / 0
      base   趋势分 0~4
      ok     True = 属于「上涨或刚金叉」优先层
      ret5 / ret10  近 5 / 10 日涨幅（%）
      ma10 / ma20 / gold_ago / turn_ago / dif / dea / hist
    """
    if not bars or len(bars) < MA_SLOW + 5:
        return None
    try:
        c = [float(b[2]) for b in bars]
    except Exception:
        return None
    n = len(c)

    ma5 = _ma(c, MA_FAST)
    ma10 = _ma(c, MA_MID)
    ma20 = _ma(c, MA_SLOW)
    # 「前一根」的 MA10：判断均线方向（上行 / 下行）
    ma10_prev = sum(c[-MA_MID - 1:-1]) / MA_MID if n > MA_MID else None
    ret5 = (c[-1] / c[-1 - ret_win] - 1) * 100 if n > ret_win and c[-1 - ret_win] > 0 else None
    ret10 = (c[-1] / c[-11] - 1) * 100 if n > 10 and c[-11] > 0 else None

    dif, dea, hist = macd_line(c)
    i = n - 1

    # ---- 金叉：近 gold_win 根内 DIF 上穿 DEA（距今几根） ----
    gold_ago = None
    lo = max(1, i - gold_win)
    for j in range(i, lo - 1, -1):
        if dif[j] > dea[j] and dif[j - 1] <= dea[j - 1]:
            gold_ago = i - j
            break

    # ---- MACD 柱由负转正（更早的启动信号） ----
    turn_ago = None
    for j in range(i, max(0, i - HIST_TURN_WIN) - 1, -1):
        if hist[j] > 0 and hist[j - 1] <= 0:
            turn_ago = i - j
            break

    # ---- 趋势分 base（0~4）----
    base = 0
    if ma20 and c[i] > ma20:
        base += 1
    if ma10 is not None and ma10_prev is not None and ma10 > ma10_prev:
        base += 1
    if ret5 is not None and ret5 > 0:
        base += 1
    if ret10 is not None and ret10 > 0:
        base += 1

    is_gold = (gold_ago is not None and gold_ago <= gold_win) or \
              (turn_ago is not None and turn_ago <= HIST_TURN_WIN)

    if is_gold and base >= 2:
        state, label, rank = 'gold', '✦刚金叉', 3
    elif base >= 3:
        state, label, rank = 'up', '↑上涨', 3
    elif base == 2:
        state, label, rank = 'flat', '→横盘', 1
    elif is_gold:
        # 金叉但趋势分 ≤1：多半是深水下跌途中的柱翻正（下跌中继），不算「在涨」
        state, label, rank = 'gold_weak', '✦金叉·仍弱', 1
    else:
        state, label, rank = 'down', '↓下坡', 0

    return {
        'state': state, 'label': label, 'rank': rank, 'ok': rank >= 2, 'base': base,
        'ret5': (round(ret5, 2) if ret5 is not None else None),
        'ret10': (round(ret10, 2) if ret10 is not None else None),
        'ma5': (round(ma5, 2) if ma5 is not None else None),
        'ma10': (round(ma10, 2) if ma10 is not None else None),
        'ma20': (round(ma20, 2) if ma20 is not None else None),
        'gold_ago': gold_ago, 'turn_ago': turn_ago,
        'dif': round(dif[i], 3), 'dea': round(dea[i], 3), 'hist': round(hist[i], 3),
        'above_ma20': (c[i] > ma20) if ma20 else None,
        'water': ('水下' if dif[i] < 0 else '水上'),
    }


def short(d):
    """仪表盘/终端用的紧凑串：'✦刚金叉(2根前) +3.1%'"""
    if not d:
        return '-'
    extra = []
    if d.get('gold_ago') is not None:
        extra.append('金叉%d根前' % d['gold_ago'])
    elif d.get('turn_ago') is not None:
        extra.append('柱翻正%d根前' % d['turn_ago'])
    if d.get('ret5') is not None:
        extra.append('%+.1f%%(5日)' % d['ret5'])
    return d['label'] + (('·' + '·'.join(extra)) if extra else '')


if __name__ == '__main__':
    import sys
    sys.path.insert(0, '.')
    from scan_newlow import fetch_day
    for code in (sys.argv[1:] or ['sh600105']):
        bs = fetch_day(code, 200)
        if not bs:
            print(code, '取数失败')
            continue
        d = calc(bs)
        print(code, short(d), d)
