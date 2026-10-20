#!/usr/bin/env python3
"""大盘30分KDJ 跌后回升监控 v1（2026-10-14）
================================================
用户需求（10/14 凌晨拍板）：大盘30分钟K下跌后**回升**到 K=20 时微信通知。
强调：必须是回升（拐头向上），不是下跌途中。

触发条件（三条全满足才推）：
1. 前一根【已完成】bar 的 K < 20   —— 之前确实处于下跌低位
2. 当前（含进行中bar实时值）K >= 20 —— 回升到 20 上方
3. 当前 K > 前一根完成bar 的 K      —— 方向向上，排除下跌途中擦线

去重/频控：
- 同一根30分bar内只推 1 次（记录触发bar时间戳）
- 当日上限 2 次（防20附近抖动反复穿越刷屏）

数据源（与前端 fetchIndexKDJ30 同源同口径）：
- ifzq.gtimg.cn/appstock/app/kline/mkline?param=sh000001,m30,,320
- 320根30分K全量重放 KDJ(9,3,3)，K/D初值50
- bars[-1]=进行中bar（实时），bars[-2]=前一完成bar
- 注意：必须用 ifzq.gtimg.cn（web.ifzq.gtimg.cn 返301）

运行方式（双路，10/14 起云端兜底）：
1. 本地：交易时段（周一~五 9:35-11:30 / 13:05-15:01）每5分钟，WorkBuddy 定时任务
2. 云端：GitHub Actions cloud-dashboard-update（每30分钟）跑 --cloud，
   电脑关机时兜底；本地/云端通过远端状态文件 .index_kdj_state.json 去重防双推
非交易时段自动跳过。
自检：python3 index_kdj_watch.py --test （只打印状态，不推送不写状态）
     python3 index_kdj_watch.py --cloud （云端模式标记，行为同主流程，token走GITHUB_TOKEN）
"""
import json, os, subprocess, sys
from datetime import datetime, timedelta, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE, '.index_kdj_state.json')

INDEX_CODE = 'sh000001'    # 上证指数
# ★★★ 10/14 用户拍板：大盘回升分两档判定 → 即时推个股清单 ★★★
#   区间 A（超卖回升）：当前 K < 40  时，前bar K<20 且当前 K≥20 触发（抢反弹）
#   区间 B（中位回升）：当前 K 40~65 时，前bar K<45 且当前 K≥45 触发（站稳中位）
#   高位 (K>65) 不推——避免追高，符合用户一贯"回调到位稳健型"偏好
K_LOW_CROSS = 20.0        # 区间A 回升目标线（前bar<20 → 当前≥20）
K_MID_CROSS = 45.0        # 区间B 回升目标线（前bar<45 → 当前≥45）
K_MID_LO = 40.0           # 区间B 下界（区间A 上界）
K_MID_HI = 65.0           # 区间B 上界（>此值完全不推）
MAX_DAILY_PUSH = 4        # 当日推送上限（A+B+重叠保护）
BAR_MIN_LEN = 30           # 重放所需最少bar数

_CLEAN_ENV = {k: v for k, v in os.environ.items() if 'proxy' not in k.lower()}


def bj_now():
    return datetime.now(timezone(timedelta(hours=8)))


def in_trading_time(now):
    """周一~五 9:35-11:30, 13:05-15:01（15:01覆盖收盘最后一次检查）"""
    if now.weekday() >= 5:
        return False
    m = now.hour * 60 + now.minute
    return (9 * 60 + 35 <= m <= 11 * 60 + 31) or (13 * 60 + 5 <= m <= 15 * 60 + 1)


def curl_get(url, timeout=8):
    cmd = ['curl', '-s', '--noproxy', '*', '--connect-timeout', str(timeout),
           '--max-time', str(timeout + 3), '-H', 'User-Agent: Mozilla/5.0',
           '-H', 'Referer: https://gu.qq.com/', url]
    try:
        r = subprocess.run(cmd, capture_output=True, env=_CLEAN_ENV, timeout=timeout + 5)
        return r.stdout
    except Exception:
        return b''


def fetch_index_m30(period='m30'):
    """拉上证分钟K并重放KDJ(9,3,3)（与前端 fetchIndexKDJ30 同口径）
    period: 'm30'（默认，保持向后兼容）或 'm5'（大盘5分KDJ，供结论播报用）
    返回 dict：cur(进行中bar) / prev(前一完成bar) 的 K/D/J + close + bar时间"""
    raw = curl_get(f'https://ifzq.gtimg.cn/appstock/app/kline/mkline'
                   f'?param={INDEX_CODE},{period},,320')
    if not raw:
        return None
    try:
        d = json.loads(raw.decode('utf-8', errors='ignore'))
        bars = d['data'][INDEX_CODE][period]
    except (KeyError, TypeError, json.JSONDecodeError):
        return None
    if not bars or len(bars) < BAR_MIN_LEN:
        return None
    # 腾讯格式 [time, open, close, high, low, vol] → (high, low, close)
    hlc = [(float(b[3]), float(b[4]), float(b[2])) for b in bars]
    Ks, Ds = [50.0], [50.0]
    for i in range(len(hlc)):
        s = max(0, i - 8)
        hh = max(x[0] for x in hlc[s:i + 1])
        ll = min(x[1] for x in hlc[s:i + 1])
        rsv = 50.0 if hh == ll else (hlc[i][2] - ll) / (hh - ll) * 100
        curK = (Ks[i] * 2 + rsv) / 3
        curD = (Ds[i] * 2 + curK) / 3
        Ks.append(curK)
        Ds.append(curD)
    n = len(bars)

    def pack(i):
        k, dd = Ks[i + 1], Ds[i + 1]
        return {'bar': bars[i][0], 'k': round(k, 1), 'd': round(dd, 1),
                'j': round(3 * k - 2 * dd, 1), 'close': float(bars[i][2])}
    return {'cur': pack(n - 1), 'prev': pack(n - 2)}


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {'date': '', 'pushed_bars': [], 'push_count': 0}


def save_state(state):
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ============================================================
# 跨环境去重（10/14 云端兜底版新增）
# 本地(每5分钟)与云端GitHub Actions(每30分钟)共用远端状态文件，
# 避免同一根bar的信号被两端各推一遍。
# 远端路径：.index_kdj_state.json（仅推送成功时写入，低频）
# ============================================================
REPO_API = 'https://api.github.com/repos/Yj66668888/stock-dashboard'
REMOTE_STATE_PATH = '.index_kdj_state.json'


def gh_token():
    t = os.environ.get('GITHUB_TOKEN', '').strip()
    if t:
        return t
    try:
        with open(os.path.join(BASE, '.github_token')) as f:
            return f.read().strip()
    except OSError:
        return None


def gh_api_json(method, url, payload=None):
    """GitHub API（无代理环境），返回 (status, json/None)"""
    token = gh_token()
    if not token:
        return 0, None
    cmd = ['curl', '-s', '--noproxy', '*', '--connect-timeout', '8',
           '--max-time', '15', '-X', method,
           '-H', f'Authorization: token {token}',
           '-H', 'Accept: application/vnd.github.v3+json',
           '-H', 'User-Agent: index-kdj-watch']
    if payload is not None:
        cmd += ['-H', 'Content-Type: application/json',
                '-d', json.dumps(payload)]
    cmd.append(url)
    try:
        r = subprocess.run(cmd, capture_output=True, env=_CLEAN_ENV, timeout=20)
        body = r.stdout.decode('utf-8', errors='ignore')
        try:
            return r.returncode, (json.loads(body) if body else None)
        except json.JSONDecodeError:
            return r.returncode, None
    except Exception:
        return 0, None


def load_remote_state():
    """GET 远端状态文件；404/失败返回 None（容错，不阻断主流程）"""
    _, d = gh_api_json('GET', f'{REPO_API}/contents/{REMOTE_STATE_PATH}')
    if not d or 'content' not in d:
        return None
    try:
        import base64
        return json.loads(base64.b64decode(d['content']).decode('utf-8'))
    except Exception:
        return None


def merge_state(a, b):
    """同日合并去重状态：bars并集、count取max；不同日取新日期的"""
    if not a:
        return b or {'date': '', 'pushed_bars': [], 'push_count': 0}
    if not b:
        return a
    if a.get('date') == b.get('date'):
        bars = list(dict.fromkeys(a.get('pushed_bars', []) + b.get('pushed_bars', [])))
        return {'date': a['date'], 'pushed_bars': bars,
                'push_count': max(a.get('push_count', 0), b.get('push_count', 0))}
    return a if a.get('date', '') > b.get('date', '') else b


def save_remote_state(state):
    """PUT 状态到远端（先GET拿sha，409冲突重试一次）；失败静默——
    只影响跨环境去重精度，不影响推送本身"""
    import base64
    for attempt in range(2):
        _, cur = gh_api_json('GET', f'{REPO_API}/contents/{REMOTE_STATE_PATH}')
        payload = {
            'message': 'index_kdj_watch: state sync (push dedup)',
            'content': base64.b64encode(json.dumps(state, ensure_ascii=False).encode()).decode(),
            'branch': 'main',
        }
        if cur and cur.get('sha'):
            payload['sha'] = cur['sha']
        status, resp = gh_api_json('PUT', f'{REPO_API}/contents/{REMOTE_STATE_PATH}', payload)
        ok = resp and resp.get('commit')
        if ok:
            print(f'[index_kdj] 远端状态已同步（attempt {attempt + 1}）')
            return True
        # 409=sha冲突 → 重试；404=文件刚被删（无sha再PUT一次也会走到这） → 重试
        if status == 0:
            return False  # 网络/token不可用，放弃
    print('[index_kdj] ⚠️ 远端状态同步失败（不影响本次推送，仅去重精度）')
    return False


def check(kdj):
    """大盘30分K回升分档判定（10/14 用户拍板版）。

    返回 (触发?, zone_id, 原因说明)
      zone_id ∈ {'A','B',None}
        A: 当前 K<40 区间，K 从 20 下方回升到 20 上方（超卖回升·抢反弹窗口）
        B: 当前 40≤K≤65 区间，K 从 45 下方回升到 45 上方（中位站稳窗口）
        None: 不触发
    """
    cur, prev = kdj['cur'], kdj['prev']
    ck, pk = cur['k'], prev['k']

    # 高位 (>K_MID_HI) 完全不推——避免追高
    if ck > K_MID_HI:
        return False, None, (f"当前 K={ck} > {K_MID_HI:.0f}（高位，不追）")

    # === 区间 B：中位回升（40 ≤ K ≤ 65），目标线 K_MID_CROSS ===
    if K_MID_LO <= ck <= K_MID_HI:
        c1 = pk < K_MID_CROSS
        c2 = ck >= K_MID_CROSS
        c3 = ck > pk
        if c1 and c2 and c3:
            return True, 'B', (f"区间B【中位站稳】前bar K={pk}<{K_MID_CROSS:.0f} "
                                f"→ 当前 K={ck}≥{K_MID_CROSS:.0f} 且向上 +{ck - pk:.1f}")
        return False, None, (f"区间B不满足: c1={c1}(前bar<{K_MID_CROSS:.0f}={pk}) "
                             f"c2={c2}(当前≥{K_MID_CROSS:.0f}={ck}) c3={c3}")

    # === 区间 A：超卖回升（K < 40），目标线 K_LOW_CROSS ===
    if ck < K_MID_LO:
        c1 = pk < K_LOW_CROSS
        c2 = ck >= K_LOW_CROSS
        c3 = ck > pk
        if c1 and c2 and c3:
            return True, 'A', (f"区间A【超卖回升】前bar K={pk}<{K_LOW_CROSS:.0f} "
                                f"→ 当前 K={ck}≥{K_LOW_CROSS:.0f} 且向上 +{ck - pk:.1f}")
        return False, None, (f"区间A不满足: c1={c1}(前bar<{K_LOW_CROSS:.0f}={pk}) "
                             f"c2={c2}(当前≥{K_LOW_CROSS:.0f}={ck}) c3={c3}")

    # K 处于 (K_MID_HI, K_MID_LO) 不可能的取值兜底（理论上 K 是连续值）
    return False, None, f"K={ck} 处于未覆盖区间"


def pick_low_bounce_stocks(zone_id):
    """从 dynamic_stocks.json 池子筛「低位启动型」个股，配套大盘回升推送。

    筛选规则（项目一贯稳健偏好）：
      - 30分 KDJ K 值 < 30（低位）
      - 30分方向向上（kdj30Dir = '↑'，启动中）
      - 5分 K < 35（5分钟也低位未追高）或 5分 J < 60
      - score >= 35（综合评分够格）
      - dailyFlow >= -2000 万（资金未大幅流出，可接受小幅流出）
      - final_rank 升序排，取 top 5

    池子为空或读不到时返回空 list，调用方按"只推大盘、不附个股"降级处理。
    """
    pool_path = os.path.join(BASE, 'dynamic_stocks.json')
    try:
        pool = json.load(open(pool_path)).get('stocks', [])
    except Exception:
        return []

    ZONE_LABEL = {'A': '（抢反弹窗口·优先选超卖票）', 'B': '（中位站稳·优先选启动型票）'}

    candidates = []
    for s in pool:
        try:
            k30 = float(s.get('kdj30') or 999)
            kd30_dir = s.get('kdj30Dir', '')
            k5 = float(s.get('kdj5') or 999)
            kd5_j = float(s.get('kdj5J') or 999)
            score = float(s.get('score') or 0)
            daily_flow = float(s.get('dailyFlow') or 0)
            rank = float(s.get('final_rank') or 9999)
        except (TypeError, ValueError):
            continue

        if zone_id == 'A':
            # 抢反弹：30K 越低越好（但 < 30）；资金限制放宽到 -8000 万
            # （超卖票本来就在流出期，方向↑才是关键；过滤掉爆量出逃票即可）
            ok = k30 < 30 and kd30_dir in ('↑', '↗') and score >= 35 and daily_flow >= -8000
        else:
            # 中位站稳：30K 处于 30-65 + 方向向上，5分也低位
            ok = (K_MID_LO <= k30 <= K_MID_HI and kd30_dir == '↑'
                  and (k5 < 35 or kd5_j < 60)
                  and score >= 35 and daily_flow >= -2000)

        if ok:
            candidates.append({
                'code': s.get('code', ''),
                'name': s.get('name', ''),
                'kdj30': k30,
                'kdj5': k5,
                'score': score,
                'dailyFlow': daily_flow,
                'final_rank': rank,
                'industry': s.get('industry', ''),
                'reason': s.get('reason', ''),
            })
    candidates.sort(key=lambda x: x['final_rank'])
    return candidates[:5]


def main():
    test_mode = '--test' in sys.argv
    now = bj_now()
    today = now.strftime('%Y-%m-%d')

    if not test_mode and not in_trading_time(now):
        print(f'[index_kdj] {now.strftime("%H:%M")} 非交易时段，跳过')
        return

    kdj = fetch_index_m30()
    if not kdj:
        print('[index_kdj] 30分K拉取失败，本轮跳过')
        return

    cur, prev = kdj['cur'], kdj['prev']
    fired, zone, why = check(kdj)
    print(f'[index_kdj] {now.strftime("%H:%M:%S")} 大盘30分KDJ: '
          f'当前bar={cur["bar"]} K={cur["k"]} D={cur["d"]} J={cur["j"]} '
          f'收盘={cur["close"]:.2f} | 前bar={prev["bar"]} K={prev["k"]}')
    zone_str = f' [区间{zone}]' if zone else ''
    print(f'[index_kdj] 判定: {"🔔 触发回升信号" if fired else "未触发"}{zone_str} — {why}')

    if test_mode:
        return
    if not fired:
        return

    # 去重：本地+远端合并状态（本地每5分钟与云端每30分钟共用，防双推）
    state = merge_state(load_state(), load_remote_state())
    if state.get('date') != today:
        state = {'date': today, 'pushed_bars': [], 'push_count': 0,
                 'pushed_zones': []}
    if cur['bar'] in state['pushed_bars']:
        print(f'[index_kdj] bar {cur["bar"]} 已推送过，跳过')
        return
    if zone in state.get('pushed_zones', []):
        print(f'[index_kdj] 区间{zone} 今日已推送过，跳过')
        return
    if state['push_count'] >= MAX_DAILY_PUSH:
        print(f'[index_kdj] 已达当日上限 {MAX_DAILY_PUSH} 次，跳过')
        return

    # ★★★ 个股清单：从 dynamic_stocks.json 筛低位启动型 ★★★
    stocks = pick_low_bounce_stocks(zone)

    # 推送（FORCE_PUSH=1 绕过时间闸门，频控由本脚本自己管）
    try:
        from wechat_notify import send_wechat
        os.environ['FORCE_PUSH'] = '1'
        target = K_LOW_CROSS if zone == 'A' else K_MID_CROSS
        title = f"🚨 大盘拐头·区间{zone}｜K={cur['k']}→{target}·{len(stocks)}只低位启动"
        lines = [f"## 大盘30分钟KDJ 分档回升｜即时个票清单\n",
                 f"> 时间: {now.strftime('%Y-%m-%d %H:%M:%S')} | 区间 **{zone}**\n",
                 f"> 上证指数: **{cur['close']:.2f}**\n\n",
                 f"### 区间判定\n",
                 f"- 前一完成bar ({prev['bar'][:8]} {prev['bar'][8:]}): K=**{prev['k']}**（{'超卖低位' if zone=='A' else '中位'}）",
                 f"- 当前实时bar ({cur['bar'][:8]} {cur['bar'][8:]}): K=**{cur['k']}** ≥ {target:.0f}",
                 f"- 方向: **回升**（较前bar +{cur['k'] - prev['k']:.1f} 点）",
                 f"- D={cur['d']} J={cur['j']}\n\n",
                 f"### 🎯 仪表盘低位启动型（{len(stocks)}只）\n"]

        if not stocks:
            lines += [f"> ⚠️ 池子里当前没有匹配的低位启动型票（数据可能滞后）",
                      f"> 仅大盘信号，个股决策仍按原仪表盘五档推送节奏\n\n"]
        else:
            for i, s in enumerate(stocks, 1):
                flow_dir = '🔴出' if s['dailyFlow'] < 0 else '🟢入'
                lines += [f"{i}. **{s['name']}** ({s['code']}) {s['industry']}",
                          f"   30K={s['kdj30']:.1f}↑ · 5K={s['kdj5']:.1f} · 评{s['score']:.0f} · {flow_dir}{abs(s['dailyFlow']):.0f}万"]
            lines += [""]
        notes = (f"- 区间{zone} 目标线={target:.0f}："
                 f"{'A=抢反弹窗口，B=站稳中位可关注；' if zone=='A' else 'B=站稳中位，A=抢反弹窗口；'}"
                 f"K 重新回落穿越阈值警惕二次探底")
        lines += [f"### 操作提示\n", notes,
                  f"- 配套 {MAX_DAILY_PUSH} 档/日 上限（防高位追）",
                  f"- 仪表盘标准推送（10/11/13/14/14:40）仍按时触发"]
        lines += ["\n---\n",
                  f"个体决策请结合个股30分K/资金流向，回调到位+资金回流型优先。"]

        ok = send_wechat(title, '\n'.join(lines), push_type='index_rebound_v2')
        if ok:
            state['pushed_bars'].append(cur['bar'])
            state['push_count'] += 1
            state.setdefault('pushed_zones', []).append(zone)
            save_state(state)          # 本地文件
            save_remote_state(state)   # 远端同步（云端/本地双路去重，失败不阻断）
            print(f'[index_kdj] ✅ 已推送（今日第{state["push_count"]}次，区间{zone}，'
                  f'bar={cur["bar"]}，附{len(stocks)}只个股）')
        else:
            print('[index_kdj] ⚠️ 推送失败，未记录状态（下轮可重试）')
    except ImportError:
        print('[index_kdj] wechat_notify 不可用，无法推送')


if __name__ == '__main__':
    main()
