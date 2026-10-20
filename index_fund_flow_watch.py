#!/usr/bin/env python3
"""大盘资金「流出→回流」监测 v1（2026-10-20）
================================================
用户原话（10/20 16:00）：
  「比如说今天的大盘在下跌，资金已经流出了90亿了，然后过了一会资金变成了85亿，
    这种是不是也可以相当于大盘在开始回升，像这种情况你能监测不，
    就是大盘资金在流出，然后他们可能又在一点点的流入」

判定思路（累计净流出的"变浅"逻辑）：
  1. 东财上证指数**分钟级**资金流（klt=1，全天 240 个点），取"主力净流入"累计曲线
  2. 当日最低点 = 流出峰值（trough）。trough 值为负 = 净流出
  3. 回流判定 = 当前累计值 - trough 值 = 净回补额
       阈值 = max(绝对下限, trough深度的比例)   —— 深跌市要求回补更多才承认
  4. 持续性 = 最近若干点单调不降（排除单点脉冲/插针）
  5. 去重 = 同一波回流只推一次；要么 trough 刷新为更深的坑，要么距上次推送 >= COOLDOWN_MIN

两档强度（对应用户"一点点流入"的模糊描述）：
  WEAK  弱回流：刚收窄，多半只是跌速放缓，不构成买点
  STRONG 强回流：回补量足够大 + 持续，可与大盘30分KDJ回升共振 → 真回升概率高

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

# ---------- 判定阈值（全部可在下方独立调整）----------
MIN_RECALL_YI = 2.0          # 净回补绝对下限：2 亿。低于此数视为噪声，不推
RECALL_RATIO = 0.08          # 相对回流比例下限：当日流出峰值的 8%
SUSTAIN_POINTS = 5           # 持续性：最近 N 个点（分钟序列≈5分钟；快照序列降级用全部点）
MIN_POINTS_FLOOR = 5         # 序列最短点数（快照稀疏模式 5 点即可判定）
SUSTAIN_MIN_YI = 1.0         # 持续性：N 点内净回升至少 1 亿
STRONG_RECALL_YI = 8.0       # 强回流：净回补 >= 8 亿
COOLDOWN_MIN = 30            # 冷却：距上次推送至少 30 分钟才算新一波
TRENCH_REFRESH_YI = 5.0      # 坑刷新：trough 再深 5 亿以上才允许重推
MAX_DAILY_PUSH = 2           # 当日上限（与 index_kdj_watch 合计受 Server酱 5 条约束）
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
        level = 'STRONG' if recall_yi >= STRONG_RECALL_YI else 'WEAK'

    return {
        'fired': fired, 'level': level,
        'cur_yi': cur_yi, 'trough_yi': trough_yi, 'recall_yi': recall_yi,
        'thresh_yi': thresh_yi, 'depth_yi': depth / YI,
        'trough_t': trough['t'], 'cur_t': cur['t'],
        'trough_i': trough_i, 'sustain': sustain, 'tail_rise_yi': tail_rise_yi,
        'points': len(flows),
        'big_yi': cur['big'] / YI, 'xlbig_yi': cur['xlbig'] / YI,
    }


def build_message(a, idx_note=''):
    """构建推送文案（全部用实时数值，不写死任何方向）"""
    level_txt = '弱回流' if a['level'] == 'WEAK' else '强回流'
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
        advice = ('回补量已超过阈值且末段仍在持续回升，属于**资金层面的真实回升**，'
                  '与大盘 30 分 K 拐头共振时反弹概率最高。')
    else:
        advice = ('回补幅度还不足以确认趋势反转，历史上**流出收窄常是下跌中继**，'
                  '建议等资金转正或 30 分 K 反穿 D 再动手。')

    lines = [
        '## 大盘资金流向变化监测\n',
        f"> 时间: {a['cur_t']}（{pt_txt}）\n\n",
        '### 1、资金变化\n',
        f"- 净流出峰值：**{a['trough_yi']:.2f} 亿**（{a['trough_t']}）",
        f"- 当前累计：{a['cur_yi']:+.2f} 亿",
        f"- **净回补：{a['recall_yi']:+.2f} 亿**（阈值 {a['thresh_yi']:.2f} 亿）",
        f"- 持续性：{span_txt}净回升 {a['tail_rise_yi']:+.2f} 亿"
        f"{'（成立）' if a['sustain'] else '（未成立）'}",
        f"- 结构：大单 {a['big_yi']:+.2f} 亿 / 超大单 {a['xlbig_yi']:+.2f} 亿\n\n",
        f'### 2、结论\n',
        f"**{verdict}。**{head}。{advice}",
    ]
    if idx_note:
        lines += ['', f'### 3、大盘 30 分 K\n', idx_note]
    lines += ['', '---',
              '> 口径：东财上证指数主力净流入累计曲线；'
              '回补额 = 当前值 − 当日最低值；阈值 = max(2亿, 峰值深度×8%)，且末段须持续回升。',
              '> 提示：资金收窄≠见底，历史上多为下跌中继，务必配合 30 分 KDJ 趋势方向判断。']
    title = (f"{'✅' if a['cur_yi'] >= 0 else '🔄'} 大盘资金{'转正' if a['cur_yi'] >= 0 else '回流'}·{level_txt}"
             f"｜回补{a['recall_yi']:+.2f}亿｜当前{a['cur_yi']:+.2f}亿")
    return title, '\n'.join(lines)


def index_kdj_note():
    """顺手带上大盘 30 分 KDJ 状态（同一接口口径），共振时更有说服力"""
    try:
        from index_kdj_watch import fetch_index_m30
        k = fetch_index_m30()
        if not k:
            return ''
        cur, prev = k['cur'], k['prev']
        rising = cur['k'] > prev['k']
        above_d = cur['k'] >= cur['d']
        trend = '已反穿D' if above_d else '仍在D下方'
        return (f"- 上证 {cur['close']:.2f}｜30分K={cur['k']} D={cur['d']} J={cur['j']}"
                f"（{trend}，本bar{'向上' if rising else '走弱'}，前bar K={prev['k']}）")
    except Exception:
        return ''


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
    print(f'[fund_flow] 判定: {"🔔 触发(" + a["level"] + ")" if a["fired"] else "未触发回流"}')

    title, desp = build_message(a, index_kdj_note())
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