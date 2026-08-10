#!/usr/bin/env python3
"""
预计算4只固定票的技术指标(KDJ/MACD/RSI/MA/趋势) + 全部34只票的主力资金流
输出JSON格式，供注入HTML的KDJ_FALLBACK和CAPITAL_FLOW_CACHE使用
"""
import json
import re
import urllib.request
import urllib.parse
import time
import math
from datetime import datetime

# 4只固定票
PINNED_STOCKS = [
    {'code': 'sh603067', 'name': '振华股份', 'secid': '1.603067'},
    {'code': 'sh600105', 'name': '永鼎股份', 'secid': '1.600105'},
    {'code': 'sh601126', 'name': '四方股份', 'secid': '1.601126'},
    {'code': 'sz000688', 'name': '国城矿业', 'secid': '0.000688'},
]

# 全部34只票（从HTML的STOCKS数组提取）
ALL_STOCKS_CODES = [
    'sh600418','sh600686','sh600702','sh601058','sh603156','sh603345','sh603317',
    'sh600741','sh601163','sh601966','sh603057','sh600737','sh600335','sh600166',
    'sh601633','sh603039','sh600475','sh601872','sh600188','sh603619','sh600436',
    'sh600835','sh601127','sh600895','sh601933','sh601606','sh603171','sh600986',
    'sh600426','sh601799',
    # 4只固定票
    'sh603067','sh600105','sh601126','sz000688',
]

def to_secid(code):
    num = re.sub(r'^(sh|sz|bj)', '', code)
    if code.startswith('sh'):
        return '1.' + num
    return '0.' + num

def fetch_kline(secid, klt, count=200):
    """获取K线数据
    klt: 5=5分钟, 15=15分钟, 30=30分钟, 60=60分钟, 101=日线
    """
    url = f'https://push2his.eastmoney.com/api/qt/stock/kline/get?secid={secid}&klt={klt}&fqt=1&end=20500101&lmt={count}&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61&_={int(time.time()*1000)}'
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)',
            'Referer': 'https://quote.eastmoney.com/'
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            return data
    except Exception as e:
        print(f'  [WARN] fetch_kline({secid},{klt}) failed: {e}')
        return None

def calc_kdj(klines, n=9):
    """KDJ(9,3,3)标准算法"""
    if not klines or len(klines) < n:
        return None
    k, d = 50.0, 50.0
    prev_k, prev_d = 50.0, 50.0
    for i in range(len(klines)):
        parts = klines[i].split(',')
        close = float(parts[2])
        high = -float('inf')
        low = float('inf')
        lookback = min(n, i + 1)
        for j in range(i - lookback + 1, i + 1):
            p = klines[j].split(',')
            h = float(p[3])
            l = float(p[4])
            if h > high:
                high = h
            if l < low:
                low = l
        if high == low:
            rsv = 50.0
        else:
            rsv = (close - low) / (high - low) * 100.0
        k = (2.0 / 3.0) * prev_k + (1.0 / 3.0) * rsv
        d = (2.0 / 3.0) * prev_d + (1.0 / 3.0) * k
        prev_k = k
        prev_d = d
    # 交叉判断
    cross = ''
    if prev_k < prev_d and k > d:
        cross = '金叉'
    elif prev_k > prev_d and k < d:
        cross = '死叉'
    else:
        cross = '上升' if k > d else '下降'
    return {'k': round(k, 2), 'd': round(d, 2), 'cross': cross}

def calc_rsi(klines, period=14):
    """RSI(14)"""
    if not klines or len(klines) < period + 1:
        return None
    closes = [float(k.split(',')[2]) for k in klines]
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
    rsi = 100 - (100 / (1 + rs))
    return round(rsi, 2)

def calc_macd(klines, fast=12, slow=26, signal=9):
    """MACD"""
    if not klines or len(klines) < slow + signal:
        return None
    closes = [float(k.split(',')[2]) for k in klines]
    # EMA
    def ema(data, period):
        if len(data) < period:
            return None
        multiplier = 2 / (period + 1)
        ema_val = sum(data[:period]) / period
        for i in range(period, len(data)):
            ema_val = (data[i] - ema_val) * multiplier + ema_val
        return ema_val
    # 计算EMA12和EMA26
    ema_fast_vals = []
    ema_slow_vals = []
    mf = 2 / (fast + 1)
    ms = 2 / (slow + 1)
    ef = sum(closes[:fast]) / fast
    es = sum(closes[:slow]) / slow
    for i in range(len(closes)):
        if i >= fast:
            ef = (closes[i] - ef) * mf + ef
        if i >= slow:
            es = (closes[i] - es) * ms + es
        if i >= slow - 1:
            ema_fast_vals.append(ef)
            ema_slow_vals.append(es)
    if len(ema_fast_vals) < signal:
        return None
    dif_vals = [ema_fast_vals[i] - ema_slow_vals[i] for i in range(len(ema_slow_vals))]
    # DEA = EMA(DIF, 9)
    dea = sum(dif_vals[:signal]) / signal
    md = 2 / (signal + 1)
    for i in range(signal, len(dif_vals)):
        dea = (dif_vals[i] - dea) * md + dea
    dif = dif_vals[-1]
    macd_bar = 2 * (dif - dea)
    cross = '金叉' if dif > dea else '死叉'
    return {
        'dif': round(dif, 2),
        'dea': round(dea, 2),
        'macd': round(macd_bar, 2),
        'cross': cross,
        'text': f'DIF:{dif:+.2f} DEA:{dea:+.2f} {cross}'
    }

def calc_ma(klines):
    """均线MA5/MA10/MA20"""
    if not klines or len(klines) < 5:
        return None
    closes = [float(k.split(',')[2]) for k in klines]
    ma5 = sum(closes[-5:]) / 5
    ma10 = sum(closes[-10:]) / 10 if len(closes) >= 10 else None
    ma20 = sum(closes[-20:]) / 20 if len(closes) >= 20 else None
    last_close = closes[-1]
    # 均线排列
    if ma20 and ma5 > ma10 > ma20:
        ma_str = '多头排列'
    elif ma20 and ma5 < ma10 < ma20:
        ma_str = '空头排列'
    else:
        ma_str = '交叉震荡'
    ma_above = ma20 is not None and last_close > ma20
    result = {
        'ma': ma_str,
        'ma5': round(ma5, 2),
        'maAbove': ma_above,
    }
    if ma10:
        result['ma10'] = round(ma10, 2)
    if ma20:
        result['ma20'] = round(ma20, 2)
    return result

def calc_drop20d(klines):
    """20日涨跌幅"""
    if not klines or len(klines) < 2:
        return 0
    closes = [float(k.split(',')[2]) for k in klines]
    if len(closes) >= 20:
        return round((closes[-1] - closes[-20]) / closes[-20] * 100, 2)
    elif len(closes) >= 2:
        return round((closes[-1] - closes[0]) / closes[0] * 100, 2)
    return 0

def calc_vol_ratio(klines):
    """量比"""
    if not klines or len(klines) < 6:
        return 1.0
    vols = [float(k.split(',')[5]) for k in klines]
    if len(vols) < 6:
        return 1.0
    avg_vol = sum(vols[-6:-1]) / 5
    if avg_vol == 0:
        return 1.0
    return round(vols[-1] / avg_vol, 2)

def calc_trend(klines):
    """趋势判断"""
    if not klines or len(klines) < 5:
        return '震荡', 0
    closes = [float(k.split(',')[2]) for k in klines]
    # 近5日趋势
    recent = closes[-5:]
    if len(recent) < 2:
        return '震荡', 0
    # 简单线性回归斜率
    n = len(recent)
    sum_x = sum(range(n))
    sum_y = sum(recent)
    sum_xy = sum(i * recent[i] for i in range(n))
    sum_x2 = sum(i * i for i in range(n))
    denom = n * sum_x2 - sum_x * sum_x
    if denom == 0:
        return '震荡', 0
    slope = (n * sum_xy - sum_x * sum_y) / denom
    pct = slope / recent[0] * 100 if recent[0] != 0 else 0
    if pct > 0.5:
        return '上升趋势', round(pct, 1)
    elif pct < -0.5:
        return '下降趋势', round(pct, 1)
    return '震荡', round(pct, 1)

def calc_support(klines):
    """支撑价（近20日最低价）"""
    if not klines or len(klines) < 5:
        return None, None
    lows = [float(k.split(',')[4]) for k in klines]
    lookback = min(20, len(lows))
    support = min(lows[-lookback:])
    last_close = float(klines[-1].split(',')[2])
    if support > 0 and last_close > 0:
        dist = round((last_close - support) / last_close * 100, 1)
    else:
        dist = None
    return round(support, 2), dist

def fetch_capital_flow_today(codes):
    """批量获取当日主力资金流"""
    secids = [to_secid(c) for c in codes]
    url = f'https://push2.eastmoney.com/api/qt/ulist.np/get?secids={",".join(secids)}&fields=f12,f14,f62,f184,f66,f69,f72,f75&fltt=2&invt=2&_={int(time.time()*1000)}'
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)',
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            return data
    except Exception as e:
        print(f'  [WARN] fetch_capital_flow_today failed: {e}')
        return None

def fetch_flow_5d10d(secid):
    """获取5日/10日资金流"""
    url = f'https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get?secid={secid}&klt=101&lmt=12&fltt=2&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56,f57&_={int(time.time()*1000)}'
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)',
            'Referer': 'https://quote.eastmoney.com/'
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            return data
    except Exception as e:
        return None

def format_flow_amt(val):
    """格式化资金金额"""
    if val is None or val != val:  # NaN check
        return '--'
    abs_val = abs(val)
    sign = '+' if val >= 0 else '-'
    if abs_val >= 1e8:
        return f'{sign}{abs_val/1e8:.2f}亿'
    if abs_val >= 1e4:
        return f'{sign}{abs_val/1e4:.0f}万'
    return f'{sign}{abs_val:.0f}'

def compute_pinned_stock_indicators():
    """计算4只固定票的全部技术指标"""
    results = {}
    for stock in PINNED_STOCKS:
        code = stock['code']
        secid = stock['secid']
        print(f'\n=== {code} {stock["name"]} ===')

        fb = {}

        # 5分钟K线 -> KDJ
        d5 = fetch_kline(secid, 5, 200)
        if d5 and d5.get('data') and d5['data'].get('klines'):
            kdj5 = calc_kdj(d5['data']['klines'])
            if kdj5:
                fb['kd5_k'] = kdj5['k']
                fb['kd5_d'] = kdj5['d']
                print(f'  5min KDJ: K={kdj5["k"]} D={kdj5["d"]} {kdj5["cross"]}')

        time.sleep(0.3)

        # 30分钟K线 -> KDJ
        d30 = fetch_kline(secid, 30, 200)
        if d30 and d30.get('data') and d30['data'].get('klines'):
            kdj30 = calc_kdj(d30['data']['klines'])
            if kdj30:
                fb['kd30_k'] = kdj30['k']
                fb['kd30_d'] = kdj30['d']
                fb['kd30_cross'] = kdj30['cross']
                print(f'  30min KDJ: K={kdj30["k"]} D={kdj30["d"]} {kdj30["cross"]}')

        time.sleep(0.3)

        # 60分钟K线 -> KDJ
        d60 = fetch_kline(secid, 60, 200)
        if d60 and d60.get('data') and d60['data'].get('klines'):
            kdj60 = calc_kdj(d60['data']['klines'])
            if kdj60:
                fb['kd60_k'] = kdj60['k']
                fb['kd60_d'] = kdj60['d']
                fb['kd60_cross'] = kdj60['cross']
                print(f'  60min KDJ: K={kdj60["k"]} D={kdj60["d"]} {kdj60["cross"]}')

        time.sleep(0.3)

        # 日K线 -> RSI, MACD, MA, drop20d, volRatio, trend, support
        dd = fetch_kline(secid, 101, 100)
        if dd and dd.get('data') and dd['data'].get('klines'):
            dklines = dd['data']['klines']

            rsi = calc_rsi(dklines)
            if rsi is not None:
                fb['rsi'] = rsi
                print(f'  RSI: {rsi}')

            macd = calc_macd(dklines)
            if macd:
                fb['macd'] = macd['text']
                print(f'  MACD: {macd["text"]}')

            ma = calc_ma(dklines)
            if ma:
                fb.update(ma)
                print(f'  MA: {ma["ma"]} (MA5={ma.get("ma5","?")}, MA20={ma.get("ma20","?")})')

            fb['drop20d'] = calc_drop20d(dklines)
            fb['volRatio'] = calc_vol_ratio(dklines)
            print(f'  drop20d: {fb["drop20d"]}%  volRatio: {fb["volRatio"]}')

            trend, trend_pct = calc_trend(dklines)
            print(f'  trend: {trend} ({trend_pct}%)')

            support, support_dist = calc_support(dklines)
            if support:
                fb['supportPrice'] = support
                fb['supportDist'] = support_dist
                print(f'  support: {support} ({support_dist}%)')

            # 30日高开率（简化计算）
            opens = [float(k.split(',')[1]) for k in dklines]
            prev_closes = [float(k.split(',')[2]) for k in dklines]
            open_up_count = 0
            total_count = 0
            for i in range(1, min(30, len(opens))):
                if opens[i] > prev_closes[i-1]:
                    open_up_count += 1
                total_count += 1
            if total_count > 0:
                fb['openRate30d'] = round(open_up_count / total_count * 100, 1)
                fb['openRateTotal'] = fb['openRate30d']
                fb['openRateScore'] = 2 if fb['openRate30d'] >= 40 else 1

        time.sleep(0.3)

        # 资金流5日/10日
        flow_data = fetch_flow_5d10d(secid)
        if flow_data and flow_data.get('data') and flow_data['data'].get('klines'):
            flows = []
            for k in flow_data['data']['klines']:
                parts = k.split(',')
                if len(parts) >= 2:
                    try:
                        flows.append(float(parts[1]))
                    except:
                        flows.append(0)
            if len(flows) >= 5:
                f5 = sum(flows[-5:])
                fb['mainFlow'] = f'5日{format_flow_amt(f5)}'
                fb['mainFlowDate'] = '5日累计'
                print(f'  flow5d: {format_flow_amt(f5)}')
            if len(flows) >= 10:
                f10 = sum(flows[-10:])
                print(f'  flow10d: {format_flow_amt(f10)}')

        results[code] = fb
        print(f'  => {len(fb)} fields computed')

    return results

def compute_all_capital_flow():
    """计算全部34只票的当日主力资金流"""
    print('\n\n=== 获取全部34只票当日主力资金流 ===')
    data = fetch_capital_flow_today(ALL_STOCKS_CODES)
    cache = {}
    if data and data.get('data') and data['data'].get('diff'):
        for item in data['data']['diff']:
            num = str(item.get('f12', ''))
            if num.startswith('6') or num.startswith('9'):
                code = 'sh' + num
            else:
                code = 'sz' + num
            main_net = item.get('f62')
            main_pct = item.get('f184')
            entry = {}
            if main_net is not None and main_net != '-':
                entry['mainNetInflow'] = float(main_net)
                entry['mainFlowStr'] = format_flow_amt(float(main_net))
                entry['date'] = datetime.now().strftime('%Y-%m-%d')
            if main_pct is not None and main_pct != '-':
                entry['mainFlowPct'] = float(main_pct)
            if entry:
                cache[code] = entry
                print(f'  {code}: {entry.get("mainFlowStr","?")} ({entry.get("mainFlowPct","?")}%)')

    # 获取5日/10日资金流
    print('\n=== 获取5日/10日资金流 ===')
    for code in ALL_STOCKS_CODES:
        secid = to_secid(code)
        flow_data = fetch_flow_5d10d(secid)
        if flow_data and flow_data.get('data') and flow_data['data'].get('klines'):
            flows = []
            for k in flow_data['data']['klines']:
                parts = k.split(',')
                if len(parts) >= 2:
                    try:
                        flows.append(float(parts[1]))
                    except:
                        flows.append(0)
            if code not in cache:
                cache[code] = {}
            if len(flows) >= 5:
                f5 = sum(flows[-5:])
                cache[code]['flow_5d_wan'] = round(f5, 1)
                cache[code]['flow_5d_str'] = format_flow_amt(f5)
            if len(flows) >= 10:
                f10 = sum(flows[-10:])
                cache[code]['flow_10d_wan'] = round(f10, 1)
                cache[code]['flow_10d_str'] = format_flow_amt(f10)
        time.sleep(0.15)

    return cache

def main():
    print(f'=== 预计算开始 {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} ===')

    # 1. 计算4只固定票技术指标
    pinned_fb = compute_pinned_stock_indicators()

    # 2. 计算全部34只票资金流
    capital_cache = compute_all_capital_flow()

    # 3. 输出结果
    output = {
        'pinned_fallback': pinned_fb,
        'capital_flow_cache': capital_cache,
        'update_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }

    with open('pinned_precompute.json', 'w') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f'\n=== 预计算完成，输出到 pinned_precompute.json ===')
    print(f'固定票技术指标: {len(pinned_fb)} 只')
    print(f'资金流缓存: {len(capital_cache)} 只')

    # 同时输出可直接注入的JS代码片段
    print('\n=== KDJ_FALLBACK 补充片段 ===')
    fb_js = json.dumps(pinned_fb, ensure_ascii=False)
    print(f'Object.assign(KDJ_FALLBACK, {fb_js});')

    print('\n=== CAPITAL_FLOW_CACHE 补充片段 ===')
    cf_js = json.dumps(capital_cache, ensure_ascii=False)
    print(f'Object.assign(CAPITAL_FLOW_CACHE, {cf_js});')

if __name__ == '__main__':
    main()
