#!/usr/bin/env python3
"""
全市场主力资金流向代理指标计算
数据源：新浪财经K线API（稳定，已验证）
代理指标：成交额 × 涨幅% = 资金流向近似值
输出：capital_flow.json
"""
import urllib.request, json, time, os, sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

DAILY_FILE = os.path.join(os.path.dirname(__file__), 'daily_predictions.json')
OUTPUT_FILE = os.path.join(os.path.dirname(__file__), 'capital_flow.json')

MAIN_BOARD_PREFIXES = ('00', '60')

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
    'Referer': 'https://finance.sina.com.cn/'
}

def get_sina_kline(code, datalen=12):
    """新浪日K线（默认12日，覆盖10个交易日）"""
    sina_code = ('sh' if code.startswith('6') else 'sz') + code
    url = f"http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol={sina_code}&scale=240&ma=no&datalen={datalen}"
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=8) as r:
                return json.loads(r.read().decode())
        except Exception:
            if attempt < 2:
                time.sleep(0.3)
            else:
                return None

def calc_capital_flow(kls):
    """计算资金流向代理指标（5日+10日）"""
    if not kls or len(kls) < 2:
        return None
    
    # 5日窗口
    kls_5d = kls[-5:] if len(kls) >= 5 else kls
    # 10日窗口
    kls_10d = kls[-10:] if len(kls) >= 10 else kls
    
    daily_flows = []
    up_days = 0
    down_days = 0
    total_amount = 0
    total_flow = 0
    max_upper_shadow = 0
    vol_ratio = 1.0

    vols_5d = [float(k['volume']) for k in kls_5d]
    avg_vol_5 = sum(vols_5d[:-1]) / max(len(vols_5d) - 1, 1) if len(vols_5d) > 1 else vols_5d[0]

    # 10日资金流合计
    total_flow_10d = 0
    total_amount_10d = 0
    for k in kls_10d:
        o = float(k['open'])
        c = float(k['close'])
        v = float(k['volume'])
        chg_pct = (c - o) / o * 100
        amount = v * c
        flow = amount * chg_pct / 100 / 10000
        total_flow_10d += flow
        total_amount_10d += amount

    for i, k in enumerate(kls_5d):
        o = float(k['open'])
        h = float(k['high'])
        l = float(k['low'])
        c = float(k['close'])
        v = float(k['volume'])
        chg_pct = (c - o) / o * 100
        amount = v * c  # 成交额(元)
        flow = amount * chg_pct / 100 / 10000  # 代理资金流(万元)

        if chg_pct > 0:
            up_days += 1
        elif chg_pct < 0:
            down_days += 1

        total_amount += amount
        total_flow += flow

        # 上影线（冲高回落风险）
        upper_shadow = (h - max(o, c)) / o * 100
        if upper_shadow > max_upper_shadow:
            max_upper_shadow = upper_shadow

        # 今日量比
        if i == len(kls_5d) - 1:
            vol_ratio = v / avg_vol_5 if avg_vol_5 > 0 else 1.0

        daily_flows.append({
            'date': k['day'],
            'chg': round(chg_pct, 2),
            'amount_yi': round(amount / 1e8, 2),  # 亿元
            'flow_wan': round(flow, 0),  # 万元
            'upper_shadow': round(upper_shadow, 2)
        })

    # 最近一日数据
    latest = kls_5d[-1]
    latest_close = float(latest['close'])
    latest_open = float(latest['open'])
    latest_chg = (latest_close - latest_open) / latest_open * 100

    return {
        'flow_5d_wan': round(total_flow, 0),  # 5日累计代理资金流(万元)
        'flow_10d_wan': round(total_flow_10d, 0),  # 10日累计代理资金流(万元)【新增】
        'up_days_5d': up_days,
        'down_days_5d': down_days,
        'avg_amount_yi': round(total_amount / len(kls_5d) / 1e8, 2),  # 日均成交额(亿)
        'latest_chg': round(latest_chg, 2),
        'latest_amount_yi': round(float(latest['volume']) * latest_close / 1e8, 2),
        'vol_ratio': round(vol_ratio, 2),
        'max_upper_shadow': round(max_upper_shadow, 2),
        'daily': daily_flows,  # 5日逐日数据
        'daily_10d': [         # 10日逐日数据【新增】
            {
                'date': k['day'],
                'chg': round((float(k['close']) - float(k['open'])) / float(k['open']) * 100, 2),
                'amount_yi': round(float(k['volume']) * float(k['close']) / 1e8, 2),
                'flow_wan': round(float(k['volume']) * float(k['close']) * ((float(k['close']) - float(k['open'])) / float(k['open']) * 100) / 100 / 10000, 0),
                'upper_shadow': round((float(k['high']) - max(float(k['open']), float(k['close']))) / float(k['open']) * 100, 2)
            }
            for k in kls_10d
        ]
    }

def process_stock(code, name):
    """处理单只票"""
    kls = get_sina_kline(code, 12)
    if not kls:
        return code, None
    flow = calc_capital_flow(kls)
    if flow:
        flow['name'] = name
        flow['code'] = code
    return code, flow

def main():
    print(f"=== 全市场资金流向代理指标计算 {datetime.now().strftime('%Y-%m-%d %H:%M')} ===")

    # 读取全市场票列表
    with open(DAILY_FILE, 'r') as f:
        data = json.load(f)

    all_results = data.get('all_results', [])
    stocks = []
    for r in all_results:
        code = r['code']
        if code.startswith(MAIN_BOARD_PREFIXES):
            stocks.append((code, r['name']))

    print(f"待扫描: {len(stocks)}只主板票")

    # 并发获取（10线程，避免被封）
    results = {}
    success = 0
    fail = 0
    start = time.time()

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(process_stock, code, name): code for code, name in stocks}
        for i, future in enumerate(as_completed(futures)):
            code, flow = future.result()
            if flow:
                results[code] = flow
                success += 1
            else:
                fail += 1

            if (i + 1) % 500 == 0:
                elapsed = time.time() - start
                print(f"  进度: {i+1}/{len(stocks)} 成功{success} 失败{fail} 耗时{elapsed:.0f}s")

    elapsed = time.time() - start
    print(f"\n完成: 成功{success} 失败{fail} 耗时{elapsed:.0f}s ({elapsed/60:.1f}分钟)")

    # 输出
    output = {
        'update_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'total': len(results),
        'stocks': results
    }

    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, separators=(',', ':'))

    print(f"输出: {OUTPUT_FILE} ({os.path.getsize(OUTPUT_FILE)/1024:.0f}KB)")

    # 打印TOP10资金流入
    sorted_stocks = sorted(results.items(), key=lambda x: x[1]['flow_5d_wan'], reverse=True)
    print("\n=== 5日累计代理资金流 TOP10 ===")
    for code, d in sorted_stocks[:10]:
        print(f"  {code} {d['name']}: {d['flow_5d_wan']:+.0f}万 涨{d['up_days_5d']}天 量比{d['vol_ratio']}")
    print("\n=== 5日累计代理资金流 BOTTOM10 ===")
    for code, d in sorted_stocks[-10:]:
        print(f"  {code} {d['name']}: {d['flow_5d_wan']:+.0f}万 涨{d['up_days_5d']}天 量比{d['vol_ratio']}")

if __name__ == '__main__':
    main()
