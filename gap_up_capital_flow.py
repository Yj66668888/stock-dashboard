#!/usr/bin/env python3
"""
全市场主力资金流向计算 — v2.0
数据源：东方财富Level2主力资金API（真实大单数据）+ 新浪K线（OHLCV辅助）
输出：capital_flow.json
"""
import urllib.request, json, time, os, sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

DAILY_FILE = os.path.join(os.path.dirname(__file__), 'daily_predictions.json')
OUTPUT_FILE = os.path.join(os.path.dirname(__file__), 'capital_flow.json')

MAIN_BOARD_PREFIXES = ('00', '60')

# 熔断器：新浪API连续失败计数，超过阈值后跳过
_sina_fail_count = 0
_SINA_FAIL_THRESHOLD = 10  # 连续失败10次后熔断

SINA_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
    'Referer': 'https://finance.sina.com.cn/'
}
EM_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
    'Referer': 'https://data.eastmoney.com/'
}

def get_sina_kline(code, datalen=12):
    """新浪日K线（提供OHLCV）— 带熔断器"""
    global _sina_fail_count
    if _sina_fail_count >= _SINA_FAIL_THRESHOLD:
        return None  # 熔断：跳过新浪API
    sina_code = ('sh' if code.startswith('6') else 'sz') + code
    url = f"http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol={sina_code}&scale=240&ma=no&datalen={datalen}"
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=SINA_HEADERS)
            with urllib.request.urlopen(req, timeout=10) as r:
                _sina_fail_count = 0  # 成功则重置
                return json.loads(r.read().decode())
        except Exception:
            if attempt < 2:
                time.sleep(0.3)
    _sina_fail_count += 1  # 失败则计数
    return None

def get_eastmoney_fflow(code, datalen=12):
    """东方财富主力资金流向 — 真实Level2大单数据
    返回: [{'date': 'YYYY-MM-DD', 'main_net': 元, 'chg_pct': %}, ...]
    """
    market = '1' if code.startswith('6') else '0'
    secid = f'{market}.{code}'
    url = (f'https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get'
           f'?secid={secid}&fields1=f1,f2,f3,f7'
           f'&fields2=f51,f52,f53,f54,f55,f56,f57'
           f'&lmt={datalen}')
    
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=EM_HEADERS)
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read().decode())
            klines = data.get('data', {}).get('klines', [])
            results = []
            for k in klines:
                parts = k.split(',')
                results.append({
                    'date': parts[0],
                    'main_net': float(parts[1]) if len(parts) > 1 else 0,
                    'chg_pct': float(parts[6]) if len(parts) > 6 else 0
                })
            return results
        except Exception:
            if attempt < 2:
                time.sleep(0.5)
    return None

def get_eastmoney_kline(code, datalen=12):
    """东方财富日K线（OHLCV）— 新浪API不可用时的备用源
    返回格式兼容新浪: [{'day': date, 'open':..., 'close':..., 'high':..., 'low':..., 'volume':...}, ...]
    """
    market = '1' if code.startswith('6') else '0'
    secid = f'{market}.{code}'
    url = (f'https://push2his.eastmoney.com/api/qt/stock/kline/get'
           f'?secid={secid}&fields1=f1,f2,f3,f4,f5,f6'
           f'&fields2=f51,f52,f53,f54,f55,f56,f57,f58'
           f'&klt=101&fqt=0&lmt={datalen}&end=20500101')
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=EM_HEADERS)
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read().decode())
            klines = data.get('data', {}).get('klines', [])
            results = []
            for k in klines:
                parts = k.split(',')
                if len(parts) >= 6:
                    results.append({
                        'day': parts[0],
                        'open': float(parts[1]),
                        'close': float(parts[2]),
                        'high': float(parts[3]),
                        'low': float(parts[4]),
                        'volume': float(parts[5])
                    })
            return results if results else None
        except Exception:
            if attempt < 2:
                time.sleep(0.5)
    return None

def merge_capital_flow(em_data, kls, name, code):
    """合并东方财富主力资金 + OHLCV，输出兼容格式"""
    if not em_data:
        return None
    # kls可能为None（新浪+东方财富K线都失败时），用空列表兜底
    if not kls:
        kls = []
    
    # 日期→新浪K线索引
    sina_map = {}
    for k in kls:
        sina_map[k['day']] = k
    
    em_5d = em_data[-5:] if len(em_data) >= 5 else em_data
    em_10d = em_data[-10:] if len(em_data) >= 10 else em_data
    
    # ---- 5日 daily ----
    daily_flows = []
    up_days = down_days = 0
    total_amount = 0
    total_flow_5d = 0
    max_upper_shadow = 0
    vol_ratio = 1.0
    
    vols_5d = []
    for item in em_5d:
        sk = sina_map.get(item['date'])
        if sk:
            vols_5d.append(float(sk['volume']))
    avg_vol_5 = sum(vols_5d[:-1]) / max(len(vols_5d) - 1, 1) if len(vols_5d) > 1 else (vols_5d[0] if vols_5d else 1)
    
    for i, item in enumerate(em_5d):
        d = item['date']
        main_net_wan = round(item['main_net'] / 10000, 0)
        sk = sina_map.get(d)
        
        chg_pct = item['chg_pct']
        amount_yi = 0
        upper_shadow = 0
        
        if sk:
            o, h, l, c, v = float(sk['open']), float(sk['high']), float(sk['low']), float(sk['close']), float(sk['volume'])
            amount_yi = round(v * c / 1e8, 2)
            upper_shadow = round((h - max(o, c)) / o * 100, 2)
            chg_pct = round((c - o) / o * 100, 2)
        
        if chg_pct > 0:
            up_days += 1
        elif chg_pct < 0:
            down_days += 1
        
        total_amount += amount_yi * 1e8
        total_flow_5d += main_net_wan
        
        if upper_shadow > max_upper_shadow:
            max_upper_shadow = upper_shadow
        
        if i == len(em_5d) - 1 and sk:
            latest_v = float(sk['volume'])
            vol_ratio = latest_v / avg_vol_5 if avg_vol_5 > 0 else 1.0
        
        daily_flows.append({
            'date': d,
            'chg': round(chg_pct, 2),
            'amount_yi': amount_yi,
            'flow_wan': main_net_wan,
            'upper_shadow': upper_shadow
        })
    
    # ---- 10日 daily_10d ----
    total_flow_10d = 0
    daily_10d_list = []
    for item in em_10d:
        d = item['date']
        main_net_wan = round(item['main_net'] / 10000, 0)
        sk = sina_map.get(d)
        
        chg_pct = item['chg_pct']
        amount_yi = 0
        upper_shadow = 0
        
        if sk:
            o, h, c, v = float(sk['open']), float(sk['high']), float(sk['close']), float(sk['volume'])
            amount_yi = round(v * c / 1e8, 2)
            upper_shadow = round((h - max(o, c)) / o * 100, 2)
            chg_pct = round((c - o) / o * 100, 2)
        
        total_flow_10d += main_net_wan
        
        daily_10d_list.append({
            'date': d,
            'chg': round(chg_pct, 2),
            'amount_yi': amount_yi,
            'flow_wan': main_net_wan,
            'upper_shadow': upper_shadow
        })
    
    latest_sina = kls[-1] if kls else None
    if latest_sina:
        latest_close = float(latest_sina['close'])
        latest_open = float(latest_sina['open'])
        latest_chg = (latest_close - latest_open) / latest_open * 100 if latest_open > 0 else 0
        latest_amount_yi = round(float(latest_sina['volume']) * latest_close / 1e8, 2)
    else:
        # 无K线数据时用东方财富的涨跌幅
        latest_chg = em_data[-1]['chg_pct'] if em_data else 0
        latest_close = 0
        latest_amount_yi = 0
    
    return {
        'name': name,
        'code': code,
        'flow_5d_wan': round(total_flow_5d, 0),
        'flow_10d_wan': round(total_flow_10d, 0),
        'up_days_5d': up_days,
        'down_days_5d': down_days,
        'avg_amount_yi': round(total_amount / max(len(em_5d), 1) / 1e8, 2),
        'latest_chg': round(latest_chg, 2),
        'latest_amount_yi': latest_amount_yi,
        'vol_ratio': round(vol_ratio, 2),
        'max_upper_shadow': round(max_upper_shadow, 2),
        'daily': daily_flows,
        'daily_10d': daily_10d_list
    }

def process_stock(code, name):
    """处理单只票：双源拉取 + 合并"""
    em_data = get_eastmoney_fflow(code, 12)
    if not em_data:
        return code, None
    
    # 新浪K线优先，失败时用东方财富K线备用
    kls = get_sina_kline(code, 12)
    if not kls:
        kls = get_eastmoney_kline(code, 12)
    
    # 即使kls为None也尝试合并（用东方财富数据+默认值）
    flow = merge_capital_flow(em_data, kls, name, code)
    return code, flow

def main():
    print(f"=== 🔥 全市场主力资金计算 v2.0 (东方财富Level2) {datetime.now().strftime('%Y-%m-%d %H:%M')} ===")
    
    with open(DAILY_FILE, 'r') as f:
        data = json.load(f)
    
    all_results = data.get('all_results', [])
    stocks = [(r['code'], r['name']) for r in all_results if r['code'].startswith(MAIN_BOARD_PREFIXES)]
    
    print(f"待扫描: {len(stocks)}只主板票")
    print(f"数据源: 东方财富 Level2 (真实大单) + 新浪K线 (OHLCV)")
    
    results = {}
    success = fail = 0
    start = time.time()
    
    # 降并发到5线程 + 礼貌延迟，避免东方财富API封IP
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(process_stock, code, name): code for code, name in stocks}
        for i, future in enumerate(as_completed(futures)):
            code, flow = future.result()
            if flow:
                results[code] = flow
                success += 1
            else:
                fail += 1
            
            if (i + 1) % 200 == 0:
                elapsed = time.time() - start
                print(f"  进度: {i+1}/{len(stocks)} 成功{success} 失败{fail} 耗时{elapsed:.0f}s")
    
    elapsed = time.time() - start
    print(f"\n完成: 成功{success} 失败{fail} 耗时{elapsed:.0f}s ({elapsed/60:.1f}分钟)")
    
    # 安全网：成功率过低时合并旧数据而非覆盖（防止API全挂时数据被清空）
    if success < len(stocks) * 0.1 and os.path.exists(OUTPUT_FILE):
        print(f"WARNING: 成功率仅{success}/{len(stocks)}({success/len(stocks)*100:.0f}%)，合并旧数据而非覆盖")
        try:
            with open(OUTPUT_FILE, 'r', encoding='utf-8') as f:
                old_data = json.load(f)
            old_stocks = old_data.get('stocks', {})
            merged = dict(old_stocks)
            merged.update(results)
            results = merged
            print(f"  合并后: {len(results)}只 (旧{len(old_stocks)} + 新{success})")
        except Exception as e:
            print(f"  合并失败({e})，仅写入新数据{success}只")
    
    output = {
        'update_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'total': len(results),
        'data_source': 'eastmoney_level2',
        'stocks': results
    }
    
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, separators=(',', ':'))
    
    print(f"输出: {OUTPUT_FILE} ({os.path.getsize(OUTPUT_FILE)/1024:.0f}KB)")
    
    # TOP/BOTTOM 10 主力资金
    sorted_stocks = sorted(results.items(), key=lambda x: x[1]['flow_5d_wan'], reverse=True)
    print("\n=== 🔥 5日主力资金 TOP10 ===")
    for code, d in sorted_stocks[:10]:
        flow_str = f"{d['flow_5d_wan']/10000:+.2f}亿" if abs(d['flow_5d_wan']) >= 10000 else f"{d['flow_5d_wan']:+.0f}万"
        print(f"  {code} {d['name']}: {flow_str} 涨{d['up_days_5d']}天 量比{d['vol_ratio']}")
    print("\n=== 🔥 5日主力资金 BOTTOM10 ===")
    for code, d in sorted_stocks[-10:]:
        flow_str = f"{d['flow_5d_wan']/10000:+.2f}亿" if abs(d['flow_5d_wan']) >= 10000 else f"{d['flow_5d_wan']:+.0f}万"
        print(f"  {code} {d['name']}: {flow_str} 涨{d['up_days_5d']}天 量比{d['vol_ratio']}")

if __name__ == '__main__':
    main()
