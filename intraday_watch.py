#!/usr/bin/env python3
"""盘中5分钟低位监控 v1（2026-08-27）
=====================================
方案B：解决"推送时5分钟已在高点"——盘中每5分钟扫仪表盘30只票的5分钟KDJ，
当某只票 5分钟K<25 超卖 + K/J拐头向上（低吸窗口）时，立即微信推送。

触发条件（全部满足才推）：
1. 5分钟KDJ的K < 25（超卖低位）
2. 拐头：K > 前一根K 或 J > 前一根J（开始回升）
3. 当日涨幅 < 4%（排除已大涨的）
4. 该票当日未推送过（去重）

频控（Server酱额度保护）：
- 每只票每天最多推1次
- 当天总推送条数 ≤ 3 条（保留额度给 bottom/prelaunch 正式推送）
- 同批多只票合并为一条推送

运行方式：交易时段（周一~五 9:35-11:30 / 13:05-14:55）每5分钟跑一次，
由本地 WorkBuddy 定时任务触发（电脑开着才生效）。非交易时段自动跳过。

推送绕过 wechat_notify 的时间闸门（FORCE_PUSH=1），去重/频控由本脚本自己管。
"""
import json, re, subprocess, os, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
HTML = os.path.join(BASE, 'deploy', 'index.html')
STATE_FILE = os.path.join(BASE, '.intraday_watch_state.json')

MAX_DAILY_PUSH = 3        # 每天最多推送条数（Server酱额度保护）
K5_LOW = 25.0             # 5分钟K超卖阈值
CHG_MAX = 4.0             # 当日涨幅上限（已涨起来的不要）
CONCURRENCY = 3           # 腾讯并发≤3，防风控

sys.path.insert(0, BASE)
_CLEAN_ENV = {k: v for k, v in os.environ.items() if 'proxy' not in k.lower()}


def bj_now():
    return datetime.now(timezone(timedelta(hours=8)))


def in_trading_time(now):
    """仅交易时段（周一~周五 9:35-11:30, 13:05-14:55）"""
    if now.weekday() >= 5:
        return False
    m = now.hour * 60 + now.minute
    return (9 * 60 + 35 <= m <= 11 * 60 + 30) or (13 * 60 + 5 <= m <= 14 * 60 + 55)


def curl_get(url, referer=None, timeout=8):
    """返回原始字节（qt.gtimg.cn 是GBK编码，需调用方手动解码）"""
    cmd = ['curl', '-s', '--noproxy', '*', '--connect-timeout', str(timeout),
           '--max-time', str(timeout + 3), '-H', 'User-Agent: Mozilla/5.0']
    if referer:
        cmd += ['-H', f'Referer: {referer}']
    cmd.append(url)
    try:
        r = subprocess.run(cmd, capture_output=True, env=_CLEAN_ENV,
                           timeout=timeout + 5)
        return r.stdout
    except Exception:
        return b''


def load_stocks():
    """从 deploy/index.html 读 STOCKS 数组（30只）"""
    try:
        html = open(HTML, encoding='utf-8').read()
    except OSError:
        return []
    m = re.search(r'(const STOCKS\s*=\s*)(\[.*?\])(\s*;\s*\n)', html, re.S)
    if not m:
        return []
    try:
        arr = json.loads(m.group(2))
    except json.JSONDecodeError:
        return []
    return [{'code': s.get('code', ''), 'name': s.get('name', '')} for s in arr if s.get('code')]


def fetch_m5_kdj(code):
    """腾讯5分K线 → KDJ(9,3,3)，返回最新K/D/J/前值/最新价"""
    raw = curl_get(f'https://ifzq.gtimg.cn/appstock/app/kline/mkline?param={code},m5,,120',
                   referer='https://gu.qq.com/')
    if not raw:
        return None
    try:
        d = json.loads(raw.decode('utf-8', errors='ignore'))
        bars = d['data'][code]['m5']
    except (KeyError, TypeError, json.JSONDecodeError):
        return None
    if not bars or len(bars) < 20:
        return None
    # [time, open, close, high, low, vol] → (high, low, close)
    hlc = [(float(b[3]), float(b[4]), float(b[2])) for b in bars]
    k, d_ = 50.0, 50.0
    series = []
    for i in range(len(hlc)):
        hi = max(x[0] for x in hlc[max(0, i - 8):i + 1])
        lo = min(x[1] for x in hlc[max(0, i - 8):i + 1])
        c = hlc[i][2]
        rsv = 50.0 if hi == lo else (c - lo) / (hi - lo) * 100
        k = (2 / 3) * k + (1 / 3) * rsv
        d_ = (2 / 3) * d_ + (1 / 3) * k
        series.append((k, d_))
    j = 3 * k - 2 * d_
    j_prev = 3 * series[-2][0] - 2 * series[-2][1]
    return {'k': round(k, 1), 'd': round(d_, 1), 'j': round(j, 1),
            'k_prev': round(series[-2][0], 1), 'j_prev': round(j_prev, 1),
            'price': float(bars[-1][2])}


def fetch_realtime_chg(codes):
    """腾讯实时行情批量 → {code: 涨跌幅%}（qt.gtimg.cn 用~分隔）"""
    out = {}
    for i in range(0, len(codes), 40):   # 分批，防URL过长
        batch = codes[i:i + 40]
        raw = curl_get(f'https://qt.gtimg.cn/q={",".join(batch)}', referer='https://gu.qq.com/')
        if not raw:
            continue
        try:
            text = raw.decode('gbk', errors='ignore')
        except Exception:
            continue
        for line in text.split(';'):
            line = line.strip()
            if '="' not in line:
                continue
            try:
                secid = line.split('="')[0].split('_')[-1]
                parts = line.split('="')[1].split('~')
                if len(parts) < 5:
                    continue
                price = float(parts[3])
                prev = float(parts[4])
                chg = (price - prev) / prev * 100 if prev else 0
                out[secid] = round(chg, 2)
            except (ValueError, IndexError):
                continue
    return out


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {'pushed_codes': {}, 'push_days': {}}


def save_state(state):
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def main():
    now = bj_now()
    today = now.strftime('%Y-%m-%d')

    if not in_trading_time(now):
        print(f'[intraday] {now.strftime("%H:%M")} 非交易时段，跳过')
        return

    stocks = load_stocks()
    if not stocks:
        print('[intraday] 未找到 STOCKS，退出')
        return
    print(f'[intraday] {now.strftime("%Y-%m-%d %H:%M:%S")} 扫描 {len(stocks)} 只票的5分钟KDJ...')

    # 1. 并行拉5分钟KDJ（并发≤3 防腾讯风控）
    kdjs = {}
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as ex:
        futs = {ex.submit(fetch_m5_kdj, s['code']): s for s in stocks}
        for fu in as_completed(futs):
            s = futs[fu]
            try:
                r = fu.result()
                if r:
                    kdjs[s['code']] = r
            except Exception:
                pass
    print(f'[intraday] 5分钟KDJ获取: {len(kdjs)}/{len(stocks)}')

    # 2. 实时涨幅
    chgs = fetch_realtime_chg([s['code'] for s in stocks if s['code'] in kdjs])

    # 3. 触发判定
    state = load_state()
    pushed_today = state.get('push_days', {}).get(today, 0)
    triggered = []
    for s in stocks:
        code = s['code']
        r = kdjs.get(code)
        if not r:
            continue
        chg = chgs.get(code, 0)
        # 条件1: 5分钟K超卖低位
        if r['k'] >= K5_LOW:
            continue
        # 条件2: 拐头向上（K或J回升）
        turning = r['k'] > r['k_prev'] or r['j'] > r['j_prev']
        if not turning:
            continue
        # 条件3: 当日涨幅限制
        if chg >= CHG_MAX:
            continue
        # 条件4: 该票当日未推送过
        if state.get('pushed_codes', {}).get(code) == today:
            continue
        # 频控: 当天总条数上限
        if pushed_today + len(triggered) >= MAX_DAILY_PUSH:
            print(f'[intraday] 已达当日推送上限 {MAX_DAILY_PUSH} 条，跳过剩余信号')
            break
        triggered.append({'code': code, 'name': s['name'], **r, 'chg': chg})

    if not triggered:
        print(f'[intraday] 无触发信号')
        return

    # 4. 合并推送（FORCE_PUSH=1 绕过时间闸门，频控由本脚本负责）
    try:
        from wechat_notify import send_wechat
        os.environ['FORCE_PUSH'] = '1'
        names = '/'.join(t['name'] for t in triggered)
        title = f"5分低位拐头{len(triggered)}只: {names}"
        lines = [f"## 盘中5分钟低位监控\n",
                 f"> 时间: {now.strftime('%H:%M:%S')} | 5分钟K<{K5_LOW:.0f}超卖+拐头=低吸窗口\n",
                 f"> 涨幅<{CHG_MAX}% | 每票当日仅推一次\n\n", "---\n\n"]
        for i, t in enumerate(triggered, 1):
            lines.append(f"### {i}. {t['name']} {t['code']}")
            lines.append(f"- 现价: {t['price']:.2f} | 今日: **{t['chg']:+.2f}%**")
            lines.append(f"- 5分KDJ: K={t['k']:.1f} D={t['d']:.1f} J={t['j']:.1f}（前K={t['k_prev']:.1f}）")
            lines.append(f"- 状态: {'K上拐' if t['k'] > t['k_prev'] else ''}"
                         f"{' J回升' if t['j'] > t['j_prev'] else ''} 超卖低吸区，勿追高\n")
        ok = send_wechat(title, '\n'.join(lines), push_type='intraday')
        if ok:
            # 5. 更新状态（只有真正推送成功才记）
            state.setdefault('pushed_codes', {})
            state.setdefault('push_days', {})
            for t in triggered:
                state['pushed_codes'][t['code']] = today
            state['push_days'][today] = pushed_today + 1
            save_state(state)
            print(f'[intraday] ✅ 已推送 {len(triggered)} 只（今日第{pushed_today + 1}条）')
        else:
            print(f'[intraday] ⚠️ 推送失败，未记录状态（下轮可重试）')
    except ImportError:
        print(f'[intraday] wechat_notify 不可用，无法推送: {[t["name"] for t in triggered]}')


if __name__ == '__main__':
    main()
