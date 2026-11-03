# -*- coding: utf-8 -*-
"""
隔夜跳空质量模块（2026-11-03 建立）
================================================================================
用户诉求：推的票里好多"今天收盘一个价、第二天开盘在水下好几个点、白天一天回不到
昨收"。→ 选票时先回看这只票**过去一周（5 个交易日）的隔夜走向**，反复出现这种
形态的就别放进名单。

★ 指标定义（只用日线，无未来函数）
--------------------------------------------------------------------------------
对最近 WIN 个交易日，逐日算：
    gap_i     = open_i / close_{i-1} - 1          （隔夜跳空，%）
    recov_i   = high_i >= close_{i-1}             （盘中是否摸到过前收）

汇总：
    bad     近 WIN 日内 gap <= GAP_TH（默认 −1%）的次数
    worst   近 WIN 日最差的一次 gap（%）
    mean    近 WIN 日平均 gap（%）
    rec     低开日的回补率 = 摸到前收的低开日 / 低开日

★ 排除规则（两条，任一命中即「不放进来」）
--------------------------------------------------------------------------------
  R1 反复低开：bad >= BAD_N（默认一周 2 次以上低开 ≤ −1%）
     ⚠️ 实测「回补率」不是关键变量：加上"且回补<50%"后规则变窄（剔除 2.7% vs 12.1%）、
        分离度 t 从 18.0 掉到 16.4 —— 该排除的是"老低开"，不是"低开没回来"。
        → REQUIRE_REC 默认 False，回补率只作展示列。
  R2 单次深坑：worst <= HARD_GAP（默认 −3%）且该日未回补
     → 一次跳空 −3% 以上还收不回来，说明有突发利空/资金出逃

★ 为什么规则这么松
--------------------------------------------------------------------------------
`analyze_overnight_gap.py`（191.6 万样本）已证明：全市场隔夜平均 −0.086%/天、
低开 >1% 的只占 10.3% —— 低开本身是常态，不是缺陷。所以只排除**重复发生且
回补失败**的个体，不做"任一次低开就砍"（那会把 90% 的票全砍掉）。

★ 与其它模块的关系
--------------------------------------------------------------------------------
· day_trend.py 管「日线方向」（在涨 / 下坡）
· gap_quality.py 管「隔夜质量」（开盘是否老漏水）
两者正交：一只票可以日线在涨但天天低开（前一日涨 2~10% 未封板的典型形态）。
· 不动 30 分钟 MACD 口径。

⚠️ 盘中跑：最新一根是当日快照（open 已定、close 未定），gap 用的是
   「今日 open / 昨日 close」，这个值盘中就是最终的，不受盘中波动影响 —— 相对稳。
===================================
"""

# ==================== 参数区（★ 可按偏好改） ====================
WIN = 5                 # ★ 回看窗口：5 个交易日 = 一周
GAP_TH = -1.0           # ★ 低开阈值（%）：gap ≤ 此值算一次"低开"
BAD_N = 2               # ★ 一周内低开次数达到几次 → 排除（R1）
HARD_GAP = -3.0         # ★ 单次深坑阈值（%）：跌这么深且当天没回补 → 排除（R2）
REQUIRE_REC = False     # ★ R1 是否额外要求"回补失败"。实测 False 更强（见下方说明）
REC_MIN = 0.5           # 只有 REQUIRE_REC=True 时才用：回补率低于此值才算"回不来"
EXCLUDE = True          # ★ True = 命中的票不进双名单；False = 只标注不拦截


def _pct(a, b):
    return (a / b - 1) * 100 if b else 0.0


def calc(bars, win=None, partial_last=None):
    """bars: [[date, open, close, high, low, vol], ...]（升序，close=索引2）

    partial_last: 最后一根是否"未走完"
        None（默认）= 自动判断：最后一根日期 == 今天 且 当前 < 15:00 → 视为未走完
        True/False  = 强制指定
      ⚠️ 未走完的那根：gap 仍然计入（开盘价是最终价），但**不参与回补率统计**
         （盘中最高价还会变，否则 10:41 跑一次会把后来翻红的票误判成"没回补"）。

    返回 dict（数据不足返回 None）：
      ok     True = 通过隔夜质量检查（可以进名单）
      bad    近 win 日低开（≤GAP_TH）次数
      worst  近 win 日最差 gap（%）
      mean   近 win 日平均 gap（%）
      rec    低开日回补率（0~1，无低开日/无完整日时 None）
      label  中文短标（隔夜稳 / 偶发低开 / 反复低开不回 / 深坑未补）
      flags  命中规则列表 ['R1'] / ['R2']
      partial 最后一根是否按"未走完"处理
    """
    win = win or WIN
    n = len(bars)
    if n < win + 1:                      # 需要 win 天 gap → 需要 win+1 根
        return None

    def _f(x):
        try:
            return float(x)
        except (TypeError, ValueError):
            return 0.0

    seg = bars[-(win + 1):]              # 最后一个 close 也参与（作为前收）

    if partial_last is None:
        try:
            import datetime
            last_d = str(seg[-1][0])
            today = datetime.datetime.now()
            ds = [last_d[0:4], last_d[4:6], last_d[6:8]] if '-' not in last_d else last_d.split('-')[:3]
            same_day = (int(ds[0]), int(ds[1]), int(ds[2])) == (today.year, today.month, today.day)
            partial_last = bool(same_day and today.hour < 15)
        except Exception:
            partial_last = False

    gaps = []
    for i in range(1, len(seg)):
        o = _f(seg[i][1])
        pc = _f(seg[i - 1][2])
        hi = _f(seg[i][3])
        if not pc or not o:
            continue
        g = _pct(o, pc)
        is_last = (i == len(seg) - 1)
        rec_known = not (partial_last and is_last)
        gaps.append((g, (hi >= pc) if rec_known else None))

    if not gaps:
        return None

    # 回补率只用"日内已走完"的交易日；未走完那根只计 gap
    bad_all = [(g, r) for g, r in gaps if g <= GAP_TH]
    bad_known = [(g, r) for g, r in bad_all if r is not None]
    bad = len(bad_all)
    worst = min(g for g, _ in gaps)
    mean = sum(g for g, _ in gaps) / len(gaps)
    rec = (sum(1 for _, r in bad_known if r) / len(bad_known)) if bad_known else None

    flags = []
    r1 = (bad >= BAD_N)
    if r1 and REQUIRE_REC and rec is not None:
        r1 = (rec < REC_MIN)
    elif r1 and REQUIRE_REC and rec is None:
        r1 = False                       # 只有未走完的一天且无完整日 → 不下结论
    if r1:
        flags.append('R1')               # 一周内反复低开
    if worst <= HARD_GAP:
        worst_rec = next((r for g, r in gaps if g == worst), True)
        if worst_rec is False:           # None = 那根还没走完，不下结论
            flags.append('R2')           # 深坑未补

    ok = (not flags) or (not EXCLUDE)

    if 'R2' in flags:
        label = '深坑未补'
    elif 'R1' in flags:
        label = '反复低开'
    elif bad >= 1:
        label = '偶发低开'
    else:
        label = '隔夜稳'

    return {
        'ok': ok, 'bad': bad, 'worst': round(worst, 2), 'mean': round(mean, 2),
        'rec': (round(rec, 2) if rec is not None else None),
        'label': label, 'flags': flags, 'win': win, 'partial': bool(partial_last),
    }


def short(g):
    """极短标签，给表格列用：'稳' / '低2次'"""
    if not g:
        return '-'
    return '稳' if g['bad'] == 0 else '低%d次' % g['bad']


def txt(g):
    """一行短描述，用于终端/JSON"""
    if not g:
        return '-'
    r = '-' if g['rec'] is None else '%.0f%%' % (g['rec'] * 100)
    return '%s(%d次低开/最差%.1f%%/回补%s)' % (g['label'], g['bad'], g['worst'], r)


if __name__ == '__main__':
    import json
    import sys
    import urllib.request

    codes = sys.argv[1:] or ['sh600000', 'sz000001']
    for c in codes:
        url = ('http://ifzq.gtimg.cn/appstock/app/kline/kline?param=%s,day,,,60' % c)
        try:
            raw = urllib.request.urlopen(
                urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'}),
                timeout=20).read().decode('utf-8', 'ignore')
            j = json.loads(raw)
            ks = j['data'][c]['day']
        except Exception as e:
            print(c, 'fail', e)
            continue
        g = calc(ks)
        print('%-10s %s' % (c, txt(g)))
        last = ks[-(WIN + 1):]
        for i in range(1, len(last)):
            o, pc, hi = float(last[i][1]), float(last[i - 1][2]), float(last[i][3])
            gp = (o / pc - 1) * 100
            print('    %s gap %+6.2f%%  %s' % (
                last[i][0], gp, '摸回前收' if hi >= pc else '未回'))
