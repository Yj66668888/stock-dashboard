#!/usr/bin/env python3
"""
v2: 使用curl调用API，为4只固定票预计算技术指标 + 全部34只票的5/10日资金流
"""
import json
import re
import subprocess
import time
import math
from datetime import datetime

PINNED_STOCKS = [
    {'code': 'sh603067', 'name': '振华股份', 'sina': 'sh603067', 'em_secid': '1.603067'},
    {'code': 'sh600105', 'name': '永鼎股份', 'sina': 'sh600105', 'em_secid': '1.600105'},
    {'code': 'sh601126', 'name': '四方股份', 'sina': 'sh601126', 'em_secid': '1.601126'},
    {'code': 'sz000688', 'name': '国城矿业', 'sina': 'sz000688', 'em_secid': '0.000688'},
]

ALL_CODES = [
    'sh600418','sh600686','sh600702','sh601058','sh603156','sh603345','sh603317',
    'sh600741','sh601163','sh601966','sh603057','sh600737','sh600335','sh600166',
    'sh601633','sh603039','sh600475','sh601872','sh600188','sh603619','sh600436',
    'sh600835','sh601127','sh600895','sh601933','sh601606','sh603171','sh600986',
    'sh600426','sh601799',
    'sh603067','sh600105','sh601126','sz000688',
]

def to_secid(code):
    num = re.sub(r'^(sh|sz|bj)', '', code)
    return ('1.' if code.startswith('sh') else '0.') + num

def curl_get(url, timeout=10):
    """用curl获取URL内容"""
    try:
        result = subprocess.run(
            ['curl', '-sL', '--max-time', str(timeout), url,
             '-H', 'User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)',
             '-H', 'Referer: https://quote.eastmoney.com/'],
            capture_output=True, text=True, timeout=timeout+5
        )
        return result.stdout
    except Exception as e:
        return ''

def fetch_sina_kline(sina_code, scale, datalen=300):
    """从新浪获取K线
    scale: 5=5分钟, 30=30分钟, 60=60分钟, 240=日线
    返回: list of [day, open, high, low, close, volume]
    """
    url = f'https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol={sina_code}&scale={scale}&datalen={datalen}'
    raw = curl_get(url, 8)
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return data
    except:
        return None

def fetch_em_daily_kline(em_secid, lmt=100):
    """从东方财富获取日K线
    返回: list of "date,open,close,high,low,volume" 字符串
    """
    url = f'https://push2his.eastmoney.com/api/qt/stock/kline/get?secid={em_secid}&klt=101&fqt=1&end=20500101&lmt={lmt}&fields1=f1,f2,f3&fields2=f51,f52,f53,f54,f55,f56'
    raw = curl_get(url, 10)
    if not raw:
        return None
    try:
        data = json.loads(raw)
        if data.get('data') and data['data'].get('klines'):
            return data['data']['klines']
    except:
        pass
    return None

def fetch_em_flow_5d10d(em_secid):
    """从东方财富获取5/10日资金流"""
    url = f'https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get?secid={em_secid}&klt=101&lmt=12&fltt=2&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56,f57'
    raw = curl_get(url, 10)
    if not raw:
        return None, None
    try:
        data = json.loads(raw)
        if data.get('data') and data['data'].get('klines'):
            flows = []
            for k in data['data']['klines']:
                parts = k.split(',')
                if len(parts) >= 2:
                    try:
                        flows.append(float(parts[1]))
                    except:
                        flows.append(0)
            f5 = sum(flows[-5:]) if len(flows) >= 5 else None
            f10 = sum(flows[-10:]) if len(flows) >= 10 else None
            return f5, f10
    except:
        pass
    return None, None

def calc_kdj(klines_data, n=9):
    """KDJ(9,3,3) - 从新浪格式数据计算
    klines_data: list of {day, open, high, low, close, volume}
    """
    if not klines_data or len(klines_data) < n:
        return None
    k, d = 50.0, 50.0
    prev_k, prev_d = 50.0, 50.0
    for i in range(len(klines_data)):
        close = float(klines_data[i]['close'])
        high = -float('inf')
        low = float('inf')
        lookback = min(n, i + 1)
        for j in range(i - lookback + 1, i + 1):
            h = float(klines_data[j]['high'])
            l = float(klines_data[j]['low'])
            if h > high: high = h
            if l < low: low = l
        if high == low:
            rsv = 50.0
        else:
            rsv = (close - low) / (high - low) * 100.0
        k = (2.0/3.0) * prev_k + (1.0/3.0) * rsv
        d = (2.0/3.0) * prev_d + (1.0/3.0) * k
        prev_k = k
        prev_d = d
    cross = '金叉' if prev_k < prev_d and k > d else ('死叉' if prev_k > prev_d and k < d else ('上升' if k > d else '下降'))
    return {'k': round(k, 2), 'd': round(d, 2), 'cross': cross}

def calc_kdj_em(klines_str, n=9):
    """KDJ from East Money klines format: "date,open,close,high,low,volume" """
    if not klines_str or len(klines_str) < n:
        return None
    k, d = 50.0, 50.0
    prev_k, prev_d = 50.0, 50.0
    for i in range(len(klines_str)):
        parts = klines_str[i].split(',')
        close = float(parts[2])
        high = float(parts[3])
        low = float(parts[4])
        lookback_h = -float('inf')
        lookback_l = float('inf')
        lookback = min(n, i + 1)
        for j in range(i - lookback + 1, i + 1):
            p = klines_str[j].split(',')
            h = float(p[3])
            l = float(p[4])
            if h > lookback_h: lookback_h = h
            if l < lookback_l: lookback_l = l
        if lookback_h == lookback_l:
            rsv = 50.0
        else:
            rsv = (close - lookback_l) / (lookback_h - lookback_l) * 100.0
        k = (2.0/3.0) * prev_k + (1.0/3.0) * rsv
        d = (2.0/3.0) * prev_d + (1.0/3.0) * k
        prev_k = k
        prev_d = d
    cross = '金叉' if prev_k < prev_d and k > d else ('死叉' if prev_k > prev_d and k < d else ('上升' if k > d else '下降'))
    return {'k': round(k, 2), 'd': round(d, 2), 'cross': cross}

def calc_rsi_em(klines_str, period=14):
    """RSI(14) from East Money klines"""
    if not klines_str or len(klines_str) < period + 1:
        return None
    closes = [float(k.split(',')[2]) for k in klines_str]
    gains = []
    losses = []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i-1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    if len(gains) < period:
        return None
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)

def calc_macd_em(klines_str, fast=12, slow=26, signal=9):
    """MACD from East Money klines"""
    if not klines_str or len(klines_str) < slow + signal:
        return None
    closes = [float(k.split(',')[2]) for k in klines_str]
    ef = sum(closes[:fast]) / fast
    es = sum(closes[:slow]) / slow
    dif_vals = []
    mf = 2 / (fast + 1)
    ms = 2 / (slow + 1)
    for i in range(len(closes)):
        if i >= fast:
            ef = (closes[i] - ef) * mf + ef
        if i >= slow:
            es = (closes[i] - es) * ms + es
        if i >= slow - 1:
            dif_vals.append(ef - es)
    if len(dif_vals) < signal:
        return None
    dea = sum(dif_vals[:signal]) / signal
    md = 2 / (signal + 1)
    for i in range(signal, len(dif_vals)):
        dea = (dif_vals[i] - dea) * md + dea
    dif = dif_vals[-1]
    cross = '金叉' if dif > dea else '死叉'
    return f'DIF:{dif:+.2f} DEA:{dea:+.2f} {cross}'

def calc_ma_em(klines_str):
    """MA from East Money klines"""
    if not klines_str or len(klines_str) < 5:
        return None
    closes = [float(k.split(',')[2]) for k in klines_str]
    ma5 = sum(closes[-5:]) / 5
    ma10 = sum(closes[-10:]) / 10 if len(closes) >= 10 else None
    ma20 = sum(closes[-20:]) / 20 if len(closes) >= 20 else None
    last = closes[-1]
    if ma20 and ma5 > ma10 > ma20:
        ma_str = '多头排列'
    elif ma20 and ma5 < ma10 < ma20:
        ma_str = '空头排列'
    else:
        ma_str = '交叉震荡'
    result = {'ma': ma_str, 'ma5': round(ma5, 2), 'maAbove': ma20 is not None and last > ma20}
    if ma10: result['ma10'] = round(ma10, 2)
    if ma20: result['ma20'] = round(ma20, 2)
    return result

def calc_drop20d_em(klines_str):
    if not klines_str or len(klines_str) < 2:
        return 0
    closes = [float(k.split(',')[2]) for k in klines_str]
    if len(closes) >= 20:
        return round((closes[-1] - closes[-20]) / closes[-20] * 100, 2)
    return round((closes[-1] - closes[0]) / closes[0] * 100, 2)

def calc_vol_ratio_em(klines_str):
    if not klines_str or len(klines_str) < 6:
        return 1.0
    vols = [float(k.split(',')[5]) for k in klines_str]
    avg = sum(vols[-6:-1]) / 5
    return round(vols[-1] / avg, 2) if avg > 0 else 1.0

def calc_trend_em(klines_str):
    if not klines_str or len(klines_str) < 5:
        return '震荡', 0
    closes = [float(k.split(',')[2]) for k in klines_str][-5:]
    n = len(closes)
    sx = sum(range(n))
    sy = sum(closes)
    sxy = sum(i * closes[i] for i in range(n))
    sx2 = sum(i * i for i in range(n))
    denom = n * sx2 - sx * sx
    if denom == 0:
        return '震荡', 0
    slope = (n * sxy - sx * sy) / denom
    pct = slope / closes[0] * 100 if closes[0] != 0 else 0
    if pct > 0.5:
        return '上升趋势', round(pct, 1)
    elif pct < -0.5:
        return '下降趋势', round(pct, 1)
    return '震荡', round(pct, 1)

def calc_support_em(klines_str):
    if not klines_str or len(klines_str) < 5:
        return None, None
    lows = [float(k.split(',')[4]) for k in klines_str]
    lookback = min(20, len(lows))
    support = min(lows[-lookback:])
    last_close = float(klines_str[-1].split(',')[2])
    dist = round((last_close - support) / last_close * 100, 1) if last_close > 0 else None
    return round(support, 2), dist

def format_flow(val):
    if val is None or val != val:
        return '--'
    a = abs(val)
    s = '+' if val >= 0 else '-'
    if a >= 1e8:
        return f'{s}{a/1e8:.2f}亿'
    if a >= 1e4:
        return f'{s}{a/1e4:.0f}万'
    return f'{s}{a:.0f}'

def compute_pinned():
    """计算4只固定票技术指标"""
    results = {}
    for stock in PINNED_STOCKS:
        code = stock['code']
        print(f'\n=== {code} {stock["name"]} ===')
        fb = {}

        # 5分钟KDJ (Sina)
        k5 = fetch_sina_kline(stock['sina'], 5, 300)
        if k5:
            kdj5 = calc_kdj(k5)
            if kdj5:
                fb['kd5_k'] = kdj5['k']
                fb['kd5_d'] = kdj5['d']
                print(f'  5min KDJ: K={kdj5["k"]} D={kdj5["d"]} {kdj5["cross"]}')
        time.sleep(0.5)

        # 30分钟KDJ (Sina)
        k30 = fetch_sina_kline(stock['sina'], 30, 300)
        if k30:
            kdj30 = calc_kdj(k30)
            if kdj30:
                fb['kd30_k'] = kdj30['k']
                fb['kd30_d'] = kdj30['d']
                fb['kd30_cross'] = kdj30['cross']
                print(f'  30min KDJ: K={kdj30["k"]} D={kdj30["d"]} {kdj30["cross"]}')
        time.sleep(0.5)

        # 60分钟KDJ (Sina)
        k60 = fetch_sina_kline(stock['sina'], 60, 300)
        if k60:
            kdj60 = calc_kdj(k60)
            if kdj60:
                fb['kd60_k'] = kdj60['k']
                fb['kd60_d'] = kdj60['d']
                fb['kd60_cross'] = kdj60['cross']
                print(f'  60min KDJ: K={kdj60["k"]} D={kdj60["d"]} {kdj60["cross"]}')
        time.sleep(0.5)

        # 日K线 (East Money)
        dk = fetch_em_daily_kline(stock['em_secid'], 100)
        if dk:
            rsi = calc_rsi_em(dk)
            if rsi is not None:
                fb['rsi'] = rsi
                print(f'  RSI: {rsi}')

            macd = calc_macd_em(dk)
            if macd:
                fb['macd'] = macd
                print(f'  MACD: {macd}')

            ma = calc_ma_em(dk)
            if ma:
                fb.update(ma)
                print(f'  MA: {ma["ma"]} MA5={ma.get("ma5","?")} MA20={ma.get("ma20","?")}')

            fb['drop20d'] = calc_drop20d_em(dk)
            fb['volRatio'] = calc_vol_ratio_em(dk)
            print(f'  drop20d: {fb["drop20d"]}%  volRatio: {fb["volRatio"]}')

            trend, tp = calc_trend_em(dk)
            print(f'  trend: {trend} ({tp}%)')

            sp, sd = calc_support_em(dk)
            if sp:
                fb['supportPrice'] = sp
                fb['supportDist'] = sd
                print(f'  support: {sp} ({sd}%)')

            # 30日高开率
            opens = [float(k.split(',')[1]) for k in dk]
            prev_closes = [float(k.split(',')[2]) for k in dk]
            up = sum(1 for i in range(1, min(31, len(opens))) if opens[i] > prev_closes[i-1])
            tot = min(30, len(opens) - 1)
            if tot > 0:
                fb['openRate30d'] = round(up / tot * 100, 1)
                fb['openRateTotal'] = fb['openRate30d']
                fb['openRateScore'] = 2 if fb['openRate30d'] >= 40 else 1

        time.sleep(0.5)

        # 5/10日资金流
        f5, f10 = fetch_em_flow_5d10d(stock['em_secid'])
        if f5 is not None:
            fb['mainFlow'] = f'5日{format_flow(f5)}'
            fb['mainFlowDate'] = '5日累计'
            print(f'  flow5d: {format_flow(f5)}')
        if f10 is not None:
            print(f'  flow10d: {format_flow(f10)}')

        results[code] = fb
        print(f'  => {len(fb)} fields')
        time.sleep(1)

    return results

def compute_all_flow_5d10d():
    """获取全部34只票5/10日资金流"""
    print('\n=== 获取5/10日资金流 ===')
    cache = {}
    for code in ALL_CODES:
        secid = to_secid(code)
        f5, f10 = fetch_em_flow_5d10d(secid)
        entry = {}
        if f5 is not None:
            entry['flow_5d_wan'] = round(f5, 1)
            entry['flow_5d_str'] = format_flow(f5)
        if f10 is not None:
            entry['flow_10d_wan'] = round(f10, 1)
            entry['flow_10d_str'] = format_flow(f10)
        if entry:
            cache[code] = entry
            print(f'  {code}: 5d={entry.get("flow_5d_str","?")} 10d={entry.get("flow_10d_str","?")}')
        else:
            print(f'  {code}: FAILED')
        time.sleep(0.8)
    return cache

def main():
    print(f'=== 预计算v2开始 {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} ===')

    pinned_fb = compute_pinned()
    flow_cache = compute_all_flow_5d10d()

    output = {
        'pinned_fallback': pinned_fb,
        'flow_5d10d_cache': flow_cache,
        'update_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }

    with open('pinned_precompute_v2.json', 'w') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f'\n=== 完成 ===')
    print(f'固定票技术指标: {sum(1 for v in pinned_fb.values() if len(v) > 0)}/4 只有数据')
    print(f'5/10日资金流: {len(flow_cache)}/34 只有数据')

    # 输出JS片段
    print('\n=== KDJ_FALLBACK 补充 ===')
    print(json.dumps(pinned_fb, ensure_ascii=False))

    print('\n=== FLOW_5D10D 补充 ===')
    print(json.dumps(flow_cache, ensure_ascii=False))

if __name__ == '__main__':
    main()
