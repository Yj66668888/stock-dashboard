#!/usr/bin/env python3
"""全市场低位重扫 v2 — 带换手率硬过滤
=====================================
流程：
  1. 从 daily_predictions.json (全市场扫描) 筛低位池：RSI<65 + 20日跌>3% + 评分>35
  2. 推算昨日真实换手率 = 昨日成交量(腾讯日线) × 100股 × 现价 / 流通市值(f21)
     （已验证与东财 f168 全天值完全一致，误差<0.01%）
  3. 硬过滤：2.0% ≤ 换手率 ≤ 7.0%
  4. 日K朝上过滤（2026-09-03新增）：收盘<MA5(阴跌)硬踢；收盘≥MA5且MA5上行(↑)优先，
     仅站上MA5(→)在排序靠后补足——"回调到位且日线开始朝上"
  5. 腾讯实时30分KDJ过滤：K<70（追高硬踢），今日涨幅<4%（已涨起来的踢）
  6. 腾讯实时5分KDJ过滤：K<70（5分钟也已追高的踢），低位上拐加分（2026-08-27新增：
     解决"推送时5分钟已在高点"——选票要求30分钟+5分钟双重低位）
  7. 按评分+K30位置加分+5分加分排序，取前30只 + 4固定票（2026-09-03: 26→30）
  7. 写 dynamic_stocks.json + 注入 deploy/index.html（固定票原样保留）

数据源（全部 subprocess curl --noproxy '*' 绕过本地代理）：
  - push2delay.eastmoney.com ulist.np : 价格/涨跌幅/流通市值
  - ifzq.gtimg.cn fqkline/mkline     : 日线昨日成交量、30分/5分K线
"""
import json, re, subprocess, os, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
HTML = os.path.join(BASE, 'deploy', 'index.html')
DYN_FILE = os.path.join(BASE, 'dynamic_stocks.json')
PRED_FILE = os.path.join(BASE, 'daily_predictions.json')

_CLEAN_ENV = {k: v for k, v in os.environ.items() if 'proxy' not in k.lower()}

# ---- 硬条件 ----
TURNOVER_MIN, TURNOVER_MAX = 2.0, 7.0   # 换手率区间（用户规则）
K30_HARD_KICK = 70.0                     # 30分K追高硬踢
K5_HARD_KICK = 70.0                      # 5分K追高硬踢（2026-08-27新增：双重低位确认）
TODAY_CHG_MAX = 4.0                      # 今日已涨幅超此值视为涨起来了
RSI_MAX, DROP20D_MAX, SCORE_MIN = 65.0, -3.0, 35.0
SELECT_N = 30   # 2026-09-03: 26→30（用户要求加量），+4固定票 = 池子34只

PINNED = ['sh603067', 'sh600105', 'sh601126', 'sz000688']


def curl_get(url, referer=None, retries=3, timeout=10):
    cmd = ['curl', '-s', '--noproxy', '*', '--connect-timeout', str(timeout),
           '-H', 'User-Agent: Mozilla/5.0']
    if referer:
        cmd += ['-H', f'Referer: {referer}']
    cmd.append(url)
    for _ in range(retries):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 5, env=_CLEAN_ENV)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout
        except Exception:
            pass
        time.sleep(0.3)
    return None


def curl_json(url, referer=None):
    raw = curl_get(url, referer)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def to_secid(code):
    p = code[-6:]
    return ('1.' if p.startswith('6') else '0.') + p


def fetch_quotes_batch(codes):
    """批量拉价格/今日涨幅/流通市值（ulist.np，60只一批）"""
    out = {}
    for i in range(0, len(codes), 60):
        chunk = codes[i:i + 60]
        secids = ','.join(to_secid(c) for c in chunk)
        d = curl_json(f'https://push2delay.eastmoney.com/api/qt/ulist.np/get'
                      f'?secids={secids}&fields=f12,f14,f2,f3,f21,f62&fltt=2&invt=2')
        for it in (d.get('data') or {}).get('diff') or []:
            try:
                out[str(it.get('f12'))] = {
                    'name': it.get('f14'),
                    'price': float(it.get('f2')) if it.get('f2') != '-' else None,
                    'chg_today': float(it.get('f3')) if it.get('f3') != '-' else None,
                    'float_mv': float(it.get('f21')) if it.get('f21') != '-' else None,
                }
            except (TypeError, ValueError):
                pass
    return out


def fetch_yest_volume(code):
    """腾讯日线 → 昨日成交量（手）+ 已走完的收盘序列（2026-09-03改：兼供日线趋势判断）"""
    d = curl_json(f'https://ifzq.gtimg.cn/appstock/app/fqkline/get?param={code},day,,,12,qfq',
                  referer='https://gu.qq.com/')
    try:
        k = d['data'][code].get('qfqday') or d['data'][code].get('day')
        if not k or len(k) < 2:
            return None
        # 剔除今日进行中的K线（盘前拉取时今日bar尚不存在，天然安全；盘中拉取时剔除）
        today = datetime.now().strftime('%Y-%m-%d')
        done = [b for b in k if not str(b[0]).startswith(today)]
        if len(done) < 2:
            return None
        yest = done[-1]  # 最后一根已走完的K线（盘前=昨日，盘中=昨日）
        closes = [float(b[2]) for b in done]
        return {'date': yest[0], 'vol_shou': float(yest[5]), 'closes': closes}
    except (KeyError, TypeError, IndexError, ValueError):
        return None


def daily_trend(closes):
    """日线趋势判定（2026-09-03新增：日K朝上过滤，优先↑不足时放宽→补足）。
    up:   收盘 ≥ MA5 且 MA5 走平/上行（MA5 ≥ 昨日MA5）→ 回调到位且日线开始朝上（优先选）
    flat: 收盘 ≥ MA5 但 MA5 仍下行 → 站上5日线但趋势未确认（↑不足30只时补足）
    down: 收盘 < MA5 → 日线仍在5日线下方，阴跌未止（硬踢）
    返回 (是否站上MA5, 标签'↑'/'→'/'↓')；数据不足返回 (False, '')"""
    if not closes or len(closes) < 6:
        return False, ''
    ma5 = sum(closes[-5:]) / 5.0
    ma5_prev = sum(closes[-6:-1]) / 5.0
    close = closes[-1]
    if close < ma5:
        return False, '↓'
    if ma5 >= ma5_prev:
        return True, '↑'
    return True, '→'


def drop_incomplete_bar(bars):
    """丢弃盘中未走完的当根K线（2026-08-27新增）。
    腾讯mkline时间戳为bar终点标注：bar时间 > 当前时间 → 该bar未走完，K值临时不可靠"""
    if not bars:
        return bars
    try:
        bar_time = datetime.strptime(bars[-1][0][:12], '%Y%m%d%H%M')
    except (ValueError, TypeError, IndexError):
        return bars
    if bar_time > datetime.now():
        return bars[:-1]
    return bars


def calc_kdj_m30(code):
    """腾讯30分K线 → KDJ(9,3,3)，返回最新K/D及最近3根序列（判断方向用）"""
    d = curl_json(f'https://ifzq.gtimg.cn/appstock/app/kline/mkline?param={code},m30,,300',
                  referer='https://gu.qq.com/')
    try:
        bars = drop_incomplete_bar(d['data'][code]['m30'])
    except (KeyError, TypeError):
        return None
    if len(bars) < 20:
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
    tail = series[-3:]
    return {'k': round(k, 1), 'd': round(d_, 1), 'bars': len(bars),
            'k_prev': round(tail[-2][0], 1), 'd_prev': round(tail[-2][1], 1)}


def classify_trend(r):
    """K30趋势分层：
       up     = 上升中（K>D）
       prep_up= 准备上升（K≤D 但 乖离收敛 或 K上拐）
       down   = 仍在下跌
    """
    gap, gap_prev = r['k'] - r['d'], r['k_prev'] - r['d_prev']
    if r['k'] > r['d']:
        return 'up'
    if gap > gap_prev or r['k'] > r['k_prev']:
        return 'prep_up'
    return 'down'


TIER_NAMES = {0: 'K≤45上升', 1: 'K≤45待升', 2: 'K≤45下跌',
              3: 'K45-60上升', 4: 'K45-60待升', 5: 'K45-60下跌', 6: 'K60-70补足'}


def classify_tier(k, trend):
    """优先级分层：K≤45+方向 优先；不够名额才逐层放宽到 45-60、60-70"""
    if k >= K30_HARD_KICK:
        return None  # 硬踢
    base = 0 if k <= 45 else (3 if k <= 60 else 6)
    if base == 6:
        return 6
    return base + {'up': 0, 'prep_up': 1, 'down': 2}[trend]


def kdj_bonus(k):
    """K30位置加分（仅作层内排序微调，层优先级见 classify_tier）"""
    if k >= K30_HARD_KICK:
        return -999
    if k >= 60:
        return -6
    if k >= 45:
        return -2
    if k >= 25:
        return 3   # 最佳区间
    return 1


def calc_kdj_m5(code):
    """腾讯5分K线 → KDJ(9,3,3)，返回最新K/D/J及前值（判断5分钟是否低位/是否已拉起）"""
    d = curl_json(f'https://ifzq.gtimg.cn/appstock/app/kline/mkline?param={code},m5,,120',
                  referer='https://gu.qq.com/')
    try:
        bars = drop_incomplete_bar(d['data'][code]['m5'])
    except (KeyError, TypeError):
        return None
    if len(bars) < 20:
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
    tail = series[-3:]
    return {'k': round(k, 1), 'd': round(d_, 1), 'j': round(j, 1),
            'k_prev': round(tail[-2][0], 1), 'bars': len(bars)}


def kdj5_bonus(r5):
    """5分钟KDJ位置加分（2026-08-27新增）：
       低位且上拐 = 最佳介入点；接近高位 = 降权（避免推送时5分钟已在顶点）"""
    if r5['k'] >= K5_HARD_KICK:
        return -999
    if r5['k'] >= 55:
        return -4      # 5分钟接近高位，降权
    if r5['k'] >= 40:
        return 0
    if r5['k'] < 30 and r5['k'] > r5['k_prev']:
        return 4       # 低位上拐，最佳
    return 2           # 低位但尚未上拐


def main():
    # ---- 1. 低位池 ----
    pred = json.load(open(PRED_FILE))
    print(f"全市场扫描底数: {pred['scan_date']} | {len(pred['all_results'])}只")
    pool = []
    for s in pred['all_results']:
        m = s.get('metrics', {})
        rsi, drop, score = m.get('rsi'), m.get('drop_20d'), s.get('total_score')
        if rsi is None or drop is None or score is None:
            continue
        if rsi < RSI_MAX and drop < DROP20D_MAX and score > SCORE_MIN:
            bare = s['code']
            full_code = ('sh' if bare.startswith('6') else 'sz') + bare
            pool.append({
                'code': full_code, 'name': s['name'], 'score': score,
                'rsi': round(rsi, 1), 'drop_20d': round(drop, 1),
                'consecutive': m.get('consecutive', 0),
                'close': m.get('latest_close'),
            })
    print(f"低位池: {len(pool)}只 (RSI<{RSI_MAX} + 跌20d>{-DROP20D_MAX}% + 评分>{SCORE_MIN})")

    # ---- 2. 批量行情（价格/今日涨幅/流通市值） ----
    quotes = fetch_quotes_batch([s['code'] for s in pool])
    print(f"行情获取: {len(quotes)}/{len(pool)}")

    # ---- 3. 昨日成交量 → 真实换手率 ----
    def turnover_worker(s):
        v = fetch_yest_volume(s['code'])
        return s['code'], v
    vols = {}
    with ThreadPoolExecutor(max_workers=10) as ex:
        futs = [ex.submit(turnover_worker, s) for s in pool]
        for fu in as_completed(futs):
            c, v = fu.result()
            if v:
                vols[c] = v

    for s in pool:
        q = quotes.get(s['code'][-6:])
        v = vols.get(s['code'])
        s['turnover'] = None
        if q and v and q['price'] and q['float_mv']:
            s['turnover'] = round(v['vol_shou'] * 100 * q['price'] / q['float_mv'] * 100, 2)
            s['chg_today'] = q['chg_today']
            s['close'] = q['price']  # 用实时价
        # 日线趋势（2026-09-03新增：日K朝上过滤，↓硬踢，↑优先→补足）
        s['dailyUp'], s['dailyTrend'] = daily_trend(v.get('closes')) if v else (False, '')
    got = [s for s in pool if s['turnover'] is not None]
    print(f"换手率推算: {len(got)}/{len(pool)} | 分布: "
          f"<2%: {sum(1 for s in got if s['turnover'] < 2)} | "
          f"2-7%: {sum(1 for s in got if 2 <= s['turnover'] <= 7)} | "
          f">7%: {sum(1 for s in got if s['turnover'] > 7)}")

    # ---- 4. 换手率硬过滤 + 今日涨幅过滤 + 日K朝上过滤（2026-09-03新增）----
    step1 = [s for s in got if TURNOVER_MIN <= s['turnover'] <= TURNOVER_MAX]
    step1 = [s for s in step1 if s.get('chg_today') is None or s.get('chg_today') < TODAY_CHG_MAX]
    n_before = len(step1)
    # ↓(收盘<MA5,日线阴跌)硬踢；↑(MA5也上行)优先，→(仅站上MA5)在排序时靠后补足
    step1 = [s for s in step1 if s.get('dailyTrend') in ('↑', '→')]
    n_up = sum(1 for s in step1 if s.get('dailyTrend') == '↑')
    print(f"换手{TURNOVER_MIN}-{TURNOVER_MAX}% + 今日涨幅<{TODAY_CHG_MAX}%: {n_before}只")
    print(f"日K站上MA5(踢↓阴跌): {len(step1)}只 = ↑朝上{n_up} + →待确认{len(step1)-n_up}（剔除{n_before-len(step1)}只日线仍弱的）")

    # ---- 5. 实时30分KDJ过滤 ----
    def kdj_worker(s):
        r = calc_kdj_m30(s['code'])
        return s['code'], r
    kdjs = {}
    with ThreadPoolExecutor(max_workers=10) as ex:
        futs = [ex.submit(kdj_worker, s) for s in step1]
        for fu in as_completed(futs):
            c, r = fu.result()
            if r:
                kdjs[c] = r

    # ---- 5b. 实时5分KDJ（2026-08-27新增：5分钟也需低位，避免推送即高点） ----
    def kdj5_worker(s):
        r = calc_kdj_m5(s['code'])
        return s['code'], r
    kdj5s = {}
    with ThreadPoolExecutor(max_workers=10) as ex:
        futs = [ex.submit(kdj5_worker, s) for s in step1]
        for fu in as_completed(futs):
            c, r = fu.result()
            if r:
                kdj5s[c] = r

    final = []
    for s in step1:
        r = kdjs.get(s['code'])
        if not r:
            continue
        if r['k'] >= K30_HARD_KICK:
            continue  # K>=70 硬踢
        trend = classify_trend(r)
        tier = classify_tier(r['k'], trend)
        if tier is None:
            continue
        r5 = kdj5s.get(s['code'])
        if not r5:
            continue
        if r5['k'] >= K5_HARD_KICK:
            continue  # 5分钟K>=70 也已追高，硬踢（双重低位确认）
        dir_bonus = {'up': 2, 'prep_up': 1, 'down': 0}[trend]
        s['kdj30'] = r['k']
        s['kdj30Dir'] = '↑' if trend == 'up' else ('↗' if trend == 'prep_up' else '↓')
        s['kdj30Trend'] = trend
        s['kdj30Tier'] = tier
        s['kdj5'] = r5['k']
        s['kdj5Dir'] = '↑' if r5['k'] > r5['k_prev'] else '↓'
        s['kdj5J'] = r5['j']
        s['final_rank'] = s['score'] + kdj_bonus(r['k']) + kdj5_bonus(r5) + dir_bonus \
            + (3 if s.get('dailyTrend') == '↑' else 0)  # 日线朝上加分(2026-09-03)
        final.append(s)

    # ---- 分层放宽取满：T0(K≤45上升) → T1 → T2 → 45-60各层 → 60-70补足 ----
    final.sort(key=lambda x: (x['kdj30Tier'], -x['final_rank']))
    tier_stat = {}
    for s in final:
        tier_stat.setdefault(s['kdj30Tier'], 0)
        tier_stat[s['kdj30Tier']] += 1
    print(f"30分K<{K30_HARD_KICK} + 5分K<{K5_HARD_KICK}: {len(final)}只，分层分布:")
    for t in sorted(tier_stat):
        print(f"  T{t} {TIER_NAMES[t]}: {tier_stat[t]}只")

    selected = final[:SELECT_N]
    used_tiers = sorted({s['kdj30Tier'] for s in selected})
    max_tier = max(used_tiers) if used_tiers else 0
    n_up_sel = sum(1 for s in selected if s.get('dailyTrend') == '↑')
    print(f"\n=== 选中 {len(selected)} 只（放宽到 T{max_tier}: {TIER_NAMES.get(max_tier, '-')}）| 日线↑{n_up_sel} + →{len(selected)-n_up_sel} ===")
    for s in selected:
        print(f"  [T{s['kdj30Tier']}] {s['name']}({s['code']}): 评分{s['score']} "
              f"换手{s['turnover']}% K30={s['kdj30']}{s['kdj30Dir']} 日线{s.get('dailyTrend', '?')} "
              f"K5={s['kdj5']}{s['kdj5Dir']}(J{s['kdj5J']}) "
              f"今涨{s.get('chg_today')}% 20日{s['drop_20d']}%")

    # ---- 6. 写 dynamic_stocks.json ----
    dyn = {'scan_date': datetime.now().strftime('%Y-%m-%d'), 'total': len(selected),
           'filter': f'RSI<{RSI_MAX}/跌20d>3%/评分>35/换手{TURNOVER_MIN}-{TURNOVER_MAX}%/今涨<{TODAY_CHG_MAX}%/'
                     f'日K站上MA5(收盘<MA5阴跌硬踢,MA5上行↑优先,2026-09-03新增)/'
                     f'K30≤45+上升优先,不足放宽至45-60、60-70,K30≥70硬踢/'
                     f'K5≥70硬踢,低位上拐加分(双重低位确认)',
           'stocks': selected}
    json.dump(dyn, open(DYN_FILE, 'w'), ensure_ascii=False, indent=2)

    # ---- 7. 注入 HTML（固定票原样保留） ----
    html = open(HTML).read()
    m = re.search(r'(const STOCKS\s*=\s*)(\[.*?\])(\s*;\s*\n)', html, re.S)
    if not m:
        print('❌ 未找到 STOCKS 数组'); sys.exit(1)
    old_arr = json.loads(m.group(2))
    pinned_entries = [s for s in old_arr if s['code'] in PINNED]
    if len(pinned_entries) != len(PINNED):
        print(f'⚠️ 固定票只找到{len(pinned_entries)}/{len(PINNED)}，缺失的不动其他票')

    new_entries = []
    # 旧池中按代码保留资金流数据（盘前/开盘初期 f62 拉不到当日值时兜底，避免覆盖成0）
    old_flow = {s['code']: s for s in old_arr if s.get('dailyFlow') not in (None, 0, '', '--')}
    for s in selected:
        e = {k: s[k] for k in ('code', 'name', 'score', 'rsi', 'drop_20d', 'consecutive', 'close') if s.get(k) is not None}
        e['turnover'] = s['turnover']
        e['kdj30'] = s['kdj30']
        e['kdj30Dir'] = s['kdj30Dir']
        e['kdj30Trend'] = s['kdj30Trend']
        e['kdj30Tier'] = s['kdj30Tier']
        e['dailyTrend'] = s.get('dailyTrend', '')  # 日线趋势: ↑朝上(新硬过滤)
        e['kdj5'] = s['kdj5']
        e['kdj5Dir'] = s['kdj5Dir']
        e['kdj5J'] = s['kdj5J']
        prev = old_flow.get(s['code'])
        if prev:
            # 继承上一轮的资金流数据（enrich 拿到当日新值后会覆盖）
            e['dailyFlow'] = prev.get('dailyFlow')
            e['flow5d'] = prev.get('flow5d')
            e['flow10d'] = prev.get('flow10d')
            e['flowDate'] = prev.get('flowDate')
        else:
            e['dailyFlow'] = 0
            e['flow5d'] = 0
            e['flow10d'] = 0
        e['flow_5d'] = 0
        e['flow_10d'] = 0
        new_entries.append(e)
    # 固定票跟在后面（保留原有全部字段）
    new_entries.extend(pinned_entries)
    new_json = json.dumps(new_entries, ensure_ascii=False, indent=2)
    html = html[:m.start(2)] + new_json + html[m.end(2):]
    open(HTML, 'w').write(html)
    print(f"\n✅ 已注入 {len(new_entries)} 只（{len(selected)}新选 + {len(pinned_entries)}固定）→ {HTML}")
    print("下一步: enrich_missing_fields.py → precompute_daily_indicators.py → 排序推送")


if __name__ == '__main__':
    main()
