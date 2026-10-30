# -*- coding: utf-8 -*-
"""
MACD 信号实盘扫描器 v3（2026-10-27 三位专家评审后的 P0 修复）
------------------------------------------------------------------
用法：
    python scan_macd.py                      # 实时扫票池（deploy/index.html 的 STOCKS+PINNED）
    python scan_macd.py sh600105 sz000688     # 看指定个股体检（实时）
    python scan_macd.py --all-signals         # 只要触发任一水下信号就列出（不要求量能）
    python scan_macd.py --all                # 显示全部扫描到的票（含未通过者）
    python scan_macd.py --no-idx-gate         # 关掉大盘30分K门控（回到 v2 行为）
    python scan_macd.py --nofilter            # 关掉标的过滤（ST/次新/涨停/成交额/换手）
    python scan_macd.py --full                # 全市场扫（本地 julsep_klines.json，仅到 2026-10-14，复盘用）
    python scan_macd.py --old                # 退回 v1 判定（P<75 + 放量逻辑），仅作对比

================================================================================
用户长期约束（不随版本号变动，任何改版都必须遵守）
================================================================================
【0】大盘 K 档位「只提示、不拦截」—— 买不买由用户自己定
   · 任何 K 档位都列出全部命中票，档位结论只印在头部和列表顶部，不改名单。

⚠️ 2026-10-28 曾短暂加入以下三项，用户已要求全部撤销（回到 2026-10-27 16:50 状态）：
   ✗ 当日振幅 ≤6% 过滤（win_frontier.py 的实测结论）
   ✗ volr20 下限 0.10 → 0.35
   ✗ 末尾「建议仓位 / 出手门槛 / 集中度 5-8 只 / 尾盘卖不加止损」输出块
   回退前的完整版本备份在：_rollback_20261028/scan_macd.py.v32.bak

================================================================================
v3 相对 v2 的改动（每条都有 volr_calib.py 实测出处）
================================================================================
【P0-1】量能口径统一到 MA20，阈值改为 volr20 < 1.0
   · 旧口径「今日量/前5日均量 ∈[0.8,1.2]」与通达信 MA(VOL,20)、与回测口径三处不一致。
     实测：两口径「平量」名单 Jaccard 重合度仅 41.3%，**错位率 58.7%**
     —— 回测验过的样本和实盘下单的票不是同一批。
   · MA20 口径下 volr 分档（20,305 条信号池）：
        [0,0.5)+0.310%  [0.5,0.8)+0.218%  [0.8,1.0)+0.261%
        [1.0,1.1)-0.122%  [1.1,1.2)+0.118%  [1.2,1.4)-0.021%
        [1.7,2.5)-0.390%  [2.5,∞)-0.569%   ← 放量区单调向下，很干净
     → 真正的切点是 **1.0**，不是 1.2。[1.0,1.2) 是拖累，不该放进名单。
   · 盘中量能怎么办：腾讯日线的今日量是「累计到当下」的量，直接除均量会系统性偏低。
     改用快照的量比 f[49] 反推全日预估量（量比本身就是时间归一化后的值），
     再乘 TAIL_ADJ 补偿尾盘放量。实测量比与「今日量/MA5」误差 ±8%（黄底假设）。

【P0-2】大盘30分K 判档，**只提示不拦截**（用户长期约束：买不买自己定）
   · 实测（899只×95天，叠加 volr20<1.0，账户口径等权8只）：
        大盘K∈[20,40)  14天  日均 +1.721%  夏普 0.613  回撤 2.0%   ← ★最强档
        大盘K<20       11天  日均 +0.315%  夏普 0.119  回撤 6.2%   ← ⚠次优（波动大）
        大盘K∈[40,60)  14天  日均 -0.053%  夏普 -0.029            ← 弱势
        大盘K∈[60,80)  18天  日均 -0.421%  夏普 -0.187 回撤12.6%   ← 最差
        大盘K≥40 合计  45天  日均 -0.124%                          ← 弱势
   · ⚠️ **全部只印 conclusion、不改名单**（用户明确要求：K≥40 也照样列，买不买自己定）。
     所以 --all 标志虽仍保留，但已不再需要——任何档位都会列出全部命中票。
   · 稳健性：大盘K<40 子集下，量能各窗口**月度 5/5 全正、剔Top3后仍 +0.408%**
     —— 反过来，没有大盘择时过滤时任何量能窗口都过不了稳健性关。真正起作用的是择时。

【P1】新增标的过滤（可执行性，不追求 alpha）
   ST/退市、次新(数据<120根)、涨停买不进、一字板、成交额<0.5亿、换手率越界。
   ⚠️ 腾讯 kline/kline **不支持复权**（实测 qfq 参数返回空），所以实盘日线同样有
      除权污染 → 新增除权检测：近 25 根内出现「物理不可能跳空」的票直接判否
      （MACD/KDJ/P 值在除权日全部失真）。

保留 v2 的判断：
   ❌ 位置 P<75 仍不拦（账户口径每档 N 都是无 P 赢，日夏普差 10 倍）
   ❌ 放量要求仍不要
   ✅ DIF<0 + 水下三信号
   🆕 KDJ K 值只标记不拦截

执行口径（很重要）：
   1. 收益 100% 来自次日盘中（隔夜段 -0.012%、日内段 +0.532%）→ 必须拿到次日尾盘。
   2. 14:45 之后再跑；14:50 前买到手。
   3. 约 65% 的交易日一只都筛不出来，别放宽条件凑票。
   4. 次日止损改分级：开盘<-5% 立即卖 / 盘中<-7% 才止损（原 -3% 触发率31.5%且有害）。
   5. 成本 0.15%/笔。
"""
import json
import os
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
DAILY_FILE = os.path.join(BASE, 'julsep_klines.json')

# 实时取数复用 scan_newlow 的（腾讯口径、已验证）
from scan_newlow import fetch_day, qt_snap, pool_codes   # noqa: E402

import activity_factor as AF   # noqa: E402  活跃度/风险轴（2026-10-31）
import mkt_regime as MR        # noqa: E402  大盘状态判定

# ==================== 参数区（★ = 可按你的偏好改） ====================
VOLR_MA = 20            # 均量基准：与通达信 MA(VOL,20) 对齐
VOLR_MAX = 1.0          # ★ volr20 上限（MA20 口径下 ≥1.0 即负区）
VOLR_MIN = 0.10         # ★ volr20 下限
TAIL_ADJ = 1.05         # ★ 盘中「量比线性外推」的尾盘放量补偿系数（假设值，±8% 误差）

MIN_AMOUNT = 5000.0     # ★ 成交额下限（万元）= 0.5 亿
TURNOVER_LO = 1.0       # ★ 换手率下限（%）
TURNOVER_HI = 30.0      # ★ 换手率上限（%）
MIN_BARS = 120          # ★ 少于此日线根数视为次新，剔除

IDX_GATE = True         # ★ 大盘30分K门控总开关（False = 回到 v2）
IDX_BEST_LO, IDX_BEST_HI = 20.0, 40.0   # 最强档
FILTER = True           # 标的过滤总开关

# ---- 双名单（2026-10-31 新增，--dual 触发；不触发时行为与 v3.3 完全一致）----
DOWN_TOPN = 8           # ★ 跌日防御名单取活跃度最低的 N 只
UP_TOPN = 8             # ★ 涨日进攻名单最多列 N 只
# ⚠️ 2026-10-31: 不能用点开头！GitHub Pages（无 .nojekyll 时走 Jekyll）不发布 dotfile，
# 线上实测 .cache_dual_picks.json 直接 404，页面拿不到名单。
JSON_FILE = 'dual_picks.json'
# ====================================================================

NAMES = {'603067': '振华股份', '600105': '永鼎股份', '601126': '四方股份',
         '000688': '国城矿业', '603120': '肯特催化', '001221': '悍高集团',
         '600866': '星湖科技', '603256': '宏和科技', '603225': '新凤鸣', '002558': '巨人网络'}


def ema(vals, n):
    k = 2.0 / (n + 1)
    out, e = [], vals[0]
    for v in vals:
        e = v * k + e * (1 - k)
        out.append(e)
    return out


def macd_line(c):
    ef, es = ema(c, 12), ema(c, 26)
    dif = [a - b for a, b in zip(ef, es)]
    dea = ema(dif, 9)
    return dif, dea, [a - b for a, b in zip(dif, dea)]


def kdj_k(bs):
    """KDJ(9,3,3) 的 K/D（全序列预热，初值 50；130 根下与「预热120根」等价）"""
    K, D = 50.0, 50.0
    for j in range(len(bs)):
        s = max(0, j - 8)
        hh = max(float(b[3]) for b in bs[s:j + 1])
        ll = min(float(b[4]) for b in bs[s:j + 1])
        rsv = (float(bs[j][2]) - ll) / (hh - ll) * 100 if hh > ll else 50.0
        K = 2.0 / 3 * K + 1.0 / 3 * rsv
        D = 2.0 / 3 * D + 1.0 / 3 * K
    return K, D


def limit_of(code):
    c = code[-6:]
    return 0.205 if c.startswith(('30', '68')) else 0.105


def find_dividend(bs, look=25):
    """检测除权：近 look 根内出现「隔日跌幅超涨跌停物理限制」→ 数据未复权，指标失真"""
    lim = limit_of(bs.code) if hasattr(bs, 'code') else 0.105
    hits = []
    c = [float(b[2]) for b in bs]
    o = [float(b[1]) for b in bs]
    for i in range(max(1, len(bs) - look), len(bs)):
        if c[i - 1] <= 0:
            continue
        # 开盘缺失（如盘中快照补的今日 bar）时只看收盘，避免把 0 当成 -100% 除权
        ro = (o[i] / c[i - 1] - 1) if o[i] and o[i] > 0 else 0.0
        if c[i] / c[i - 1] - 1 < -lim or ro < -lim:
            hits.append(bs[i][0])
    return hits


def k_zone(k):
    if k >= 80:
        return '⚠K≥80', 2
    if 50 <= k < 80:
        return '★强档', 0
    if k < 20:
        return '⚠K<20', 2
    return '', 1


def check(code, bs, old=False, full_vol=None, snap=None):
    """返回该票最新一根 K 线的体检结果。bs: [(date,o,c,h,l,vol)]
    full_vol: 实盘时传入「全日预估成交量」（由量比反推），None=用 bs 末根真实量"""
    if len(bs) < 62:
        return None
    c = [float(b[2]) for b in bs]
    v = [float(b[5]) for b in bs]
    hi_ary = [float(b[3]) for b in bs]
    lo_ary = [float(b[4]) for b in bs]
    dif, dea, hist = macd_line(c)
    i = len(c) - 1
    ma10 = sum(c[i - 9:i + 1]) / 10

    hi = max(c[i - 59:i + 1])
    pos = c[i] / hi * 100 if hi > 0 else None

    vtoday = full_vol if full_vol else v[i]
    m20 = sum(v[i - VOLR_MA:i]) / VOLR_MA
    m5 = sum(v[i - 5:i]) / 5
    volr = vtoday / m20 if m20 > 0 else 0.0          # ★ 主口径 = MA20
    volr5 = vtoday / m5 if m5 > 0 else 0.0            # 仅对照显示

    run, k = 0, i
    while k >= 1 and hist[k] < 0 and hist[k] > hist[k - 1]:
        run += 1
        k -= 1
        if run > 12:
            break

    gold = dif[i] > dea[i] and dif[i - 1] <= dea[i - 1]
    below = dif[i] < 0
    weak = below and dea[i] < 0

    sigs = []
    if gold and below:
        sigs.append(('水下金叉', 3))
    if dif[i] < 0 and dif[i] > dif[i - 1] > dif[i - 2]:
        sigs.append(('DIF水下上翘', 2))
    if run >= 2:
        sigs.append(('绿柱缩短%d根' % min(run, 12), 1))

    lo = max(1, i - 20)
    pl = min(range(lo, i), key=lambda k: c[k])
    div = c[i] <= c[pl] and dif[i] > dif[pl]

    kv, dv = kdj_k(bs)
    zone, zrank = k_zone(kv)

    vol_ok = VOLR_MIN <= volr < VOLR_MAX
    amp = (hi_ary[i] - lo_ary[i]) / c[i - 1] if c[i - 1] else 0.0   # 仅展示，不参与判定
    if old:                                    # v1 的条件量能（仅对比用）
        if not below:
            vol_ok = 0.8 <= volr5 <= 1.2
        elif c[i] < ma10:
            vol_ok = volr5 > 1.2
        else:
            vol_ok = 0.8 <= volr5 <= 1.2

    # ---- 除权污染（腾讯日线不复权，必须查） ----
    dv_hits = find_dividend(bs)

    if old:
        pass_all = (pos is not None and pos < 75) and below and sigs and vol_ok
    else:
        pass_all = below and bool(sigs) and vol_ok and not dv_hits

    return {
        'code': code, 'date': bs[-1][0], 'close': round(c[i], 2),
        'pos': round(pos, 1) if pos else None,
        'dif': round(dif[i], 3), 'dea': round(dea[i], 3), 'hist': round(hist[i], 3),
        'above_ma10': c[i] > ma10, 'volr': round(volr, 3), 'volr5': round(volr5, 3),
        'amp': round(amp * 100, 2),
        'run': min(run, 12), 'gold': gold, 'below': below, 'weak': weak, 'div': div,
        'sigs': sigs, 'score': sum(w for _, w in sigs),
        'K': round(kv, 1), 'D': round(dv, 1), 'zone': zone, 'zrank': zrank,
        'vol_ok': vol_ok, 'dividend': dv_hits, 'pass': pass_all,
        'nbars': len(bs),
    }


# ==================== 标的过滤（可执行性） ====================
def filt_reasons(r, s, live):
    if not FILTER:
        return []
    bad = []
    name = (s or {}).get('name') or ''
    if any(x in name.upper() for x in ('ST', '退')):
        bad.append('ST/退市')
    if r['nbars'] < MIN_BARS:
        bad.append('次新%d根' % r['nbars'])
    if not live:                       # 全市场复盘模式没有快照，跳过快照类过滤
        return bad
    up = (s or {}).get('up_price')
    cl = (s or {}).get('close')
    if up and cl and cl >= up - 1e-6:
        bad.append('涨停买不进')
    hi, lo = (s or {}).get('high'), (s or {}).get('low')
    if hi is not None and lo is not None and abs(hi - lo) < 1e-9:
        bad.append('一字板')
    amt = (s or {}).get('amount')
    if amt is not None and amt < MIN_AMOUNT:
        bad.append('额%.0f万' % amt)
    to = (s or {}).get('turnover')
    if to is not None and not (TURNOVER_LO <= to <= TURNOVER_HI):
        bad.append('换手%.1f%%' % to)
    return bad


# ==================== 大盘30分K门控 ====================
def idx_state():
    """返回 dict(k, close, zone, note, ok) ；取数失败时 ok=False"""
    try:
        import index_kdj_watch as W
        kdj = W.fetch_index_m30('m30')
    except Exception as e:
        return dict(ok=False, note='大盘取数异常: %s' % e)
    if not kdj:
        return dict(ok=False, note='大盘30分数据为空')
    k = kdj['cur']['k']
    close = kdj['cur']['close']
    if IDX_BEST_LO <= k < IDX_BEST_HI:
        z, note = 'best', '★最强档：14天账户日均 +1.721%、夏普 0.613、回撤 2.0%'
    elif k < IDX_BEST_LO:
        z, note = 'low', '⚠次优档：11天账户日均 +0.315% 但夏普仅 0.119、回撤 6.2%（波动大，自己定）'
    else:
        z, note = 'weak', ('⚠️ 弱势档：K≥40 的 45 天账户日均 -0.124%、其中 K∈[60,80) 18天 -0.421%'
                           '（夏普 -0.187、回撤 12.6%）。只提示不拦，买不买你自己定')
    return dict(ok=True, k=k, close=close, zone=z, note=note)


def live_bars(code, snap):
    """实时日线：腾讯日线（末根可能含今日，去掉）+ 快照补今日 bar"""
    bars = fetch_day(code, 200)
    if not bars:
        return None, None
    num = __import__('re').sub(r'\D', '', code)
    s = snap.get(num) or {}
    today = time.strftime('%Y-%m-%d')
    if bars and bars[-1][0] == today:
        bars = bars[:-1]
    if s.get('close'):
        bars = list(bars) + [(today, s.get('open') or s['close'], s['close'],
                              s.get('high') or s['close'],
                              s.get('low') or s['close'],
                              float(s.get('vol') or 0))]
        return bars, s
    return bars, s


def est_full_vol(bars, s):
    """盘中全日预估成交量：量比(已时间归一化) × 前5日均量 × 尾盘补偿
    量比定义 = (今日累计量/已开盘分钟) / (前5日均量/240)
    → 全日线性外推量 = 量比 × 前5日均量"""
    if not s:
        return None, 'no-snap'
    vr = s.get('vr')
    v = [float(b[5]) for b in bars if b[0] != time.strftime('%Y-%m-%d')]
    if len(v) < 25:
        return None, 'hist-short'
    m5 = sum(v[-5:]) / 5
    if not vr or m5 <= 0:
        return float(v[-1]), 'fallback-lastday'
    return vr * m5 * TAIL_ADJ, 'volratio'


def report(r):
    print(('  ── %s %s　%s' % (r['code'], NAMES.get(r['code'], ''), r['date'])).rstrip())
    print('     收盘=%.2f　位置P=%s（仅供参考，不拦截）　%s MA10'
          % (r['close'], r['pos'], '站上' if r['above_ma10'] else '仍在'))
    print('     KDJ：K=%.1f D=%.1f　%s' % (r['K'], r['D'], r['zone'] or '中档'))
    print('     DIF=%.3f  DEA=%.3f  MACD柱=%.3f   →  DIF%s'
          % (r['dif'], r['dea'], r['hist'], '水下' if r['below'] else '水上(警戒)'))
    print('     量能比 volr20=%.3f（主口径，%.2f≤x<%.1f）  volr5=%.3f（旧口径对照）  %s'
          % (r['volr'], VOLR_MIN, VOLR_MAX, r['volr5'], 'OK' if r['vol_ok'] else '不达标'))
    print('     当日振幅=%.2f%%（仅展示，不参与判定）' % r['amp'])
    sig = '、'.join('%s(%d分)' % (n, w) for n, w in r['sigs']) or '无'
    print('     触发信号：%s   合计 %d 分' % (sig, r['score']))
    if r['dividend']:
        print('     ⚠️ 疑似除权日 %s —— 腾讯日线不支持复权，MACD/KDJ/P 全部失真，已判否'
              % '/'.join(r['dividend']))
    if r['filt']:
        print('     ⚠️ 过滤：%s' % '、'.join(r['filt']))
    if r['div']:
        print('     ⚠ 底背离形态——回测显示无效（超额0.00%），别单独据此抄底')
    print('     判定：%s' % ('✅ 通过' if r['pass'] and not r['filt'] else '❌ 不符合'))
    print()


# ==================== 双名单（2026-10-31 新增） ====================
# 依据：横截面归因里 15 个候选因子中，只有 4 个在跌日/涨日两场景都分位单调、
# 且方向完全镜像 —— beta60 / vol20 / amt20 / newlow20。
# 它们本质是同一根「活跃度/风险」轴的四个测度，合成 ACT 综合分：
#     跌日 → 买轴的低端（冷门低弹性）  涨日 → 买轴的高端（活跃高弹性）
# 行首加 [跌] / [涨] 前缀：避免被 build_today_update.py 的正则误抓（它会匹配纯代码开头的行）
def _fmt(v, w=6, p=3):
    return ('%*s' % (w, '-')) if v is None else ('%*.*f' % (w, p, v))


def dual_row(r):
    return {
        'code': r['code'], 'name': r['name'], 'close': r['close'],
        'act': round(r.get('act') or 0.0, 3), 'act_rank': r.get('act_rank'),
        'tag': AF.tag(r.get('act')),
        'beta60': (round(r['beta60'], 3) if r.get('beta60') is not None else None),
        'vol20': (round(r['vol20'], 4) if r.get('vol20') is not None else None),
        'amt20': (round(r['amt20'], 2) if r.get('amt20') is not None else None),
        'newlow20': r.get('newlow20'), 'P60': (round(r['P60'], 1) if r.get('P60') is not None else None),
        'K': r['K'], 'DIF': r['dif'], 'volr': r['volr'],
        'amount': r.get('amount'), 'turnover': r.get('turnover'),
        'sigs': [n for n, _ in r['sigs']],
        'pass': r['pass'], 'filt': list(r.get('filt') or []),
    }


def print_dual(rg, up_list, down_list):
    print()
    print('=' * 100)
    if rg['ok']:
        print('大盘状态：上证 %.2f  %+.2f%%  →  %s  （%s）'
              % (rg['close'], rg['chg_pct'], rg['label'], rg['ts']))
    else:
        print('大盘状态：取数失败（%s）→ 两份名单都列，你自己看' % rg['note'])
    print('=' * 100)
    if rg['state'] == 'up':
        print('>> 当前应看【B 涨日·进攻名单】')
    elif rg['state'] == 'down':
        print('>> 当前应看【A 跌日·防御名单】')
    elif rg['state'] == 'flat':
        print('>> 当前平淡日：两名单都只作参考，实测这 116 天没有结构差异')
    print('   ' + MR.advice(rg['state']))
    print()

    print('⚠️ 两份名单都不用 MACD 做门槛。原因：DIF<0 水下 + 缩量 筛出的正是低活跃度票，'
          '与「涨日买高活跃」直接冲突。MACD 信号只留在最右列供你参考。')
    print()
    print('【A 跌日·防御名单】活跃度最低的 %d 只（门槛：可买性 + 无除权）'
          % len(down_list))
    print('-' * 100)
    if down_list:
        print('%-6s %-10s %-6s %-4s %8s %-5s %8s %8s %8s %-4s %7s %6s %8s %8s %s'
              % ('', '代码', '名称', '现价', '活跃度', '档位', 'beta60', 'vol20',
                 'amt20', '新低', '位置P', 'K值', 'DIF', 'volr20', '信号'))
        for r in down_list:
            d = dual_row(r)
            print('[跌] %-10s %-6s %-4.2f %8.3f %-5s %8s %8s %8s %-4s %7s %6.1f %8.3f %8.3f %s'
                  % (d['code'], d['name'][:6], d['close'], d['act'], d['tag'],
                     _fmt(d['beta60'], 8, 3), _fmt(d['vol20'], 8, 4),
                     _fmt(d['amt20'], 8, 2), '是' if d['newlow20'] else '否',
                     _fmt(d['P60'], 7, 1), d['K'], d['DIF'], d['volr'],
                     '、'.join(d['sigs']) or '-'))
    else:
        print('  （无）票池内没有可买标的')

    print()
    print('【B 涨日·进攻名单】活跃度最高的 %d 只（门槛：可买性 + 无除权）'
          % len(up_list))
    print('-' * 100)
    if up_list:
        print('%-6s %-10s %-6s %-4s %8s %-5s %8s %8s %8s %-4s %7s %6s %8s %8s %s'
              % ('', '代码', '名称', '现价', '活跃度', '档位', 'beta60', 'vol20',
                 'amt20', '新低', '位置P', 'K值', 'DIF', 'volr20', '信号'))
        for r in up_list:
            d = dual_row(r)
            print('[涨] %-10s %-6s %-4.2f %8.3f %-5s %8s %8s %8s %-4s %7s %6.1f %8.3f %8.3f %s'
                  % (d['code'], d['name'][:6], d['close'], d['act'], d['tag'],
                     _fmt(d['beta60'], 8, 3), _fmt(d['vol20'], 8, 4),
                     _fmt(d['amt20'], 8, 2), '是' if d['newlow20'] else '否',
                     _fmt(d['P60'], 7, 1), d['K'], d['DIF'], d['volr'],
                     '、'.join(d['sigs']) or '-'))
    else:
        print('  （无）当前无标的通过全部条件 —— 约 65% 的交易日就该是这样，别放宽条件凑票')
    print()
    print('活跃度 ACT = z(beta60)+z(vol20)+z(amt20)+z(newlow20) 等权合成；'
          '四者是同一根轴的四个测度，不是四个独立 alpha')
    print('名单只是排序结果，不是买卖指令 —— 买不买你自己定')


def dump_dual(rg, up_list, down_list):
    import datetime
    data = {
        'ts': time.strftime('%Y-%m-%d %H:%M'),
        'regime': {k: rg.get(k) for k in ('state', 'label', 'chg_pct', 'close', 'ts', 'ok')},
        'up': [dual_row(r) for r in up_list],
        'down': [dual_row(r) for r in down_list],
        'meta': {'down_topn': DOWN_TOPN, 'up_topn': UP_TOPN,
                 'volr_max': VOLR_MAX, 'src': 'scan_macd.py --dual --json'},
    }
    # 三处都写：根目录（本地）+ docs/（Pages 源）+ deploy/（本地预览）
    wrote = []
    for sub in ('', 'docs', 'deploy'):
        d = os.path.join(BASE, sub) if sub else BASE
        if not os.path.isdir(d):
            continue
        path = os.path.join(d, JSON_FILE)
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
            wrote.append(sub or './')
        except Exception as e:
            print('  !! 写入 %s 失败: %s' % (path, e))
    print('\n已写入 %s（大盘=%s，涨日名单 %d 只，跌日名单 %d 只）'
          % ('、'.join(wrote), rg['label'], len(up_list), len(down_list)))


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    flags = set(a for a in sys.argv[1:] if a.startswith('--'))
    full = '--full' in flags
    all_sig = '--all-signals' in flags
    old = '--old' in flags
    show_all = '--all' in flags
    dual = '--dual' in flags          # 2026-10-31 双名单（涨日进攻 / 跌日防御）
    json_out = '--json' in flags      # 双名单写 JSON 供仪表盘读取

    # ---------- 全市场（本地历史文件，仅复盘用） ----------
    if full:
        daily = json.load(open(DAILY_FILE))
        res = []
        for code, bs in daily.items():
            r = check(code, bs, old=old)
            if not r:
                continue
            r['filt'] = []
            if all_sig:
                if r['sigs'] and r['below']:
                    res.append(r)
            elif r['pass']:
                res.append(r)
        res.sort(key=lambda x: (x['zrank'], -x['score']))
        print('MACD 扫描（全市场·本地文件）　⚠️ 数据截至 2026-10-14 且未复权，仅供复盘')
        print('模式：%s（共 %d 只，除权污染票已剔除）\n'
              % ('旧版 P<75' if old else 'DIF<0 + 水下信号 + volr20<%.1f' % VOLR_MAX, len(res)))
        print('%-8s %-7s %-6s %-6s %-8s %-7s %-10s %s'
              % ('代码', '位置P', 'K值', '得分', 'DIF', 'volr20', 'K分档', '信号'))
        print('-' * 96)
        for r in res[:40]:
            print('%-8s %6.1f %6.1f %6d %8.3f %7.3f %-10s %s'
                  % (r['code'], r['pos'] or 0, r['K'], r['score'], r['dif'],
                     r['volr'], r['zone'], '、'.join(n for n, _ in r['sigs'])))
        if not res:
            print('（无）当前无标的通过全部条件')
        return

    # ---------- 大盘门控状态 ----------
    gate_on = IDX_GATE and not old
    idx = idx_state() if gate_on else None
    if gate_on:
        print('大盘30分KDJ门控：', end='')
        if not idx['ok']:
            print('⚠️ %s → 门控失效，本次不拦截（结果仅供参考）' % idx['note'])
        else:
            print('K=%.1f  上证=%.2f' % (idx['k'], idx['close']))
            print('  %s' % idx['note'])
    else:
        print('大盘判档：关闭（--old 或手动关闭）')

    if gate_on and idx and idx['ok']:
        print('  ⚠️ 只提示，不拦截：名单照常全列出，买不买你自己定')

    # ---------- 指定个股 / 票池（实时） ----------
    codes = args if args else pool_codes()
    if not codes:
        print('没有指定代码，也没读到票池（deploy/index.html）')
        return
    codes = [c if c.startswith(('sh', 'sz')) else
             ('sh' + c if c[0] in '6' else 'sz' + c) for c in codes]
    print('实时拉取 %d 只（腾讯日线 + 快照）…' % len(codes))

    # 上证日线：beta60 回归需要（拉取失败则 beta60 缺失，其余三因子照常算）
    mkt_ret = None
    try:
        mb = fetch_day('sh000001', 200)
        if mb and len(mb) > 25:
            mc = [float(b[2]) for b in mb]
            mkt_ret = [mc[i] / mc[i - 1] - 1 for i in range(1, len(mc)) if mc[i - 1] > 0]
        else:
            print('  !! 上证日线为空，beta60 本次缺失')
    except Exception as e:
        print('  !! 上证日线拉取失败，beta60 本次缺失: %s' % e)

    snap = qt_snap(codes)
    rows = []
    for c in codes:
        bs, s = live_bars(c, snap)
        if not bs:
            print('  !! %s 日线拉取失败' % c)
            continue
        fv, how = est_full_vol(bs, s)
        r = check(c, bs, old=old, full_vol=fv, snap=s)
        if r:
            r['name'] = (s or {}).get('name') or NAMES.get(c[2:], '')
            r['filt'] = filt_reasons(r, s, live=True)
            r['amount'] = (s or {}).get('amount')
            r['turnover'] = (s or {}).get('turnover')
            # 活跃度/风险轴四因子（+ P60，与 check 的 pos 同口径）
            r.update(AF.calc(bs, mkt_ret))
            rows.append(r)
        time.sleep(0.12)
    AF.act_score(rows)

    if args and not all_sig:
        for r in rows:
            report(r)
        return

    if all_sig:
        pick = [r for r in rows if r['sigs'] and r['below']]
    else:
        pick = [r for r in rows if r['pass'] and not r['filt']]
    pick.sort(key=lambda x: (x['zrank'], -x['score'], x['K']))

    # ---------- 大盘判档：只印 conclusion，名单一律全列（用户长期约束） ----------
    print('\nMACD 水下信号扫描（实时）　%s' % time.strftime('%Y-%m-%d %H:%M'))
    print('条件：DIF<0 + 水下信号 + volr20<%.1f  （共 %d/%d 只）'
          % (VOLR_MAX, len(pick), len(rows)))
    if gate_on and idx and idx['ok'] and idx['zone'] == 'weak':
        print('⚠️ 大盘 K=%.1f ≥ %.0f → 弱势档。历史 45 天账户日均 -0.124%，'
              % (idx['k'], IDX_BEST_LO))
        print('   K∈[60,80) 那 18 天更差：-0.421%、夏普 -0.187、回撤 12.6%。名单照列，自己定。')
    if gate_on and idx and idx['ok'] and idx['zone'] == 'low':
        print('⚠️ 大盘 K=%.1f < %.0f：次优档，胜率还行但波动大（回撤 6.2%），仓位自己收着点。'
              % (idx['k'], IDX_BEST_LO))

    print()
    print('%-10s %-8s %-6s %-6s %-5s %-8s %-7s %-10s %-7s %s'
          % ('代码', '名称', '现价', '位置P', 'K值', 'DIF', 'volr20', '成交额(万)', '换手%', '信号'))
    print('-' * 104)
    for r in pick:
        print('%-10s %-8s %6.2f %6.1f %5.1f %8.3f %7.3f %10s %7s %s'
              % (r['code'], r['name'][:8], r['close'], r['pos'] or 0, r['K'],
                 r['dif'], r['volr'],
                 ('%.0f' % r['amount']) if r['amount'] else '-',
                 ('%.1f' % r['turnover']) if r['turnover'] is not None else '-',
                 '、'.join(n for n, _ in r['sigs'])))
    if not pick:
        print('（无）当前无标的通过全部条件 —— 约 65% 的交易日就该是这样，别放宽条件凑票')
        print('     若单次个股体检能看到票、但这里为空，多半是被 volr/标的过滤挡掉的，看上面体检详情')
    print('\nK分档：★强档=K∈[50,80)｜⚠K≥80=最差(-0.308%)｜⚠K<20=超卖抄底无效（只标记，不拦截）')
    print('量能：volr20 = 全日预估量 / MA(VOL,20)，%.2f ≤ x < %.1f（MA20 口径下 ≥1.0 是负区）'
          % (VOLR_MIN, VOLR_MAX))

    # ---------- 双名单（--dual，独立于上面的单名单，不改动任何原有输出） ----------
    if dual:
        rg = MR.regime()
        # 同一候选池：可买性 + 无除权（MACD 不做门槛，只作展示列，理由见 print_dual 说明）
        pool = [r for r in rows if not r['filt'] and not r['dividend']]
        dn_pick = sorted(pool, key=lambda x: x.get('act', 0))[:DOWN_TOPN]
        up_pick = sorted(pool, key=lambda x: -x.get('act', 0))[:UP_TOPN]
        print_dual(rg, up_pick, dn_pick)
        if json_out:
            dump_dual(rg, up_pick, dn_pick)


if __name__ == '__main__':
    main()
