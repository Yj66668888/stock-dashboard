#!/usr/bin/env python3
"""大盘资金「流出→回流」监测 v2.1b（2026-10-21 15:15 用户拍板回到 v2.1 参数）
======================================================================
★ 当前生效：位置门 K∈[20,40) + **资金回补 5 亿触发 / 10 亿强档**（绝对额口径）
  用户 10/21 15:15 原话：「改为 2.1 版本，就是资金回补调整为 5-10 亿」
  —— 比例口径（RECALL_RATIO=0.40）已置 0 关闭；常量与代码全部保留，
     把 RECALL_RATIO 改回 0.40 即可复活 v2.3 的比例判定，不需要动逻辑分支。

v2.1（10/21 用户拍板）：位置门区间 [30,40) → **[20,40)**
  K 20~40 合并 14 天：次日胜 78.6% / 均 +0.24%，5 日胜 61.5% / 均 +0.51%
  —— 是所有分档里唯一「次日与 5 日双双跑赢基准」的组合，持仓可放宽到 5 日。
  （原 [30,40) 只有 6 天，次日 83.3% 虽高但 5 日转负，只能做次日短打。）

v1 的问题（10/21 回测证实）：
  单独用「资金回补 X 亿」当信号是无效的——回补≥10亿在 7-10 月 74 个交易日里
  触发 56 天（75.7%），次日胜率 53.6%、5日胜率 34.0%，**双双低于不设门槛的
  基准（54.8% / 37.7%）**。回补额这个维度本身没有稳定的超额。

v2 的核心改动：**30 分 KDJ 位置变成前置门，资金回流只做确认**
------------------------------------------------------------------
用户澄清（10/21）：「回流10亿」不是无条件用的，是**大盘30分K在30-40之间时**
才开始回流的那个 10 亿。前置条件过滤后样本完全变了。

回测依据（backtest_kdj30_40_recall.py，2026-07-01~10-20，74 个交易日）：

  收盘 30 分 K 位置分档（不加任何资金条件）：
    K<20     12天  次日胜 66.7%  5日 -0.89%（超卖钝化，5日反而最差）
    K 20~30   8天  次日胜 75.0%  5日 +1.00% 胜 71.4%  ← 5日最强
    K 30~40   6天  次日胜 83.3% / +0.72%（超额+0.85） ← 次日最强（用户问的档）
    K 40~50   7天  次日胜 71.4%
    K 50~65  12天  次日胜 25.0%  ← 最差，别碰
    K>65     28天  次日胜 46.4%
    K 20~40 合并 14天 次日胜 78.6% / +0.24%，5日胜 61.5% / +0.51%（唯一次日+5日双超基准）

  ⚠️ 关键反直觉发现：
    「盘中曾触及 30~40」43天 → 次日胜 58.1%（几乎无超额）
    「K 由<30 上穿进 30~40」事件化 10天 → 次日胜 50.0%（**低于基准**）
    → 说明：盘中路过的不算数，**收盘停在低位**才算数。盘中实时触发天然弱于收盘确认。

  ⚠️ 关于「10 亿」的结论：
    在 K30~40 这个子集里，Δ≥10亿 只剩 2 个样本（次日 +1.53% 胜率100%，
    但 5日胜率 0%）。样本太少，**测不出 10 亿的边际增益**，但也测不出害处。
    所以 10 亿在这里的定位是「确认资金没有继续恶化」，不是「提升胜率」。
    触发频率由 K 位置卡死（74 天只有 6~14 天），不会像 v1 那样刷爆额度。

  ⚠️ 持仓期限：v2.1 放宽到 K20~40 后，5 日也有超额（+0.51% / 胜 61.5%），
     可持有到 5 日；若改回 [30,40) 则只能做次日短打（那 2 天 5 日胜率 0%）。

判定思路：
  门1（位置）：当前 30 分 K 落在 [20, 40)  —— 不在区间直接不推
  门2（资金）：当日累计主力净流出从峰值**回补 ≥ MIN_RECALL_YI（5 亿）**才算数
              回补 ≥ STRONG_RECALL_YI（10 亿）= 强档；5~10 亿 = 弱档
              （比例口径 RECALL_RATIO 已置 0 关闭，代码保留可随时复活）
  门3（持续）：末段仍在净回升（排除单点脉冲）
  门4（方向，可选）：KDJ_REQUIRE_K_ABOVE_D = True 时额外要求 K > D

频控：当日上限 MAX_DAILY_PUSH 条；**不受个股推送时间窗限制**（全天任意时刻可推），
     但与 index_kdj_watch 的大盘通道合计预算受 Server酱 5 条/日额度约束。

数据源：
  https://push2.eastmoney.com/api/qt/stock/fflow/kline/get
    ?secid=1.000001 & klt=1(1分钟) & lmt=0(全天)
  字段 f52=主力净流入累计(元) f53=小单 f54=中单 f55=大单 f56=超大单
  ⚠️ 主力净流入 = 大单 + 超大单（元）

运行：
  python3 index_fund_flow_watch.py --test   # 只打印判定，不推送不写状态
  python3 index_fund_flow_watch.py          # 实跑（交易时段外自动跳过）
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE, '.index_fund_state.json')

# ---------- 门1：30 分 KDJ 位置（v2 新增，回测证实这是真正有效的过滤器）----------
KDJ_GATE_ENABLED = True      # False = 退回 v1 的纯资金判定（不推荐）
KDJ_RANGE_LO = 20.0          # ★ 用户 10/21 三次拍板：改回 [20, 40)
KDJ_RANGE_HI = 40.0          #   依据 compare_shortterm_signals.py 的 22 口径横向排名：
                             #   K20~40 是**唯一「次日 + 5 日双超基准」**的可稳妥使用档位
                             #   （次日 78.6%/+0.24%，5 日 61.5%/+0.51%）
                             #   ⚠️ 曾放宽到 [10,45)，但纯位置超额仅 +0.17%（排名跌到第 6），
                             #      且 K40~45 是全场唯一负超额段 → 已回退

# ★ 回测事实表：KEY = (KDJ_RANGE_LO, KDJ_RANGE_HI)，改常量时文案自动跟随，不会脱节
#   数据来源：scan_kdj_range.py（2026-07-01~10-20，74 个交易日，期间上证 -9.17%）
#   基准（全样本不设条件）：次日 -0.13% / 胜率 54.8%；5 日 -0.56% / 胜率 37.7%
BACKTEST_FACTS = {
    (10, 45): dict(n_pos=30, n_trig=6,
                   pos='区间本身 30 天：次日胜 73.3%、均 +0.04%（超额 +0.17）；5 日 -0.11%',
                   trig='叠加资金后 6 天：次日胜 83.3%、均 +0.80%（超额 +0.93）；'
                        '3 日 -0.09%、5 日 +0.11% 但胜率仅 33.3%（低于基准 37.7%）',
                   hold='⚠️ **持仓提示**：本组合只有**次日**占优，持有到 3~5 日胜率跌到 33.3%，'
                        '比基准还低。**只做次日短打，别过夜恋战。**',
                   freq='触发频率 6/74 天，约每 12 个交易日一次'),
    (20, 40): dict(n_pos=14, n_trig=2,
                   pos='区间本身 14 天：次日胜 78.6%、均 +0.24%（超额 +0.36）；'
                       '5 日 +0.51%、胜 61.5%（超额 +1.07）',
                   trig='叠加「回补≥5亿」后 2 天：次日胜 100%、均 +1.53%（超额 +1.66）；'
                        '但 3 日 -1.14%（胜率 0%）、5 日 -0.47%（胜率 0%）',
                   hold='⚠️ **持仓提示**：叠加资金门槛后**只有次日占优**，'
                        '持有到 3~5 日胜率归零（那 2 天全部回落）。'
                        '**只做次日短打，别过夜恋战。** 想拿 3~5 日就别叠加资金条件，'
                        '纯 K20~40 反而有 5 日超额（+0.51%/胜 61.5%）。',
                   freq='触发频率 2/74 天，约每 37 个交易日一次'),
    (30, 40): dict(n_pos=6, n_trig=2,
                   pos='区间本身 6 天：次日胜 83.3%、均 +0.72%（超额 +0.85）',
                   trig='叠加资金后 2 天：次日胜 100%、均 +1.53%',
                   hold='⚠️ **持仓提示**：该档位回测只支持**次日短打**，'
                        '持有到 5 日反而跑输基准，别恋战。',
                   freq='触发频率极低，可能长期不触发'),
}
DEFAULT_FACT = dict(n_pos=0, n_trig=0, pos='（该区间未做专门回测，以下为定性描述）',
                    trig='', hold='⚠️ **持仓提示**：区间未回测，请谨慎并自行复盘。', freq='')
KDJ_REQUIRE_K_ABOVE_D = False  # True = 额外要求 K>D（金叉向上）。回测：K20~40∧K>D 仅 3 样本，
                               # 次日胜 66.7% / 5日 +0.91% 胜 66.7%，方向对但样本太少

# ---------- 门2/3：资金回流判定 ----------
# ★ v2.1b（10/21 15:15 用户拍板）：回到**绝对额**口径 —— 回补 5 亿触发、10 亿强档
#   比例口径（RECALL_RATIO）保留代码但置 0 关闭，随时可复活。
#   两套口径各自的实测证据（同一份 74 天样本）：
#     绝对额 5/10 亿：在 K20~40 子集里 Δ≥0/5/10/15/20/30/50 亿筛出的**全是同一批 2 天**
#       （104.4 亿、176.0 亿），即 5 亿与 10 亿在**日线尺度**上无区分度；
#       但这 2 天次日 +1.53%/胜 100%，3 日 -1.14%（胜 0%）、5 日 -0.47%（胜 0%）。
#     比例 40%：同样 6 天，次日 +0.87%/胜 100%、5 日 +1.41%/胜 66.7%。
#   注意：日线回补量级约为盘中同口径的 12 倍，所以「5 亿 / 10 亿」在**盘中**是有区分度的
#   （盘中一条 5 亿的回补确实可能只是跌速放缓），在日线上才退化成同一批样本。
#   → 结论：绝对额口径在盘中可用，但只做**次日短打**；5 亿档更灵敏，提醒会更多。
MIN_RECALL_YI = 5.0          # ★ 触发线：净回补 ≥ 5 亿才提醒（用户拍板的 5-10 亿区间下沿）
RECALL_RATIO = 0.0           # 比例门：0 = 关闭（v2.3 用的是 0.40，改回去即复活）
                             #   非 0 时实际阈值为 max(MIN_RECALL_YI, 峰值流出 × 该比例)
STRONG_RECALL_YI = 10.0      # ★ 强档线：净回补 ≥ 10 亿 → 强回流（用户拍板的区间上沿）
SUSTAIN_POINTS = 5           # 持续性窗口：分钟密集序列看最近 5 点(≈5分钟)；稀疏序列只看最近 2 点
MIN_POINTS_FLOOR = 5         # 序列最短点数（快照稀疏模式 5 点即可判定）
SUSTAIN_MIN_YI = 0.5         # 持续性：末段净回升至少 0.5 亿（= 触发线的 10%）
                             #   ⚠️ v2.3 比例口径下曾是 1.0，因为那时回补门槛高达 37 亿；
                             #   绝对额口径触发线降到 5 亿后，1 亿/5分钟的持续门槛会把
                             #   **弱档 5~10 亿永久焊死**（5 分钟内几乎不可能升 1 亿）→ 降到 0.5
COOLDOWN_MIN = 30            # 冷却：距上次推送至少 30 分钟才算新一波
TRENCH_REFRESH_YI = 5.0      # 坑刷新：trough 再深 5 亿以上才允许重推
MAX_DAILY_PUSH = 2           # 当日上限（与 index_kdj_watch 合计受 Server酱 5 条约束）
                             #   放宽后触发频率约每 12 个交易日一次，仍远低于额度上限
THROTTLE_SEC = 60            # 跨进程取数节流：任意两次真实取数间隔下限，防东财限流

YI = 1e8                    # 1 亿

_CLEAN_ENV = {k: v for k, v in os.environ.items() if 'proxy' not in k.lower()}


def bj_now():
    return datetime.now(timezone(timedelta(hours=8)))


def in_trading_time(now):
    """周一~五 9:31-11:30, 13:01-15:02（覆盖开盘首分钟与收盘最后一分钟）"""
    if now.weekday() >= 5:
        return False
    m = now.hour * 60 + now.minute
    return (9 * 60 + 31 <= m <= 11 * 60 + 30) or (13 * 60 + 1 <= m <= 15 * 60 + 2)


def curl_get(url, timeout=10, retries=3):
    for i in range(retries):
        try:
            r = subprocess.run(
                ['curl', '-s', '--noproxy', '*', '--connect-timeout', str(timeout), url],
                capture_output=True, timeout=timeout + 5, env=_CLEAN_ENV)
            if r.returncode == 0 and r.stdout:
                return r.stdout
        except Exception:
            pass
        if i < retries - 1:
            time.sleep(0.5)
    return None


def _http_get(url, timeout=12, retries=4):
    """东财 push2 对 curl 的 TLS 指纹会直接空响应（rc=52），但 urllib 稳定；
    另一坑是**限流**：连续请求后所有 push2 接口返回 RemoteDisconnected，
    通常持续 1~3 分钟。所以这里做指数退避重试（3/6/12/20 秒）。
    两路请求都强制绕过本机代理。"""
    import urllib.request
    headers = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://data.eastmoney.com/'}
    # 指数退避 3/10/25/45 秒。东财对**连续密集请求**会整段限流
    # （实测 20+ 次/5分钟 → 所有 push2 接口持续 RemoteDisconnected 数分钟），
    # 而每 5 分钟一次的正常监控频率不会触发。退避到位即可自然恢复。
    delays = [0, 3, 10, 25]
    for i in range(retries):
        if delays[i]:
            time.sleep(delays[i])
        # —— 路线1：urllib + ProxyHandler({}) 完全禁代理 ——
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(urllib.request.Request(url, headers=headers), timeout=timeout) as resp:
                data = resp.read()
                if data:
                    return data
        except Exception:
            pass
        # —— 路线2：curl 兜底 ——
        try:
            r = subprocess.run(
                ['curl', '-s', '--noproxy', '*', '--connect-timeout', str(timeout),
                 '-H', 'Referer: https://data.eastmoney.com/', url],
                capture_output=True, timeout=timeout + 5, env=_CLEAN_ENV)
            if r.returncode == 0 and r.stdout:
                return r.stdout
        except Exception:
            pass
    return None


def fetch_market_flow():
    """拉上证指数分钟级资金流。返回 list[dict]，每项 {t, main, small, mid, big, xlbig} 单位:元"""
    url = ('https://push2.eastmoney.com/api/qt/stock/fflow/kline/get'
           '?secid=1.000001&fields1=f1,f2,f3,f7'
           '&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63'
           '&klt=1&lmt=0&ut=b2884a393a59ad64002292a3e90d46a5')
    raw = _http_get(url)
    if not raw:
        return []
    try:
        data = json.loads(raw).get('data') or {}
        klines = data.get('klines') or []
    except (json.JSONDecodeError, AttributeError):
        return []
    out = []
    for k in klines:
        p = k.split(',')
        if len(p) < 6:
            continue
        try:
            out.append({'t': p[0], 'main': float(p[1]), 'small': float(p[2]),
                        'mid': float(p[3]), 'big': float(p[4]), 'xlbig': float(p[5])})
        except ValueError:
            continue
    return out


def load_state():
    try:
        with open(STATE_FILE, 'r') as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f, ensure_ascii=False)
    except Exception:
        pass


def fetch_snapshot():
    """备用源：当日累计主力净流入(元) + 大单/超大单。三级降级：
       L1 push2 ulist  单点快照（最轻量）
       L2 push2his daykline 日线最后一条（同口径主力净流入）
    返回 {'main':..,'big':..,'xlbig':..,'t':'快照时间'} 或 None"""
    # —— L1: ulist 单点快照 ——
    url1 = ('https://push2.eastmoney.com/api/qt/ulist.np/get?fltt=2&secids=1.000001'
            '&fields=f62,f184,f66,f69,f72,f75,f78,f81,f84,f87')
    raw = _http_get(url1, retries=2, timeout=10)
    row = None
    if raw:
        try:
            diff = (json.loads(raw).get('data') or {}).get('diff') or []
            row = diff[0] if isinstance(diff, list) else diff
            main = float(row.get('f62') or 0)
            return {'main': main, 'big': float(row.get('f72') or 0),
                    'xlbig': float(row.get('f66') or 0), 't': bj_now().strftime('%Y-%m-%d %H:%M')}
        except Exception:
            row = None

    # —— L2: push2his 日线（不同服务器群，不受 push2 限流连带影响）——
    url2 = ('https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get'
            '?secid=1.000001&fields1=f1,f2,f3,f7'
            '&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63'
            '&klt=101&lmt=3&ut=b2884a393a59ad64002292a3e90d46a5')
    raw2 = _http_get(url2, retries=3, timeout=12)
    if raw2:
        try:
            ks = (json.loads(raw2).get('data') or {}).get('klines') or []
            if ks:
                p = ks[-1].split(',')
                # f51=日期 f52=主力净流入 f55=大单 f56=超大单
                return {'main': float(p[1]), 'big': float(p[4]), 'xlbig': float(p[5]),
                        't': f'{p[0]} 15:00' if len(p[0]) == 10 else bj_now().strftime('%Y-%m-%d %H:%M')}
        except Exception:
            pass
    return None


def merge_snapshot_into_timeline(flows, snap):
    """把快照并入当日时间序列（供分钟接口限流时使用）。

    时间序列存在远端 .index_fund_state.json 的 timeline 字段，
    每 5 分钟一个点，一天最多 ~48 点，足够识别"峰值→收窄"。"""
    if not snap:
        return flows
    today = snap['t'][:10]
    state = load_state()
    timeline = state.get('timeline') or []
    if state.get('timeline_date') != today:
        timeline = []
    # 同分钟覆盖去重
    timeline = [p for p in timeline if p['t'] != snap['t']]
    timeline.append(snap)
    timeline.sort(key=lambda p: p['t'])
    state['timeline_date'] = today
    state['timeline'] = timeline
    save_state(state)
    print(f'[fund_flow] 快照已并入时间序列（共 {len(timeline)} 点，'
          f'当前 {snap["main"]/YI:+.2f}亿 @ {snap["t"][11:]}）')
    # 只保留今天
    return [p for p in timeline if p['t'].startswith(today)]


def analyze(flows):
    """核心判定。返回 dict（含是否触发/强度/回补量等），无数据返回 None

    兼容两种序列：
      - 分钟全量（240 点，东财 klt=1）
      - 快照稀疏序列（每5分钟一点，降级模式，MIN_POINTS_FLOOR~5 点即可判定）
    """
    if len(flows) < MIN_POINTS_FLOOR:
        return None

    trough_i = min(range(len(flows)), key=lambda i: flows[i]['main'])
    trough = flows[trough_i]
    cur = flows[-1]

    trough_v = trough['main']                 # 负数 = 净流出
    cur_v = cur['main']
    recall = cur_v - trough_v                # 净回补额（正数=回流中）
    depth = abs(trough_v) if trough_v < 0 else 0.0
    cur_yi, trough_yi = cur_v / YI, trough_v / YI
    recall_yi = recall / YI

    # 持续性：判定"最近一段是持续回升而非单点脉冲"
    # 关键区分：
    #   分钟全量序列（点间隔 1 分钟）→ 看最近 SUSTAIN_POINTS(5) 点，跨度约 5 分钟
    #   快照稀疏序列（点间隔 5~30 分钟）→ 只看最后两点，
    #     因为取更多点会把早盘深跌一起框进来，净变化反而为负导致漏报
    span = SUSTAIN_POINTS if len(flows) > SUSTAIN_POINTS * 3 else 2
    tail = flows[-span:]
    tail_rise = tail[-1]['main'] - tail[0]['main']
    tail_rise_yi = tail_rise / YI
    sustain = tail_rise >= SUSTAIN_MIN_YI * YI

    thresh_yi = max(MIN_RECALL_YI, depth * RECALL_RATIO / YI)
    fired = recall_yi >= thresh_yi and sustain and trough_i < len(flows) - 1

    level = 'NONE'
    if fired:
        # ★ v2.1b：强档回到**绝对额** —— 回补 ≥10 亿 = 强回流，5~10 亿 = 弱回流
        #   （v2.3 曾用「资金是否转正 cur_yi >= 0」判强档，需要时改这一行即可切回）
        level = 'STRONG' if recall_yi >= STRONG_RECALL_YI else 'WEAK'

    # 未触发原因（写进 --test 预览文案，避免"强回流"和实际不符）
    fails = []
    if recall_yi < thresh_yi:
        ratio = recall_yi / (depth / YI) * 100 if depth else 0   # depth 单位是元，先换成亿
        if RECALL_RATIO > 0:
            fails.append(f'回补 {recall_yi:.2f} 亿（比例 {ratio:.1f}%）< 阈值 '
                         f'{thresh_yi:.2f} 亿（比例 {RECALL_RATIO * 100:g}%）')
        else:
            fails.append(f'回补 {recall_yi:.2f} 亿（比例 {ratio:.1f}%）'
                         f' < 触发线 {thresh_yi:.2f} 亿')
    if not sustain:
        fails.append(f'末段未持续回升（{tail_rise_yi:+.2f} 亿）')
    if trough_i >= len(flows) - 1:
        fails.append('流出坑就在最新一个点，回补尚未形成')

    return {
        'fired': fired, 'level': level, 'fails': fails,
        'cur_yi': cur_yi, 'trough_yi': trough_yi, 'recall_yi': recall_yi,
        'thresh_yi': thresh_yi, 'depth_yi': depth / YI,
        'trough_t': trough['t'], 'cur_t': cur['t'],
        'trough_i': trough_i, 'sustain': sustain, 'tail_rise_yi': tail_rise_yi,
        'points': len(flows),
        'big_yi': cur['big'] / YI, 'xlbig_yi': cur['xlbig'] / YI,
    }


def build_message(a, ks=None):
    """构建推送文案（全部用实时数值，不写死任何方向）"""
    def _ratio_pct(x):
        d = abs(x.get('depth_yi') or 0)
        return (x['recall_yi'] / d * 100) if d else 0.0

    level_txt = {'NONE': '未触发', 'WEAK': '弱回流', 'STRONG': '强回流'}[a['level']]
    # 资金门槛文案随口径自动切换（比例门开/关两种写法）
    if RECALL_RATIO > 0:
        fund_gate_txt = (f'回补比例 {_ratio_pct(a):.1f}%，'
                         f'及格线 {RECALL_RATIO * 100:g}% = {a["thresh_yi"]:.2f} 亿')
    else:
        fund_gate_txt = (f'回补比例 {_ratio_pct(a):.1f}%；'
                         f'触发线 {a["thresh_yi"]:.2f} 亿、强档 {STRONG_RECALL_YI:g} 亿')
    # 回测事实表按当前区间取值，改常量不会与文案脱节
    FACT = BACKTEST_FACTS.get((KDJ_RANGE_LO, KDJ_RANGE_HI), DEFAULT_FACT)
    dense = a.get('src', '').startswith('分钟')      # 分钟全量 vs 快照稀疏
    pt_txt = f"{a['points']} 个分钟点" if dense else f"{a['points']} 次采样快照"
    span_txt = '近 5 分钟' if dense else '最近一段'
    # 方向描述：当前累计值相对峰值收窄多少
    if a['cur_yi'] < 0:
        head = (f"大盘主力资金**回补 {a['recall_yi']:.2f} 亿**，"
                f"净流出从峰值 {a['trough_yi']:.2f} 亿收窄到 {a['cur_yi']:.2f} 亿"
                f"（仍净流出，未转正）")
        verdict = f"{level_txt}·仍在流出，但跌势在放缓"
    else:
        head = (f"大盘主力资金**由净流出转为净流入 {a['cur_yi']:.2f} 亿**"
                f"（峰值曾净流出 {abs(a['trough_yi']):.2f} 亿，累计回补 {a['recall_yi']:.2f} 亿）")
        verdict = f"{level_txt}·资金已转正，指数层面转强"

    if a['level'] == 'STRONG':
        advice = (f"回补 {a['recall_yi']:.2f} 亿已达强档线 {STRONG_RECALL_YI:g} 亿，"
                  '且末段仍在持续回升，属于**资金层面的真实回升**，'
                  '与大盘 30 分 K 拐头共振时次日反弹概率最高。')
    elif a['level'] == 'WEAK':
        if a['cur_yi'] >= 0:
            advice = (f"回补 {a['recall_yi']:.2f} 亿过触发线但未到强档 {STRONG_RECALL_YI:g} 亿，"
                      '资金虽已转正但力度一般；历史上**流出收窄常是下跌中继**，'
                      '仍以 30 分 K 方向为准。')
        else:
            advice = (f"回补 {a['recall_yi']:.2f} 亿过触发线但未到强档 {STRONG_RECALL_YI:g} 亿，"
                      '幅度还不足以确认趋势反转；历史上**流出收窄常是下跌中继**，'
                      '建议等回补到强档线或 30 分 K 反穿 D 再动手。')
    else:
        fails = a.get('fails') or ['资金门未通过']
        advice = ('**未达到推送条件**（' + '；'.join(fails) + '），本条仅供监控，'
                  '不构成买入提示。')

    lines = [
        '## 大盘资金回流 × 30分K低位\n',
        f"> 时间: {a['cur_t']}（{pt_txt}）\n\n",
        '### 1、位置门（本次信号的真正依据）\n',
    ]
    hold_txt = FACT['hold']
    if ks:
        trend = '已反穿D' if ks['above_d'] else '仍在D下方'
        in_range = KDJ_RANGE_LO <= ks['k'] < KDJ_RANGE_HI
        lines += [
            f"- 上证 {ks['close']:.2f}｜30分 **K={ks['k']}** D={ks['d']} J={ks['j']}"
            f"（{trend}，本bar{'向上' if ks['rising'] else '走弱'}，前bar K={ks['prev_k']}）",
            f"- K {'落在' if in_range else '不在'}目标区间 {KDJ_RANGE_LO:g}~{KDJ_RANGE_HI:g}"
            f"（{FACT['pos']}）\n\n",
        ]
    else:
        lines += ['- 30分KDJ 取数失败（位置门未校验）\n\n']
    lines += [
        '### 2、资金确认\n',
        f"- 净流出峰值：**{a['trough_yi']:.2f} 亿**（{a['trough_t']}）",
        f"- 当前累计：{a['cur_yi']:+.2f} 亿",
        f"- **净回补：{a['recall_yi']:+.2f} 亿**（{fund_gate_txt}）",
        f"- 持续性：{span_txt}净回升 {a['tail_rise_yi']:+.2f} 亿"
        f"{'（成立）' if a['sustain'] else '（未成立）'}",
        f"- 结构：大单 {a['big_yi']:+.2f} 亿 / 超大单 {a['xlbig_yi']:+.2f} 亿\n\n",
        '### 3、结论\n',
        f"**{verdict}。**{head}。{advice}",
        '',
        hold_txt,
        '',
        '👀 **本条是提醒，不是买入指令** —— 三关全过只说明「位置+资金」条件齐了，'
        '是否下手由你自己判断，请结合个股基本面与仓位。',
    ]
    gate_desc = (f'门2 资金回补比例 ≥{RECALL_RATIO * 100:g}%（回补额 ÷ 当日峰值流出）'
                 if RECALL_RATIO > 0 else
                 f'门2 资金回补 ≥{MIN_RECALL_YI:g}亿触发、≥{STRONG_RECALL_YI:g}亿为强档（绝对额）')
    why_txt = ('> 为什么用比例而不是绝对额：日线量级约为盘中同口径的 12 倍，'
               '绝对额跨不了尺度；回测里「回补额≥10亿」触发 75.7% 的交易日且跑输基准。'
               if RECALL_RATIO > 0 else
               '> 口径说明：按你的要求用绝对额（5 亿触发 / 10 亿强档）。日线尺度上'
               '「Δ≥5 亿」与「Δ≥10 亿」筛出的是同一批 2 个样本（无区分度），'
               '但盘中尺度小得多，5 亿与 10 亿在实时判定里确实有区别——'
               '5 亿档灵敏、提醒更多，10 亿档才算真回流。')
    lines += ['', '---',
              f'> 口径 v2.1b：门1 位置 K∈[{KDJ_RANGE_LO:g},{KDJ_RANGE_HI:g})；'
              f'{gate_desc}；'
              f'门3 末段持续回升 ≥{SUSTAIN_MIN_YI:g}亿。三关全过才提醒。',
              why_txt,
              f'> 回测：7-10月 74 个交易日（期间上证 -9.17%，单边下跌市）。{FACT["trig"]}。'
              f'{FACT["freq"]}。',
              '> 提醒性质：历史上盘中「曾触及」低位的口径无超额（次日胜 58.1%），'
              '**收盘停在低位**才算数；本信号为盘中实时触发，天然弱于收盘确认，'
              '按提示而非按承诺看。']
    title = (f"{'✅' if a['cur_yi'] >= 0 else '🔄'} 大盘资金{'转正' if a['cur_yi'] >= 0 else '回流'}·{level_txt}"
             f"｜回补{a['recall_yi']:+.2f}亿｜30分K={ks['k'] if ks else '—'}")
    return title, '\n'.join(lines)


def fetch_kdj_state():
    """拉大盘 30 分 KDJ 当前状态（与前端 fetchIndexKDJ30、index_kdj_watch 同口径）"""
    try:
        from index_kdj_watch import fetch_index_m30
        k = fetch_index_m30()
        if not k:
            return None
        cur, prev = k['cur'], k['prev']
        return {'k': cur['k'], 'd': cur['d'], 'j': cur['j'], 'close': cur['close'],
                'prev_k': prev['k'], 'bar': cur['bar'],
                'rising': cur['k'] > prev['k'], 'above_d': cur['k'] >= cur['d']}
    except Exception:
        return None


def kdj_gate(ks):
    """门1：30 分 K 位置是否落在目标区间。返回 (通过?, 原因文案)"""
    if not KDJ_GATE_ENABLED:
        return True, '位置门已关闭（v1 纯资金模式）'
    if not ks:
        return False, '30分KDJ 取数失败，位置门无法通过（保守不推）'
    k, d = ks['k'], ks['d']
    if not (KDJ_RANGE_LO <= k < KDJ_RANGE_HI):
        if k < KDJ_RANGE_LO:
            where = f'区间下方(<{KDJ_RANGE_LO:g})'
        elif k < 65:
            where = f'中位区({KDJ_RANGE_HI:g}-65)'
        else:
            where = '高位区(>65)'
        return False, f'K={k} 落在{where}，不在目标区间 {KDJ_RANGE_LO:g}~{KDJ_RANGE_HI:g}'
    if KDJ_REQUIRE_K_ABOVE_D and not ks['above_d']:
        return False, f'K={k} ≤ D={d}（方向仍向下），被方向门拦下'
    return True, f'K={k} 落在目标区间 {KDJ_RANGE_LO:g}~{KDJ_RANGE_HI:g}'


def index_kdj_note(ks=None):
    """大盘 30 分 KDJ 状态文案（同一接口口径），共振时更有说服力"""
    if ks is None:
        ks = fetch_kdj_state()
    if not ks:
        return ''
    trend = '已反穿D' if ks['above_d'] else '仍在D下方'
    return (f"- 上证 {ks['close']:.2f}｜30分K={ks['k']} D={ks['d']} J={ks['j']}"
            f"（{trend}，本bar{'向上' if ks['rising'] else '走弱'}，前bar K={ks['prev_k']}）")


def _throttle():
    """跨进程节流：距上次取数不足 THROTTLE_SEC 秒就直接跳过本轮。

    东财限流的根因是**密集请求**。即使 automation 每 5 分钟一次，
    手工调试 + automation 撞在一起仍可能触发。加一层跨进程节流，
    保证任意两次真实取数之间至少间隔 THROTTLE_SEC 秒。
    调试时可用 SKIP_THROTTLE=1 绕过。
    """
    if os.environ.get('SKIP_THROTTLE') == '1' or os.environ.get('FORCE_PUSH') == '1':
        return True
    try:
        st = load_state()
        last = st.get('last_fetch')
        if last:
            gap = (bj_now() - datetime.strptime(last, '%Y-%m-%d %H:%M:%S').replace(
                tzinfo=timezone(timedelta(hours=8)))).total_seconds()
            if gap < THROTTLE_SEC:
                print(f'[fund_flow] 距上次取数仅 {int(gap)}s（<{THROTTLE_SEC}s），本轮跳过')
                return False
    except Exception:
        pass
    st = load_state()
    st['last_fetch'] = bj_now().strftime('%Y-%m-%d %H:%M:%S')
    save_state(st)
    return True


def main():
    test_mode = '--test' in sys.argv
    force = os.environ.get('FORCE_PUSH') == '1'
    now = bj_now()
    today = now.strftime('%Y-%m-%d')

    if not test_mode and not force and not in_trading_time(now):
        print(f'[fund_flow] {now.strftime("%H:%M")} 非交易时段，跳过')
        return

    if not _throttle():
        return

    flows = fetch_market_flow()
    src = f'分钟全量({len(flows)}点)'
    if len(flows) < 10:
        # 分钟接口限流/失败 → 退回快照时间序列
        flows = merge_snapshot_into_timeline([], fetch_snapshot())
        src = f'快照序列({len(flows)}点)'
    a = analyze(flows)
    if not a:
        print('[fund_flow] 资金流数据不足/拉取失败，本轮跳过')
        return
    a['src'] = src

    print(f'[fund_flow] {now.strftime("%H:%M:%S")} 点数={a["points"]} '
          f'当前={a["cur_yi"]:+.2f}亿 峰值={a["trough_yi"]:.2f}亿({a["trough_t"]}) '
          f'回补={a["recall_yi"]:+.2f}亿 阈值={a["thresh_yi"]:.2f}亿 持续={"Y" if a["sustain"] else "N"}')
    print(f'[fund_flow] 资金门: {"通过(" + a["level"] + ")" if a["fired"] else "未通过"}')

    # ---- 门1：30 分 K 位置（v2 核心，回测证实这才是有效过滤器）----
    ks = fetch_kdj_state()
    gate_ok, gate_reason = kdj_gate(ks)
    print(f'[fund_flow] 位置门: {"通过" if gate_ok else "拦下"} —— {gate_reason}')
    if ks:
        print(f'[fund_flow] 30分K={ks["k"]} D={ks["d"]} J={ks["j"]} '
              f'上证={ks["close"]:.2f}（{"向上" if ks["rising"] else "走弱"}，'
              f'{"K≥D" if ks["above_d"] else "K<D"}）')

    if not gate_ok:
        # 位置门拦下时，资金门即使通过也不推（v2 语义：K 位置优先）
        # 注意：KDJ 取数失败也走这里 —— 位置门无法校验时保守不推，避免退回 v1 的无效资金信号
        a['fired'] = False
        a['level'] = 'NONE'

    title, desp = build_message(a, ks)
    if test_mode:
        print('-' * 60)
        print(f'TITLE: {title}')
        print(desp)
        print('-' * 60)
        return
    if not a['fired']:
        return

    # ---- 去重与频控 ----
    state = load_state()
    if state.get('date') != today:
        # 注意：保留 timeline（当日资金流快照序列由 merge_snapshot_into_timeline 写入）
        state = {'date': today, 'push_count': 0, 'last_push': '', 'last_trough_yi': 0.0,
                 'timeline_date': state.get('timeline_date'), 'timeline': state.get('timeline', [])}
    if state.get('push_count', 0) >= MAX_DAILY_PUSH:
        print(f'[fund_flow] 已达当日上限 {MAX_DAILY_PUSH} 次，跳过')
        return
    # 冷却
    if state.get('last_push'):
        try:
            last = datetime.strptime(state['last_push'], '%Y-%m-%d %H:%M')
            if (now.replace(tzinfo=None) - last).total_seconds() < COOLDOWN_MIN * 60:
                print(f'[fund_flow] 距上次推送不足 {COOLDOWN_MIN} 分钟，冷却中')
                return
        except ValueError:
            pass
    # 坑刷新：trough 必须比上次触发时的坑更深 TRENCH_REFRESH_YI 亿
    prev_trough = float(state.get('last_trough_yi') or 0.0)
    if prev_trough and a['trough_yi'] > prev_trough - TRENCH_REFRESH_YI:
        print(f'[fund_flow] 本次坑({a["trough_yi"]:.2f}亿)未比上次({prev_trough:.2f}亿)更深，视为同一波回流')
        return

    try:
        from wechat_notify import send_wechat
        # 注意：这里**不**设 FORCE_PUSH。大盘通道走 push_gate 的共享预算闸门
        # （MKT_PUSH_TYPES 白名单 → 每日 3 条共享上限），必须让闸门真正生效，
        # 否则资金信号会把 Server酱 5 条/日的额度吃光，导致个股两档推不出去。
        ok = send_wechat(title, desp, push_type='index_fundflow')
        if ok:
            state['push_count'] += 1
            state['last_push'] = now.strftime('%Y-%m-%d %H:%M')
            state['last_trough_yi'] = a['trough_yi']
            save_state(state)
            print(f'[fund_flow] ✅ 已推送（今日第{state["push_count"]}次，{a["level"]}）')
        else:
            print('[fund_flow] ⚠️ 推送失败，未记录状态（下轮可重试）')
    except ImportError:
        print('[fund_flow] wechat_notify 不可用，无法推送')


if __name__ == '__main__':
    main()