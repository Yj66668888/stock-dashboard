#!/usr/bin/env python3
"""
全量预计算34只票所有指标，直接注入STOCKS数组和所有缓存。
数据源：Sina K-line API + East Money capital flow + Tencent quotes
"""
import json, re, subprocess, time, sys, os

HTML_PATH = os.path.join(os.path.dirname(__file__), 'deploy_davis', 'index.html')

def curl_get(url, timeout=10):
    try:
        r = subprocess.run(
            ['curl', '-sL', '--max-time', str(timeout), url,
             '-H', 'User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'],
            capture_output=True, text=True, timeout=timeout + 5
        )
        return r.stdout
    except:
        return ''

def to_secid(code):
    num = code.replace('sh', '').replace('sz', '').replace('bj', '')
    if code.startswith('sh'):
        return '1.' + num
    return '0.' + num

# ============ 技术指标计算 ============

def calc_kdj(klines, n=9):
    """KDJ(9,3,3) - klines: list of [time,open,high,low,close,volume]"""
    if not klines or len(klines) < n:
        return None
    k, d = 50.0, 50.0
    for i in range(len(klines)):
        hn = max(float(kl[2]) for kl in klines[max(0, i - n + 1):i + 1])
        ln = min(float(kl[3]) for kl in klines[max(0, i - n + 1):i + 1])
        c = float(klines[i][4])
        rsv = (c - ln) / (hn - ln) * 100 if hn != ln else 50
        k = 2 / 3 * k + 1 / 3 * rsv
        d = 2 / 3 * d + 1 / 3 * k
    j = 3 * k - 2 * d
    prev_k = 50.0
    if len(klines) > n:
        pk, pd = 50.0, 50.0
        for i in range(len(klines) - 1):
            hn = max(float(kl[2]) for kl in klines[max(0, i - n + 1):i + 1])
            ln = min(float(kl[3]) for kl in klines[max(0, i - n + 1):i + 1])
            c = float(klines[i][4])
            rsv = (c - ln) / (hn - ln) * 100 if hn != ln else 50
            pk = 2 / 3 * pk + 1 / 3 * rsv
            pd = 2 / 3 * pd + 1 / 3 * pk
        prev_k = pk
    cross = '上升' if k > prev_k else '下降'
    return {'k': round(k, 2), 'd': round(d, 2), 'j': round(j, 2), 'cross': cross}

def calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    if len(gains) < period:
        return None
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period
    if al == 0:
        return 100.0
    return round(100 - 100 / (1 + ag / al), 2)

def calc_macd(closes, fast=12, slow=26, signal=9):
    if len(closes) < slow + signal:
        return None
    ef = sum(closes[:fast]) / fast
    es = sum(closes[:slow]) / slow
    difs = []
    mf, ms = 2 / (fast + 1), 2 / (slow + 1)
    for i in range(len(closes)):
        if i >= fast:
            ef = (closes[i] - ef) * mf + ef
        if i >= slow:
            es = (closes[i] - es) * ms + es
        if i >= slow - 1:
            difs.append(ef - es)
    if len(difs) < signal:
        return None
    dea = sum(difs[:signal]) / signal
    md = 2 / (signal + 1)
    for i in range(signal, len(difs)):
        dea = (difs[i] - dea) * md + dea
    d = difs[-1]
    cross = '金叉' if d > dea else '死叉'
    return f'DIF:{d:+.2f} DEA:{dea:+.2f} {cross}'

def calc_ma_arrangement(closes):
    if len(closes) < 20:
        return None, None, None, None
    ma5 = sum(closes[-5:]) / 5
    ma10 = sum(closes[-10:]) / 10
    ma20 = sum(closes[-20:]) / 20
    last = closes[-1]
    if ma5 > ma10 > ma20:
        ma_text = '多头排列'
    elif ma5 < ma10 < ma20:
        ma_text = '空头排列'
    else:
        ma_text = '交叉震荡'
    return ma_text, round(ma5, 2), round(ma10, 2), round(ma20, 2)

def calc_trend(closes):
    """趋势判断"""
    if len(closes) < 10:
        return None, None
    last = closes[-1]
    ma5 = sum(closes[-5:]) / 5
    ma10 = sum(closes[-10:]) / 10
    pct_5d = (last - closes[-6]) / closes[-6] * 100 if len(closes) >= 6 and closes[-6] != 0 else 0
    if ma5 > ma10 and last > ma5:
        trend = '上升趋势'
    elif ma5 < ma10 and last < ma5:
        trend = '下降趋势'
    elif abs(last - ma5) / ma5 < 0.01:
        trend = '横盘震荡'
    else:
        trend = '震荡偏弱' if last < ma5 else '震荡偏强'
    return trend, round(pct_5d, 2)

def calc_vol_signal(vols, closes):
    """量价信号：极度缩量/缩量/正常/放量"""
    if len(vols) < 6:
        return None
    avg_vol = sum(vols[-6:-1]) / 5
    if avg_vol == 0:
        return None
    ratio = vols[-1] / avg_vol
    last_chg = closes[-1] - closes[-2]
    if ratio < 0.5:
        return '极度缩量'
    elif ratio < 0.8:
        return '缩量'
    elif ratio > 2.0:
        return '放量'
    elif ratio > 1.5:
        return '温和放量'
    else:
        return '正常'

def calc_beta(stock_closes, index_closes):
    """计算Beta和相关性"""
    n = min(len(stock_closes), len(index_closes))
    if n < 20:
        return None, None, None
    sc = stock_closes[-n:]
    ic = index_closes[-n:]
    # 收益率
    sr = [(sc[i] - sc[i-1]) / sc[i-1] for i in range(1, n) if sc[i-1] != 0]
    ir = [(ic[i] - ic[i-1]) / ic[i-1] for i in range(1, n) if ic[i-1] != 0]
    if len(sr) < 15 or len(ir) < 15:
        return None, None, None
    ms = sum(sr) / len(sr)
    mi = sum(ir) / len(ir)
    cov = sum((sr[i] - ms) * (ir[i] - mi) for i in range(len(sr))) / len(sr)
    vs = sum((r - ms) ** 2 for r in sr) / len(sr)
    vi = sum((r - mi) ** 2 for r in ir) / len(ir)
    if vs == 0 or vi == 0:
        return None, None, None
    beta = cov / vi
    corr = cov / (vs ** 0.5 * vi ** 0.5)
    # 5日同步率
    sync = sum(1 for i in range(1, min(6, len(sr))) if (sr[-i] > 0) == (ir[-i] > 0)) / min(5, len(sr) - 1)
    return round(beta, 2), round(corr, 2), round(sync * 100, 0)

def calc_support(lows, closes):
    """支撑价"""
    if len(lows) < 20:
        return None, None
    support = min(lows[-20:])
    last = closes[-1]
    dist = round((last - support) / last * 100, 1) if last > 0 else None
    return round(support, 2), dist

def calc_open_rate(opens, closes):
    """30日高开率"""
    n = min(30, len(opens) - 1)
    if n <= 0:
        return None
    up = sum(1 for i in range(1, n + 1) if opens[i] > closes[i - 1])
    return round(up / n * 100, 1)

def format_flow_wan(val):
    """格式化资金流（万元→显示文本）"""
    if val is None:
        return '--'
    abs_v = abs(val)
    sign = '+' if val >= 0 else '-'
    if abs_v >= 10000:
        return f'{sign}{abs_v / 10000:.2f}亿'
    return f'{sign}{abs_v:.0f}万'

# ============ API数据获取 ============

def fetch_sina_kline(sina_code, scale, datalen):
    """新浪K线API"""
    url = f'https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol={sina_code}&scale={scale}&datalen={datalen}'
    raw = curl_get(url, 8)
    if not raw:
        return None
    try:
        data = json.loads(raw)
        # 格式: [{day,open,high,low,close,volume}, ...]
        return [[d['day'], float(d['open']), float(d['high']), float(d['low']),
                 float(d['close']), float(d['volume'])] for d in data]
    except:
        return None

def fetch_tencent_quotes(codes):
    """批量获取腾讯实时行情"""
    url = f'https://qt.gtimg.cn/q={",".join(codes)}'
    raw = curl_get(url, 10)
    if not raw:
        return {}
    results = {}
    for code in codes:
        var_name = f'v_{code}'
        idx = raw.find(var_name + '=')
        if idx < 0:
            continue
        start = raw.find('"', idx) + 1
        end = raw.find('"', start)
        if start <= 0 or end <= start:
            continue
        line = raw[start:end]
        fields = line.split('~')
        if len(fields) < 50:
            continue
        try:
            price = float(fields[3]) if fields[3] else 0
            prev_close = float(fields[4]) if fields[4] else 0
            change_pct = ((price - prev_close) / prev_close * 100) if prev_close > 0 else 0
            turnover = float(fields[38]) if fields[38] else 0
            pe = float(fields[39]) if fields[39] and fields[39] != '' else 0
            pb = float(fields[46]) if len(fields) > 46 and fields[46] and fields[46] != '' else 0
            total_mv = float(fields[45]) if len(fields) > 45 and fields[45] and fields[45] != '' else 0
            results[code] = {
                'price': price,
                'changePct': round(change_pct, 2),
                'turnover': round(turnover, 2),
                'pe': pe if pe > 0 else None,
                'pb': pb if pb > 0 else None,
                'total_mv': total_mv,
            }
        except:
            pass
    return results

def fetch_eastmoney_flow_batch(secids):
    """东方财富批量资金流"""
    url = f'https://push2.eastmoney.com/api/qt/ulist.np/get?secids={",".join(secids)}&fields=f12,f14,f62,f184,f66,f69,f72,f75&fltt=2&invt=2'
    raw = curl_get(url, 10)
    if not raw:
        return {}
    # JSONP wrapper: jQuery...({...})
    try:
        # Remove JSONP wrapper
        json_start = raw.find('(')
        json_end = raw.rfind(')')
        if json_start >= 0 and json_end > json_start:
            raw = raw[json_start + 1:json_end]
        data = json.loads(raw)
        results = {}
        if not data or 'data' not in data or not data['data'] or 'diff' not in data['data']:
            return {}
        for item in data['data']['diff']:
            num = str(item.get('f12', ''))
            code = ('sh' + num) if (num.startswith('6') or num.startswith('9')) else ('sz' + num)
            main_net = item.get('f62')
            main_pct = item.get('f184')
            results[code] = {
                'mainNetInflow': main_net,
                'mainFlowPct': main_pct,
            }
        return results
    except:
        return {}

def fetch_eastmoney_flow_daily(secid, lmt=12):
    """东方财富日线资金流（5日/10日累计）"""
    url = f'https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get?secid={secid}&klt=101&lmt={lmt}&fltt=2&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56,f57'
    raw = curl_get(url, 8)
    if not raw:
        return None, None
    try:
        json_start = raw.find('(')
        json_end = raw.rfind(')')
        if json_start >= 0 and json_end > json_start:
            raw = raw[json_start + 1:json_end]
        data = json.loads(raw)
        if not data or 'data' not in data or 'klines' not in data['data']:
            return None, None
        flows = [float(k.split(',')[1]) for k in data['data']['klines']]
        f5 = sum(flows[-5:]) / 10000 if len(flows) >= 5 else None  # 转万元
        f10 = sum(flows[-10:]) / 10000 if len(flows) >= 10 else None
        return f5, f10
    except:
        return None, None

# ============ 主流程 ============

def main():
    print('=== 全量预计算34只票所有指标 ===')

    # 1. 读取HTML获取股票列表
    with open(HTML_PATH, 'r') as f:
        html = f.read()

    m = re.search(r'const\s+STOCKS\s*=\s*(\[.*?\])\s*;', html, re.S)
    stocks = json.loads(m.group(1))
    print(f'STOCKS: {len(stocks)} 只')

    all_codes = [s['code'] for s in stocks]
    sina_codes = all_codes  # Sina uses same format (sh600418, sz000688)
    secids = [to_secid(c) for c in all_codes]

    # 2. 批量获取实时行情
    print('\n[1/5] 获取实时行情（腾讯API批量）...')
    quotes = fetch_tencent_quotes(all_codes)
    print(f'  获取到 {len(quotes)} 只票的行情')
    for code, q in list(quotes.items())[:3]:
        print(f'  {code}: price={q["price"]}, chg={q["changePct"]}%, turnover={q["turnover"]}%, pe={q.get("pe")}, pb={q.get("pb")}')

    # 3. 批量获取当日资金流
    print('\n[2/5] 获取当日主力资金流（东方财富批量API）...')
    flow_today = fetch_eastmoney_flow_batch(secids)
    print(f'  获取到 {len(flow_today)} 只票的资金流')

    # 4. 获取指数K线（Beta计算用）
    print('\n[3/5] 获取上证指数日线（Beta基准）...')
    index_klines = fetch_sina_kline('sh000001', 240, 60)
    index_closes = [k[4] for k in index_klines] if index_klines else []
    print(f'  指数日线: {len(index_closes)} 根')

    # 5. 逐只获取K线并计算指标
    print('\n[4/5] 逐只获取K线+计算指标...')
    results = {}
    for i, code in enumerate(all_codes):
        name = next((s['name'] for s in stocks if s['code'] == code), code)
        print(f'  [{i+1}/{len(all_codes)}] {code} {name}...', end='', flush=True)

        # 5min K线 → KDJ
        kl5 = fetch_sina_kline(code, 5, 300)
        kd5 = calc_kdj(kl5) if kl5 else None

        # 日K线 → RSI/MACD/MA/trend/volSignal/beta/support/openRate
        kld = fetch_sina_kline(code, 240, 100)
        if kld:
            closes = [k[4] for k in kld]
            opens = [k[1] for k in kld]
            highs = [k[2] for k in kld]
            lows = [k[3] for k in kld]
            vols = [k[5] for k in kld]

            rsi = calc_rsi(closes)
            macd = calc_macd(closes)
            ma_text, ma5, ma10, ma20 = calc_ma_arrangement(closes)
            trend, trend_pct = calc_trend(closes)
            vol_signal = calc_vol_signal(vols, closes)
            beta, corr, sync = calc_beta(closes, index_closes) if index_closes else (None, None, None)
            support_price, support_dist = calc_support(lows, closes)
            open_rate = calc_open_rate(opens, closes)
            drop20d = round((closes[-1] - closes[-20]) / closes[-20] * 100, 2) if len(closes) >= 20 else 0
            avg_vol = sum(vols[-6:-1]) / 5 if len(vols) >= 6 else 1
            vol_ratio = round(vols[-1] / avg_vol, 2) if avg_vol > 0 else 1.0
        else:
            rsi = macd = ma_text = ma5 = ma10 = ma20 = None
            trend = trend_pct = vol_signal = None
            beta = corr = sync = None
            support_price = support_dist = open_rate = None
            drop20d = 0
            vol_ratio = 1.0

        # 5日/10日资金流
        f5, f10 = fetch_eastmoney_flow_daily(to_secid(code), 12)

        # 行情
        q = quotes.get(code, {})

        # 当日资金流
        ft = flow_today.get(code, {})
        main_net = ft.get('mainNetInflow')
        main_pct = ft.get('mainFlowPct')
        main_flow_str = format_flow_wan(main_net / 10000) if main_net is not None else '--'

        # 组装结果
        result = {
            'kd5_k': kd5['k'] if kd5 else None,
            'kd5_d': kd5['d'] if kd5 else None,
            'kd5_cross': kd5['cross'] if kd5 else None,
            'kd30_k': kd5['k'] if kd5 else None,  # 用5min近似30min
            'kd30_d': kd5['d'] if kd5 else None,
            'kd30_cross': kd5['cross'] if kd5 else None,
            'kd60_k': kd5['k'] if kd5 else None,  # 用5min近似60min
            'kd60_d': kd5['d'] if kd5 else None,
            'kd60_cross': kd5['cross'] if kd5 else None,
            'rsi': rsi,
            'macd': macd,
            'ma': ma_text,
            'ma5': ma5,
            'ma10': ma10,
            'ma20': ma20,
            'maAbove': ma20 is not None and closes[-1] > ma20 if kld else False,
            'trend': trend,
            'trendPct': trend_pct,
            'volSignal': vol_signal,
            'volRatio': vol_ratio,
            'drop20d': drop20d,
            'supportPrice': support_price,
            'supportDist': support_dist,
            'openRate30d': open_rate,
            'openRateTotal': open_rate,
            'openRateScore': 2 if open_rate and open_rate >= 40 else 1,
            'price': q.get('price'),
            'changePct': q.get('changePct'),
            'turnover': q.get('turnover'),
            'pe': q.get('pe'),
            'pb': q.get('pb'),
            # mainFlow/mainFlowToday/mainFlowPct/mainFlowDate 不预烤
            # 由浏览器端 fetchCapitalFlow() 每30秒实时获取，避免显示过期数据
            'flow5d_wan': f5,
            'flow10d_wan': f10,
            'flow5d': format_flow_wan(f5) if f5 is not None else None,
            'flow10d': format_flow_wan(f10) if f10 is not None else None,
            'beta': beta,
            'betaCategory': ('强跟随' if beta and beta > 1.0 and corr and corr > 0.7
                             else '跟随' if beta and beta > 0.6 and corr and corr > 0.5
                             else '弱关联' if beta and beta > 0.3
                             else '独立' if beta and beta > 0
                             else '逆势' if beta and beta < 0
                             else None),
            'syncRate': sync,
        }

        # 移除None值
        result = {k: v for k, v in result.items() if v is not None}
        results[code] = result

        ok_count = len(result)
        print(f' {ok_count} fields')

        # 速率控制
        time.sleep(0.3)

    # 6. 注入HTML
    print(f'\n[5/5] 注入数据到HTML...')

    # 6a. 更新STOCKS数组 — 把预计算数据合并到每只票
    for s in stocks:
        code = s['code']
        if code in results:
            s.update(results[code])

    new_stocks_json = json.dumps(stocks, ensure_ascii=False)
    html = re.sub(
        r'const\s+STOCKS\s*=\s*\[.*?\]\s*;',
        f'const STOCKS = {new_stocks_json};',
        html,
        count=1,
        flags=re.S
    )
    print(f'  STOCKS数组: 已更新 ({len(stocks)} 只)')

    # 6b. 更新KDJ_FALLBACK — 用新数据替换旧数据
    kdj_fallback = {}
    for code, r in results.items():
        fb = {}
        for k in ['kd5_k', 'kd5_d', 'kd30_k', 'kd30_d', 'kd30_cross',
                   'kd60_k', 'kd60_d', 'kd60_cross', 'macd', 'ma', 'ma5', 'ma10',
                   'ma20', 'maAbove', 'rsi', 'drop20d', 'volRatio',
                   'openRate30d', 'openRateTotal', 'openRateScore',
                   'pe', 'pb',
                   'supportPrice', 'supportDist', 'trend', 'trendPct', 'volSignal']:
            if k in r:
                fb[k] = r[k]
        kdj_fallback[code] = fb

    kdj_json = json.dumps(kdj_fallback, ensure_ascii=False)
    # 替换KDJ_FALLBACK定义
    html = re.sub(
        r'var KDJ_FALLBACK = \{.*?\};',
        f'var KDJ_FALLBACK = {kdj_json};',
        html,
        count=1,
        flags=re.S
    )
    # 删除Object.assign(KDJ_FALLBACK, ...) — 不再需要
    html = re.sub(
        r'Object\.assign\(KDJ_FALLBACK,\s*\{.*?\}\);',
        '/* pinned stocks merged into KDJ_FALLBACK */',
        html,
        count=1,
        flags=re.S
    )
    print(f'  KDJ_FALLBACK: 已替换 ({len(kdj_fallback)} 只)')

    # 6c. 更新KDJ_PRECOMPUTED
    kdj_pre = {}
    for code, r in results.items():
        num = code.replace('sh', '').replace('sz', '').replace('bj', '')
        if 'kd5_k' in r:
            kdj_pre[num] = {
                'k': r['kd5_k'],
                'd': r.get('kd5_d', 50),
                'direction': r.get('kd5_cross', '上升')
            }
    kdj_pre_json = json.dumps(kdj_pre, ensure_ascii=False)
    html = re.sub(
        r'const KDJ_PRECOMPUTED = \{\};',
        f'const KDJ_PRECOMPUTED = {kdj_pre_json};',
        html,
        count=1
    )
    print(f'  KDJ_PRECOMPUTED: 已填充 ({len(kdj_pre)} 只)')

    # 6d. 更新MARKET_CORR_CACHE
    mc_cache = {}
    for code, r in results.items():
        num = code.replace('sh', '').replace('sz', '').replace('bj', '')
        if 'beta' in r:
            mc_cache[num] = {
                'beta': r['beta'],
                'corr': r.get('syncRate', 0),
                'category': r.get('betaCategory', ''),
                'syncRate': r.get('syncRate', 0)
            }
    mc_json = json.dumps(mc_cache, ensure_ascii=False)
    html = re.sub(
        r'const MARKET_CORR_CACHE = \{\};',
        f'const MARKET_CORR_CACHE = {mc_json};',
        html,
        count=1
    )
    print(f'  MARKET_CORR_CACHE: 已填充 ({len(mc_cache)} 只)')

    # 6e. 更新CAPITAL_FLOW_CACHE (仅保留5日/10日资金流，当日资金由浏览器实时获取)
    cf_cache = {}
    for code, r in results.items():
        cf_cache[code] = {}
        if 'flow5d_wan' in r:
            cf_cache[code]['flow_5d_wan'] = r['flow5d_wan']
        if 'flow10d_wan' in r:
            cf_cache[code]['flow_10d_wan'] = r['flow10d_wan']
    cf_json = json.dumps(cf_cache, ensure_ascii=False)
    html = re.sub(
        r'const CAPITAL_FLOW_CACHE = \{.*?\};',
        f'const CAPITAL_FLOW_CACHE = {cf_json};',
        html,
        count=1,
        flags=re.S
    )
    print(f'  CAPITAL_FLOW_CACHE: 已更新 ({len(cf_cache)} 只)')

    # 写回HTML
    with open(HTML_PATH, 'w') as f:
        f.write(html)
    print(f'\n=== 完成! HTML已更新: {HTML_PATH} ===')

    # 验证
    with open(HTML_PATH, 'r') as f:
        html2 = f.read()
    m2 = re.search(r'const\s+STOCKS\s*=\s*(\[.*?\])\s*;', html2, re.S)
    stocks2 = json.loads(m2.group(1))
    check_fields = ['rsi', 'kd5_k', 'kd30_k', 'kd60_k', 'macd', 'ma', 'trend',
                    'volSignal', 'mainFlow', 'turnover', 'pe', 'pb',
                    'beta', 'supportPrice', 'volRatio', 'openRate30d', 'flow5d']
    print(f'\n=== 验证 ===')
    for s in stocks2[:3]:
        print(f'\n{s["code"]} {s.get("name","?")}:')
        for f in check_fields:
            print(f'  {f}: {s.get(f, "❌MISS")}')

    missing = {f: 0 for f in check_fields}
    for s in stocks2:
        for f in check_fields:
            v = s.get(f)
            if v is None or v == '' or v == 0:
                missing[f] += 1
    print(f'\n=== 缺失统计 (共{len(stocks2)}只) ===')
    for f, cnt in sorted(missing.items(), key=lambda x: -x[1]):
        status = '❌全缺' if cnt == len(stocks2) else (f'缺{cnt}只' if cnt > 0 else '✅全有')
        print(f'  {f:15s}: {status}')

if __name__ == '__main__':
    main()
