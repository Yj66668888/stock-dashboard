# -*- coding: utf-8 -*-
"""
活跃度/风险轴因子（2026-10-31 建立）
================================================================================
来源：近一年（2025-10-30 ~ 2026-10-30，3017 只 × 243 天）横截面归因。

★ 为什么只有这 4 个因子
--------------------------------------------------------------------------------
15 个候选因子里，同时满足「跌日/涨日两场景分位都单调 + 方向镜像」的只有 4 个：

  因子      跌日        涨日        跌日跨度   涨日跨度
  beta60    低值好↓     高值好↑     1.35%     1.18%
  vol20     低值好↓     高值好↑     1.39%     0.96%
  amt20     低值好↓     高值好↑     1.04%     0.73%
  newlow20  未创新低好  创新低好↑   0.21%     0.23%

其余 11 个（bias10/20/60、ret20/60/120/250、prev_ret、P250、volr、P60）
在至少一个场景里分位不单调（倒 U 或锯齿），已全部淘汰，不进打分。

★ 这 4 个本质上是同一根轴
--------------------------------------------------------------------------------
它们两两相关（与 beta60 的秩相关：vol20 0.41、amt20 0.38），
是「活跃度/风险」这一个维度的四个测度，不是四个独立 alpha。
所以合成一个 ACT 综合分，不分开当独立因子用。

  跌日 → 买这条轴的低端（冷门、低弹性、低波动、未创新低）→ 防御
  涨日 → 买这条轴的高端（活跃、高弹性、高波动、创新低）  → 进攻

★ 口径铁律
--------------------------------------------------------------------------------
回测脚本 analyze_contrarian.py 用 T-1 数据避免同期未来函数；
本模块是实盘，用「截至当下最新一根」的数据预测次日，二者是同一个预测关系。
⚠️ 盘中跑≠收盘跑（今日 bar 是快照补的，量能未走完）——决策一律用收盘定稿。
================================================================================
"""
import math


def _ret_seq(closes):
    """日收益序列，长度 = len(closes)-1，第 k 项是 closes[k+1]/closes[k]-1"""
    out = []
    for i in range(1, len(closes)):
        if closes[i - 1] and closes[i - 1] > 0:
            out.append(closes[i] / closes[i - 1] - 1.0)
        else:
            out.append(0.0)
    return out


def _std(a):
    if len(a) < 4:
        return None
    m = sum(a) / len(a)
    v = sum((x - m) ** 2 for x in a) / (len(a) - 1)
    return math.sqrt(v) if v > 0 else 0.0


def beta(stock_ret, mkt_ret, win=60):
    """对市场收益的 OLS 斜率 = cov/var"""
    n = min(len(stock_ret), len(mkt_ret), win)
    if n < 20:
        return None
    y = stock_ret[-n:]
    x = mkt_ret[-n:]
    my = sum(y) / n
    mx = sum(x) / n
    sxy = sum((a - my) * (b - mx) for a, b in zip(y, x))
    sxx = sum((b - mx) ** 2 for b in x)
    if sxx <= 0:
        return None
    return sxy / sxx


def calc(bars, mkt_ret=None, win_v=20, win_b=60):
    """bars: [(date,o,c,h,l,vol)]，末根为最新（可以是盘中快照）
    mkt_ret: 与 bars 日期对齐的市场日收益序列（None 则不算 beta60）
    返回 dict，值可能为 None（数据不足或市场数据缺失）"""
    if len(bars) < 25:
        return {'beta60': None, 'vol20': None, 'amt20': None,
                'newlow20': None, 'P60': None}
    c = [float(b[2]) for b in bars]
    v = [float(b[5]) for b in bars]

    # ---- vol20：近 20 日收益的标准差 ----
    rs = _ret_seq(c)
    vol20 = _std(rs[-win_v:]) if len(rs) >= win_v else None

    # ---- amt20：近 20 日均成交额（价×量）的对数 ----
    seg = list(zip(c, v))[-win_v:]
    amt = [a * b for a, b in seg if a > 0 and b > 0]
    amt20 = math.log(max(sum(amt) / len(amt), 1.0)) if len(amt) >= 10 else None

    # ---- newlow20：收盘是否创近 20 日新低（含当日） ----
    tail = c[-win_v:]
    newlow20 = 1.0 if tail and tail[-1] <= min(tail) + 1e-9 else 0.0

    # ---- P60：收盘 / 近 60 日最高收盘 ×100（与 scan_macd 的 pos 同口径） ----
    hi = max(c[-60:]) if len(c) >= 60 else max(c)
    p60 = c[-1] / hi * 100 if hi > 0 else None

    # ---- beta60 ----
    b60 = beta(rs, mkt_ret, win_b) if mkt_ret else None

    return {'beta60': b60, 'vol20': vol20, 'amt20': amt20,
            'newlow20': newlow20, 'P60': p60}


def zscore(vals):
    """对一批值做标准化；不足 5 个有效值或标准差为 0 时全返回 0"""
    ok = [x for x in vals if x is not None]
    if len(ok) < 5:
        return [0.0] * len(vals)
    m = sum(ok) / len(ok)
    sd = math.sqrt(sum((x - m) ** 2 for x in ok) / (len(ok) - 1)) if len(ok) > 1 else 0.0
    if sd <= 0:
        return [0.0] * len(vals)
    return [0.0 if x is None else (x - m) / sd for x in vals]


# 四个因子等权。它们本就是同一根轴的四个测度，加权没有统计依据。
WEIGHTS = {'beta60': 1.0, 'vol20': 1.0, 'amt20': 1.0, 'newlow20': 1.0}


def act_score(rows, key='act'):
    """对 rows（每项是含 beta60/vol20/amt20/newlow20 的 dict）原地写入
    act 各分量 z 值、act（等权合成）、act_rank（名次，1=最活跃）"""
    keys = ['beta60', 'vol20', 'amt20', 'newlow20']
    zs = {}
    for k in keys:
        zs[k] = zscore([r.get(k) for r in rows])
    for i, r in enumerate(rows):
        for k in keys:
            r['z_' + k] = round(zs[k][i], 3)
        s = sum(WEIGHTS[k] * zs[k][i] for k in keys) / sum(WEIGHTS.values())
        r['act'] = round(s, 3)
    order = sorted(range(len(rows)), key=lambda i: -rows[i]['act'])
    for rank, i in enumerate(order, 1):
        rows[i]['act_rank'] = rank
    return rows


def tag(act):
    """活跃度分档（用于展示，不参与判定）"""
    if act is None:
        return ''
    if act >= 0.8:
        return '高活跃'
    if act >= 0.2:
        return '偏活跃'
    if act > -0.2:
        return '中性'
    if act > -0.8:
        return '偏冷'
    return '冷门'
