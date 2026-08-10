#!/usr/bin/env python3
"""
高开预判新增因子数据获取
========================
为TOP50高开率股票获取4项新增数据：
1. 当天日K线(高/低/收/量) → 收盘位置 + 量比
2. 5分K线最后6根 → 尾盘30分钟走势
3. 上证指数涨跌 → 大盘环境

输出 extra_factors.json，嵌入仪表盘用于预判模型。
"""
import urllib.request
import json
import time
import os
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

INPUT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "daily_predictions.json")
OUTPUT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "extra_factors.json")
MAX_WORKERS = 6
TIMEOUT = 15


def fetch_daily_sina(code, datalen=10):
    """新浪日K线API（scale=240）"""
    symbol = ('sz' + code) if code.startswith(('0', '3')) else ('sh' + code)
    url = (f"http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
           f"CN_MarketData.getKLineData?symbol={symbol}&scale=240&datalen={datalen}")
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                raw = resp.read().decode('utf-8')
                if not raw or len(raw) < 20:
                    if attempt < 2:
                        time.sleep(0.5)
                        continue
                    return None
                data = json.loads(raw)
                if not data or not isinstance(data, list):
                    return None
                klines = []
                for k in data:
                    klines.append({
                        'date': k['day'][:10],
                        'open': float(k['open']),
                        'close': float(k['close']),
                        'high': float(k['high']),
                        'low': float(k['low']),
                        'volume': int(float(k['volume']))
                    })
                return klines
        except Exception:
            if attempt < 2:
                time.sleep(0.5)
    return None


def fetch_5min_em(code, lmt=100):
    """东方财富5分K线API"""
    market = '1' if code.startswith('6') else '0'
    secid = f"{market}.{code}"
    url = (f"https://push2his.eastmoney.com/api/qt/stock/kline/get?"
           f"secid={secid}&ut=2869ffafee25fa5d66c39e8d498df3a8"
           f"&fields1=f1,f2,f3,f4,f5,f6"
           f"&fields2=f51,f52,f53,f54,f55,f56,f57"
           f"&klt=5&fqt=1&end=20500101&lmt={lmt}")
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)',
                'Referer': 'https://quote.eastmoney.com'
            })
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                raw = resp.read().decode('utf-8')
                if not raw or len(raw) < 20:
                    if attempt < 2:
                        time.sleep(0.5)
                        continue
                    return None
                data = json.loads(raw)
                if not data or not data.get('data') or not data['data'].get('klines'):
                    return None
                klines = []
                for k in data['data']['klines']:
                    f = k.split(',')
                    klines.append({
                        'datetime': f[0],
                        'date': f[0][:10],
                        'time': f[0][11:16] if len(f[0]) > 11 else '',
                        'open': float(f[1]),
                        'close': float(f[2]),
                        'high': float(f[3]),
                        'low': float(f[4]),
                        'volume': int(float(f[5])),
                        'amount': float(f[6]) if len(f) > 6 else 0
                    })
                return klines
        except Exception:
            if attempt < 2:
                time.sleep(0.5)
    return None


def fetch_index_daily():
    """获取上证指数日K线（新浪API），计算大盘涨跌"""
    url = ("http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
           "CN_MarketData.getKLineData?symbol=sh000001&scale=240&datalen=6")
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                raw = resp.read().decode('utf-8')
                data = json.loads(raw)
                if not data or not isinstance(data, list):
                    return None
                klines = []
                for k in data:
                    klines.append({
                        'date': k['day'][:10],
                        'close': float(k['close'])
                    })
                return klines
        except Exception:
            if attempt < 2:
                time.sleep(0.5)
    return None


def calc_close_position(klines):
    """计算收盘位置 = (收盘-最低)/(最高-最低)
    >0.8 光头阳线，次日高开概率大
    <0.3 长上影，次日低开概率大
    """
    if not klines or len(klines) < 1:
        return None
    last = klines[-1]
    high, low, close = last['high'], last['low'], last['close']
    if high == low:
        return 0.5
    pos = (close - low) / (high - low)
    return round(pos, 3)


def calc_volume_ratio(klines):
    """计算量比 = 当天量 / 近5日均量"""
    if not klines or len(klines) < 6:
        return None
    today_vol = klines[-1]['volume']
    avg_vol = sum(k['volume'] for k in klines[-6:-1]) / 5
    if avg_vol == 0:
        return 1.0
    return round(today_vol / avg_vol, 2)


def calc_tail_movement(klines_5min):
    """计算尾盘30分钟走势（5分K线最后6根的涨跌）
    返回尾盘涨跌幅%
    """
    if not klines_5min or len(klines_5min) < 6:
        return None
    last6 = klines_5min[-6:]
    start_price = last6[0]['open']
    end_price = last6[-1]['close']
    if start_price == 0:
        return 0
    return round((end_price - start_price) / start_price * 100, 2)


def calc_market_trend(index_klines):
    """计算大盘涨跌（上证指数当天涨跌幅%）"""
    if not index_klines or len(index_klines) < 2:
        return 0
    today = index_klines[-1]['close']
    yesterday = index_klines[-2]['close']
    if yesterday == 0:
        return 0
    return round((today - yesterday) / yesterday * 100, 2)


def process_stock(code, name):
    """获取单只股票的新增因子数据"""
    result = {'code': code, 'name': name}

    # 1. 日K线(新浪) → 收盘位置 + 量比
    daily = fetch_daily_sina(code, datalen=10)
    if daily and len(daily) >= 2:
        result['close_pos'] = calc_close_position(daily)
        result['volume_ratio'] = calc_volume_ratio(daily)
        last = daily[-1]
        result['day_high'] = last['high']
        result['day_low'] = last['low']
        result['day_close'] = last['close']
        result['day_open'] = last['open']
        # 日内涨幅
        if last['open'] > 0:
            result['day_change'] = round((last['close'] - last['open']) / last['open'] * 100, 2)
        else:
            result['day_change'] = 0
    else:
        result['close_pos'] = None
        result['volume_ratio'] = None
        result['day_change'] = None

    # 2. 5分K线(东方财富) → 尾盘30分钟走势
    k5 = fetch_5min_em(code, lmt=100)
    if k5 and len(k5) >= 6:
        result['tail_move'] = calc_tail_movement(k5)
    else:
        result['tail_move'] = None

    time.sleep(0.1)
    return result


def main():
    print("=" * 60)
    print("高开预判新增因子数据获取")
    print("=" * 60)

    # 读取TOP50
    with open(INPUT_FILE, 'r') as f:
        pred = json.load(f)
    top50 = pred.get('top_gap_up_rate', [])
    print(f"读取TOP50: {len(top50)} 只股票")

    # 获取上证指数（全局，只取一次）
    print("\n获取上证指数...")
    index_klines = fetch_index_daily()
    market_trend = calc_market_trend(index_klines)
    print(f"  上证指数当天涨跌: {market_trend:+.2f}%")

    # 多线程获取每只股票的数据
    print(f"\n开始获取 {len(top50)} 只股票的日K线和5分K线...")
    results = {}
    errors = []
    success = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(process_stock, s['code'], s['name']): s
            for s in top50
        }
        for i, future in enumerate(as_completed(futures)):
            stock = futures[future]
            try:
                r = future.result()
                results[r['code']] = {
                    'close_pos': r.get('close_pos'),
                    'volume_ratio': r.get('volume_ratio'),
                    'tail_move': r.get('tail_move'),
                    'day_change': r.get('day_change'),
                    'day_high': r.get('day_high'),
                    'day_low': r.get('day_low'),
                    'day_close': r.get('day_close')
                }
                success += 1
            except Exception as e:
                errors.append(f"{stock['code']} {stock['name']}: {e}")
            if (i + 1) % 10 == 0:
                print(f"  进度: {i+1}/{len(top50)} (成功{success})")

    print(f"\n完成: 成功 {success}, 失败 {len(errors)}")
    if errors:
        for e in errors[:5]:
            print(f"  失败: {e}")

    # 输出
    output = {
        'scan_date': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'market_trend': market_trend,
        'total_success': success,
        'total_errors': len(errors),
        'factors': results
    }

    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n输出: {OUTPUT_FILE}")

    # 打印样例
    print("\n=== 样例数据（前8只）===")
    for code, data in list(results.items())[:8]:
        cp = data.get('close_pos')
        vr = data.get('volume_ratio')
        tm = data.get('tail_move')
        dc = data.get('day_change')
        cp_str = f"{cp:.2f}" if cp is not None else "--"
        vr_str = f"{vr:.2f}" if vr is not None else "--"
        tm_str = f"{tm:+.2f}%" if tm is not None else "--"
        dc_str = f"{dc:+.2f}%" if dc is not None else "--"
        # 收盘位置标签
        if cp is not None:
            if cp > 0.8: cp_tag = "光头阳线"
            elif cp < 0.3: cp_tag = "长上影"
            else: cp_tag = "中位"
        else:
            cp_tag = "--"
        print(f"  {code}  收盘位置{cp_str}({cp_tag})  量比{vr_str}  尾盘{tm_str}  日内{dc_str}")

    print(f"\n大盘环境: 上证指数 {market_trend:+.2f}%")


if __name__ == '__main__':
    main()
