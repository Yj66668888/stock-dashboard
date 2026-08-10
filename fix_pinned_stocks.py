#!/usr/bin/env python3
"""
修复4只固定票的数据，使其与正常票完全一致：
1. 分别获取5分钟/30分钟/日线K线，正确计算三个周期KDJ
2. 获取5日/10日累计资金流
3. 计算主力占比ratio5d/ratio10d
4. 设置sectorTier
5. 计算healthScore
6. 设置direction为实际方向分析
"""

import json, re, subprocess, time, sys, os

PINNED = [
    {'code': 'sh603067', 'name': '振华股份', 'secid': '1.603067', 'sector': '化工'},
    {'code': 'sh600105', 'name': '永鼎股份', 'secid': '1.600105', 'sector': '通信'},
    {'code': 'sh601126', 'name': '四方股份', 'secid': '1.601126', 'sector': '电力设备'},
    {'code': 'sz000688', 'name': '国城矿业', 'secid': '0.000688', 'sector': '有色金属'},
]

def curl_json(url, timeout=10, headers=None):
    """curl获取JSON"""
    cmd = ['curl', '-sL', '--max-time', str(timeout), url, '-H', 'User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)']
    if headers:
        for h in headers:
            cmd.extend(['-H', h])
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout+5)
        raw = r.stdout.decode('utf-8', errors='replace')
        # Handle JSONP
        if raw.startswith('(') or 'jQuery' in raw[:20]:
            s = raw.find('(')
            e = raw.rfind(')')
            if s >= 0 and e > s:
                raw = raw[s+1:e]
        return json.loads(raw) if raw else None
    except Exception as e:
        return None

def curl_gbk(url, timeout=10):
    """curl获取GBK编码内容"""
    cmd = ['curl', '-sL', '--max-time', str(timeout), url]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout+5)
        return r.stdout.decode('gbk', errors='replace')
    except:
        return ''

def fetch_kline_sina(sina_code, scale, datalen):
    """新浪K线API: scale=5(5分钟), 30(30分钟), 240(日线)"""
    url = f'https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol={sina_code}&scale={scale}&datalen={datalen}'
    for attempt in range(3):
        raw = subprocess.run(['curl', '-sL', '--max-time', '10', url], capture_output=True, timeout=15)
        text = raw.stdout.decode('utf-8', errors='replace')
        if text and text.startswith('['):
            try:
                return json.loads(text)
            except:
                pass
        time.sleep(1)
    return None

def calc_kdj(klines, n=9, m1=3, m2=3):
    """计算KDJ(9,3,3)"""
    if not klines or len(klines) < n:
        return None
    highs = [float(k['high']) for k in klines]
    lows = [float(k['low']) for k in klines]
    closes = [float(k['close']) for k in klines]
    
    k_prev = 50.0
    d_prev = 50.0
    k_val = 50.0
    d_val = 50.0
    
    for i in range(len(klines)):
        start = max(0, i - n + 1)
        hh = max(highs[start:i+1])
        ll = min(lows[start:i+1])
        if hh == ll:
            rsv = 50.0
        else:
            rsv = (closes[i] - ll) / (hh - ll) * 100
        k_val = (m1 - 1) / m1 * k_prev + rsv / m1
        d_val = (m2 - 1) / m2 * d_prev + k_val / m2
        k_prev = k_val
        d_prev = d_val
    
    j_val = 3 * k_val - 2 * d_val
    # prev k for trend
    k_prev2 = 50.0
    d_prev2 = 50.0
    k2 = 50.0
    for i in range(len(klines) - 1):
        start = max(0, i - n + 1)
        hh = max(highs[start:i+1])
        ll = min(lows[start:i+1])
        if hh == ll:
            rsv = 50.0
        else:
            rsv = (closes[i] - ll) / (hh - ll) * 100
        k2 = (m1 - 1) / m1 * k_prev2 + rsv / m1
        d_prev2 = (m2 - 1) / m2 * d_prev2 + k2 / m2
        k_prev2 = k2
    
    cross = '上升' if k_val > k_prev2 else ('下降' if k_val < k_prev2 else '持平')
    return {
        'k': round(k_val, 2),
        'd': round(d_val, 2),
        'j': round(j_val, 2),
        'cross': cross
    }

def calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return None
    gains = []
    losses = []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i-1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    if len(gains) < period:
        return None
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period
    if al == 0:
        return 100.0
    return round(100 - 100 / (1 + ag / al), 2)

def calc_macd(closes, f=12, s=26, sig=9):
    if len(closes) < s + sig:
        return None
    ef = sum(closes[:f]) / f
    es = sum(closes[:s]) / s
    difs = []
    mf = 2 / (f + 1)
    ms = 2 / (s + 1)
    for i in range(len(closes)):
        if i >= f:
            ef = (closes[i] - ef) * mf + ef
        if i >= s:
            es = (closes[i] - es) * ms + es
        if i >= s - 1:
            difs.append(ef - es)
    if len(difs) < sig:
        return None
    dea = sum(difs[:sig]) / sig
    md = 2 / (sig + 1)
    for i in range(sig, len(difs)):
        dea = (difs[i] - dea) * md + dea
    d = difs[-1]
    cross = '金叉' if d > dea else '死叉'
    return f'DIF:{d:+.2f} DEA:{dea:+.2f} {cross}'

def fetch_capital_flow_5d10d(secid):
    """获取5日/10日主力资金流"""
    url = f'https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get?secid={secid}&klt=101&lmt=12&fltt=2&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56,f57'
    headers = ['Referer: https://quote.eastmoney.com/']
    d = curl_json(url, 10, headers)
    if not d or 'data' not in d or not d['data']:
        return None
    klines = d['data'].get('klines', [])
    if not klines:
        return None
    flows = []
    for k in klines:
        parts = k.split(',')
        if len(parts) >= 2:
            try:
                flows.append(float(parts[1]))  # 主力净流入(元)
            except:
                flows.append(0)
    if not flows:
        return None
    flow_5d = sum(flows[-5:]) if len(flows) >= 5 else sum(flows)
    flow_10d = sum(flows[-10:]) if len(flows) >= 10 else sum(flows)
    return {
        'flow_5d_wan': round(flow_5d / 10000, 2),
        'flow_10d_wan': round(flow_10d / 10000, 2),
        'daily_flows_wan': [round(f / 10000, 2) for f in flows[-12:]]
    }

def fetch_capital_ratio(secid):
    """获取主力占比5日/10日"""
    url = f'https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get?secid={secid}&klt=101&lmt=10&fltt=2&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56,f57'
    headers = ['Referer: https://quote.eastmoney.com/']
    d = curl_json(url, 10, headers)
    if not d or 'data' not in d or not d['data']:
        return None
    klines = d['data'].get('klines', [])
    if not klines:
        return None
    ratios = []
    for k in klines:
        parts = k.split(',')
        if len(parts) >= 7:
            try:
                # f55=主力净流入占比
                ratios.append(float(parts[6]) if parts[6] else 0)
            except:
                ratios.append(0)
    if not ratios:
        return None
    ratio_5d = round(sum(ratios[-5:]) / len(ratios[-5:]), 2) if len(ratios) >= 5 else round(sum(ratios) / len(ratios), 2)
    ratio_10d = round(sum(ratios[-10:]) / len(ratios[-10:]), 2) if len(ratios) >= 10 else round(sum(ratios) / len(ratios), 2)
    return {'ratio5d': ratio_5d, 'ratio10d': ratio_10d}

def fetch_realtime_quote(code):
    """腾讯行情API获取实时数据"""
    raw = curl_gbk(f'https://qt.gtimg.cn/q={code}')
    var_name = f'v_{code}='
    idx = raw.find(var_name)
    if idx < 0:
        return None
    start = raw.find('"', idx) + 1
    end = raw.find('"', start)
    if start <= 0 or end <= start:
        return None
    fields = raw[start:end].split('~')
    if len(fields) < 50:
        return None
    try:
        price = float(fields[3]) if fields[3] else 0
        prev = float(fields[4]) if fields[4] else 0
        chg = ((price - prev) / prev * 100) if prev > 0 else 0
        turnover = float(fields[38]) if fields[38] else 0
        pe = float(fields[39]) if fields[39] else 0
        pb = float(fields[46]) if len(fields) > 46 and fields[46] else 0
        return {'price': price, 'changePct': round(chg, 2), 'turnover': turnover, 'pe': pe, 'pb': pb}
    except:
        return None

def fetch_today_capital(secid):
    """获取当日主力资金流"""
    url = f'https://push2.eastmoney.com/api/qt/stock/fflow/kline/get?secid={secid}&klt=1&lmt=1&fltt=2&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56,f57'
    headers = ['Referer: https://quote.eastmoney.com/']
    d = curl_json(url, 8, headers)
    if not d or 'data' not in d or not d['data']:
        return None
    klines = d['data'].get('klines', [])
    if not klines:
        return None
    parts = klines[-1].split(',')
    if len(parts) >= 2:
        try:
            return round(float(parts[1]) / 10000, 2)  # 万元
        except:
            return None
    return None

def calc_health_score(drop20d, rsi, ma_above, flow_5d_wan, ratio5d, ratio10d, kd5_cross, ma_state):
    """简化健康分计算"""
    score = 50  # 基础分
    # 位置分
    if drop20d < -10:
        score += 8  # 回调到位
    elif drop20d < -5:
        score += 5
    elif drop20d > 10:
        score -= 8  # 高位
    elif drop20d > 5:
        score -= 4
    
    # RSI
    if rsi:
        if 30 <= rsi <= 50:
            score += 6
        elif 50 < rsi <= 65:
            score += 4
        elif rsi > 70:
            score -= 5
        elif rsi < 30:
            score += 3
    
    # 均线
    if ma_above:
        score += 3
    if ma_state == '多头排列':
        score += 5
    elif ma_state == '空头排列':
        score -= 5
    
    # 资金流
    if flow_5d_wan and flow_5d_wan > 0:
        score += 5
    elif flow_5d_wan and flow_5d_wan < -5000:
        score -= 5
    
    # 主力占比
    if ratio5d and ratio10d:
        if ratio5d > ratio10d:
            score += 4  # 加码
        else:
            score -= 2  # 撤退
    
    # KDJ方向
    if kd5_cross == '上升':
        score += 3
    elif kd5_cross == '下降':
        score -= 2
    
    return max(0, min(100, score))

def get_direction(drop20d, rsi, flow_5d_wan, kd5_cross, ma_state):
    """生成方向描述"""
    parts = []
    if drop20d < -10:
        parts.append('回调到位')
    elif drop20d > 10:
        parts.append('高位风险')
    elif drop20d > 5:
        parts.append('高位震荡')
    
    if flow_5d_wan and flow_5d_wan > 0:
        parts.append('资金流入')
    elif flow_5d_wan and flow_5d_wan < -3000:
        parts.append('资金流出')
    
    if kd5_cross == '上升':
        parts.append('KDJ上行')
    elif kd5_cross == '下降':
        parts.append('KDJ下行')
    
    if rsi:
        if rsi < 35:
            parts.append('超卖')
        elif rsi > 70:
            parts.append('超买')
    
    if ma_state == '多头排列':
        parts.append('多头')
    elif ma_state == '空头排列':
        parts.append('空头')
    
    return ','.join(parts) if parts else '震荡'

def fmt_flow(wan):
    if wan is None or wan == 0:
        return None
    abs_v = abs(wan)
    sign = '+' if wan >= 0 else '-'
    if abs_v >= 10000:
        return f'{sign}{abs_v/10000:.2f}亿'
    return f'{sign}{abs_v:.0f}万'

def get_sector_tier(sector):
    """简单板块分级"""
    t1 = ['汽车', '化工', '通信', '电力设备', '有色金属', '电子', '医药', '军工', '新能源']
    t2 = ['机械', '钢铁', '建材', '房地产', '银行', '证券', '保险']
    if sector in t1:
        return 1
    elif sector in t2:
        return 2
    return 3

# ============================================================
# Main
# ============================================================
print('='*60)
print('修复4只固定票数据')
print('='*60)

HTML_PATH = '/Users/fuckyouasshole/WorkBuddy/2026-07-01-11-18-34/deploy_davis/index.html'

with open(HTML_PATH, 'r') as f:
    html = f.read()

m = re.search(r'const\s+STOCKS\s*=\s*(\[.*?\])\s*;', html, re.S)
stocks = json.loads(m.group(1))

pinned_codes = {p['code'] for p in PINNED}

for p in PINNED:
    code = p['code']
    name = p['name']
    sina_code = code  # sina uses sh/sz prefix
    secid = p['secid']
    
    print(f'\n--- {code} {name} ---')
    
    # 1. 三周期K线
    print(f'  获取5分钟K线...')
    kl5 = fetch_kline_sina(sina_code, 5, 300)
    time.sleep(0.5)
    print(f'  获取30分钟K线...')
    kl30 = fetch_kline_sina(sina_code, 30, 300)
    time.sleep(0.5)
    print(f'  获取日线...')
    kl_daily = fetch_kline_sina(sina_code, 240, 100)
    time.sleep(0.5)
    
    if not kl_daily:
        print(f'  ❌ 日线获取失败，跳过')
        continue
    
    # 2. 计算三周期KDJ
    kd5 = calc_kdj(kl5) if kl5 else None
    kd30 = calc_kdj(kl30) if kl30 else None
    kd60 = calc_kdj(kl_daily) if kl_daily else None
    
    # 60分钟KDJ用日线数据代替（如果日线是240分钟级别）
    # 实际上应该用60分钟K线，但新浪scale=60也行
    print(f'  获取60分钟K线...')
    kl60 = fetch_kline_sina(sina_code, 60, 300)
    time.sleep(0.5)
    kd60 = calc_kdj(kl60) if kl60 else (calc_kdj(kl_daily) if kl_daily else None)
    
    print(f'  KDJ: 5min={kd5["k"] if kd5 else "?"}/{kd5["d"] if kd5 else "?"}({kd5["cross"] if kd5 else "?"}), '
          f'30min={kd30["k"] if kd30 else "?"}/{kd30["d"] if kd30 else "?"}({kd30["cross"] if kd30 else "?"}), '
          f'60min={kd60["k"] if kd60 else "?"}/{kd60["d"] if kd60 else "?"}({kd60["cross"] if kd60 else "?"})')
    
    # 3. 日线指标
    closes = [float(k['close']) for k in kl_daily]
    highs = [float(k['high']) for k in kl_daily]
    lows = [float(k['low']) for k in kl_daily]
    vols = [float(k['volume']) for k in kl_daily]
    opens = [float(k['open']) for k in kl_daily]
    
    rsi = calc_rsi(closes)
    macd = calc_macd(closes)
    ma5 = round(sum(closes[-5:]) / 5, 2)
    ma10 = round(sum(closes[-10:]) / 10, 2) if len(closes) >= 10 else None
    ma20 = round(sum(closes[-20:]) / 20, 2) if len(closes) >= 20 else None
    last = closes[-1]
    ma_above = ma20 is not None and last > ma20
    if ma20 and ma10:
        if ma5 > ma10 > ma20:
            ma_state = '多头排列'
        elif ma5 < ma10 < ma20:
            ma_state = '空头排列'
        else:
            ma_state = '交叉震荡'
    else:
        ma_state = '交叉震荡'
    
    drop20d = round((closes[-1] - closes[-20]) / closes[-20] * 100, 2) if len(closes) >= 20 else 0
    avg_vol = sum(vols[-6:-1]) / 5 if len(vols) >= 6 else vols[-1] if vols else 1
    vol_ratio = round(vols[-1] / avg_vol, 2) if avg_vol > 0 else 1.0
    
    # 量价信号
    if vol_ratio >= 2.0:
        vol_signal = '放量'
    elif vol_ratio >= 1.0:
        vol_signal = '正常'
    elif vol_ratio >= 0.5:
        vol_signal = '缩量'
    else:
        vol_signal = '极度缩量'
    
    support = min(lows[-20:]) if len(lows) >= 20 else min(lows)
    support_dist = round((last - support) / last * 100, 1) if last > 0 else None
    
    up_count = sum(1 for i in range(1, min(31, len(opens))) if opens[i] > closes[i-1])
    total = min(30, len(opens) - 1)
    open_rate = round(up_count / total * 100, 1) if total > 0 else 0
    
    # 趋势
    if len(closes) >= 10:
        trend_pct = round((closes[-1] - closes[-10]) / closes[-10] * 100, 2)
    else:
        trend_pct = 0
    if trend_pct > 5:
        trend = '上升趋势'
    elif trend_pct < -5:
        trend = '下降趋势'
    else:
        trend = '震荡偏弱' if trend_pct < 0 else '震荡偏强'
    
    # 4. 资金流
    print(f'  获取资金流数据...')
    cf = fetch_capital_flow_5d10d(secid)
    time.sleep(0.5)
    flow_5d_wan = cf['flow_5d_wan'] if cf else None
    flow_10d_wan = cf['flow_10d_wan'] if cf else None
    
    # 5. 主力占比
    ratios = fetch_capital_ratio(secid)
    time.sleep(0.5)
    ratio5d = ratios['ratio5d'] if ratios else None
    ratio10d = ratios['ratio10d'] if ratios else None
    
    # 6. 当日主力资金
    today_flow = fetch_today_capital(secid)
    time.sleep(0.5)
    
    # 7. 实时行情
    quote = fetch_realtime_quote(code)
    
    # 8. 健康分
    health_score = calc_health_score(drop20d, rsi, ma_above, flow_5d_wan, ratio5d, ratio10d,
                                      kd5['cross'] if kd5 else '持平', ma_state)
    
    # 9. 方向
    direction = get_direction(drop20d, rsi, flow_5d_wan, kd5['cross'] if kd5 else '持平', ma_state)
    
    # 10. 板块层级
    sector_tier = get_sector_tier(p['sector'])
    
    # 更新STOCKS中的固定票
    for s in stocks:
        if s['code'] == code:
            # KDJ三周期 - 确保不同
            if kd5:
                s['kd5_k'] = kd5['k']
                s['kd5_d'] = kd5['d']
                s['kd5_cross'] = kd5['cross']
            if kd30:
                s['kd30_k'] = kd30['k']
                s['kd30_d'] = kd30['d']
                s['kd30_cross'] = kd30['cross']
            if kd60:
                s['kd60_k'] = kd60['k']
                s['kd60_d'] = kd60['d']
                s['kd60_cross'] = kd60['cross']
            
            # 日线指标
            s['rsi'] = rsi
            s['macd'] = macd
            s['ma'] = ma_state
            s['ma5'] = ma5
            if ma10:
                s['ma10'] = ma10
            if ma20:
                s['ma20'] = ma20
            s['maAbove'] = ma_above
            s['drop20d'] = drop20d
            s['volRatio'] = vol_ratio
            s['volSignal'] = vol_signal
            s['supportPrice'] = round(support, 2)
            s['supportDist'] = support_dist
            s['openRate30d'] = open_rate
            s['openRateTotal'] = open_rate
            s['openRateScore'] = 2 if open_rate >= 40 else 1
            s['trend'] = trend
            s['trendPct'] = trend_pct
            
            # 资金流
            if flow_5d_wan is not None:
                s['flow5d'] = fmt_flow(flow_5d_wan)
                s['flow5d_wan'] = flow_5d_wan
            if flow_10d_wan is not None:
                s['flow10d'] = fmt_flow(flow_10d_wan)
                s['flow10d_wan'] = flow_10d_wan
            
            # 主力占比
            if ratio5d is not None:
                s['ratio5d'] = ratio5d
            if ratio10d is not None:
                s['ratio10d'] = ratio10d
            
            # 当日主力
            if today_flow is not None:
                s['mainFlowToday'] = today_flow
                s['mainFlow'] = fmt_flow(today_flow)
                s['mainFlowDate'] = '当日实时'
                s['mainFlowPct'] = round(today_flow / (quote['price'] * 10000) * 100, 2) if quote and quote.get('price') else 0
            
            # 实时行情
            if quote:
                s['price'] = quote['price']
                s['changePct'] = quote['changePct']
                s['turnover'] = quote['turnover']
                if quote['pe']:
                    s['pe'] = quote['pe']
                if quote['pb']:
                    s['pb'] = quote['pb']
            
            # 健康分和方向
            s['healthScore'] = health_score
            s['dailyScore'] = health_score  # dailyScore = healthScore
            s['direction'] = direction
            s['sectorTier'] = sector_tier
            s['tier'] = 'A' if health_score >= 70 else ('B' if health_score >= 55 else 'C')
            
            # riskFlags
            flags = ['📌固定关注']
            if drop20d < -10:
                flags.append('回调到位')
            if flow_5d_wan and flow_5d_wan > 0:
                flags.append('资金流入')
            elif flow_5d_wan and flow_5d_wan < -3000:
                flags.append('资金流出')
            if rsi and rsi > 70:
                flags.append('超买')
            elif rsi and rsi < 30:
                flags.append('超卖')
            s['riskFlags'] = flags
            
            # reason
            s['reason'] = f'📌固定关注（{direction}）'
            
            # preLaunch
            s['preLaunchPhase'] = 'stable' if health_score >= 55 else 'weak'
            s['preLaunchScore'] = health_score
            
            print(f'  ✅ 更新完成: score={health_score}, dir={direction}, flow5d={fmt_flow(flow_5d_wan) if flow_5d_wan else "?"}, ratio5d={ratio5d}, sectorTier={sector_tier}')
            break

# 写回HTML
new_stocks_json = json.dumps(stocks, ensure_ascii=False)
html = re.sub(r'const\s+STOCKS\s*=\s*\[.*?\]\s*;', f'const STOCKS = {new_stocks_json};', html, count=1, flags=re.S)
with open(HTML_PATH, 'w') as f:
    f.write(html)

# 验证
with open(HTML_PATH, 'r') as f:
    html2 = f.read()
m2 = re.search(r'const\s+STOCKS\s*=\s*(\[.*?\])\s*;', html2, re.S)
stocks2 = json.loads(m2.group(1))

print('\n' + '='*60)
print('验证结果')
print('='*60)

for s in stocks2:
    if s.get('isPinned'):
        print(f'\n{s["code"]} {s["name"]}:')
        print(f'  KDJ 5min:  k={s.get("kd5_k","?")}, d={s.get("kd5_d","?")}, {s.get("kd5_cross","?")}')
        print(f'  KDJ 30min: k={s.get("kd30_k","?")}, d={s.get("kd30_d","?")}, {s.get("kd30_cross","?")}')
        print(f'  KDJ 60min: k={s.get("kd60_k","?")}, d={s.get("kd60_d","?")}, {s.get("kd60_cross","?")}')
        k5 = s.get('kd5_k'); k30 = s.get('kd30_k'); k60 = s.get('kd60_k')
        identical = '⚠️相同!' if k5 == k30 == k60 else '✅不同'
        print(f'  三周期KDJ: {identical}')
        print(f'  healthScore: {s.get("healthScore")}, dailyScore: {s.get("dailyScore")}')
        print(f'  direction: {s.get("direction")}')
        print(f'  sectorTier: {s.get("sectorTier")}, tier: {s.get("tier")}')
        print(f'  flow5d: {s.get("flow5d","?")}, ratio5d: {s.get("ratio5d","?")}, ratio10d: {s.get("ratio10d","?")}')
        print(f'  mainFlow: {s.get("mainFlow","?")}, mainFlowDate: {s.get("mainFlowDate","?")}')

print('\n✅ 完成!')
