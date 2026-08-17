#!/usr/bin/env python3
"""快速更新仪表盘股票的资金流数据（只更新仪表盘上的~57只票）
   v2: 用subprocess curl替代urllib，绕过本地代理+TLS兼容问题
"""
import json, re, subprocess, time, os
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE = "/Users/fuckyouasshole/WorkBuddy/2026-07-01-11-18-34"
DAILY_FILE = f"{BASE}/daily_predictions.json"
OUTPUT_FILE = f"{BASE}/capital_flow.json"
DEPLOY_HTML = f"{BASE}/deploy/index.html"

# 清除代理环境变量（本地代理会导致东方财富/新浪API返回空）
_CLEAN_ENV = {k: v for k, v in os.environ.items() if 'proxy' not in k.lower()}


def curl_get_json(url, retries=5, timeout=10):
    """用subprocess curl获取JSON，绕过代理，自动重试"""
    for attempt in range(retries):
        try:
            result = subprocess.run(
                ['curl', '-s', '--noproxy', '*', '--connect-timeout', str(timeout), url],
                capture_output=True, text=True, timeout=timeout + 5, env=_CLEAN_ENV
            )
            if result.returncode == 0 and result.stdout:
                return json.loads(result.stdout)
        except (json.JSONDecodeError, subprocess.TimeoutExpired, Exception):
            pass
        if attempt < retries - 1:
            time.sleep(0.3)
    return None


def get_eastmoney_fflow(code, datalen=12):
    """东方财富资金流API"""
    market = '1' if code.startswith('6') else '0'
    url = (f'https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get'
           f'?secid={market}.{code}&fields1=f1,f2,f3,f7'
           f'&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65'
           f'&klt=101&lmt={datalen}'
           f'&ut=b2884a393a59ad64002292a3e90d46a5')
    data = curl_get_json(url, retries=5)
    if not data:
        return None
    klines = data.get('data', {}).get('klines', [])
    if not klines:
        return None
    daily = []
    for k in klines:
        parts = k.split(',')
        if len(parts) >= 7:
            daily.append({
                'date': parts[0],
                'flow_wan': round(float(parts[1]) / 10000, 2),
                'chg_pct': float(parts[-1]) if parts[-1] else 0
            })
    return daily


def get_sina_kline(code, datalen=12):
    """新浪K线API"""
    sina_code = ('sh' if code.startswith('6') else 'sz') + code
    url = (f'http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/'
           f'CN_MarketData.getKLineData?symbol={sina_code}&scale=240&ma=no&datalen={datalen}')
    data = curl_get_json(url, retries=3)
    return data if data else None


def process_stock(code, name):
    """处理单只股票"""
    # 1. 获取资金流
    daily = get_eastmoney_fflow(code, 12)
    if not daily:
        return code, None

    # 2. 获取K线（用于涨跌幅和成交量）
    kls = get_sina_kline(code, 12)

    # 3. 合并数据
    sina_map = {}
    if kls:
        for k in kls:
            sina_map[k['day']] = k

    for d in daily:
        sk = sina_map.get(d['date'])
        if sk:
            d['close'] = float(sk['close'])
            d['open'] = float(sk['open'])
            d['volume'] = float(sk['volume'])

    # 4. 计算汇总
    recent_5 = daily[-5:] if len(daily) >= 5 else daily
    recent_10 = daily[-10:] if len(daily) >= 10 else daily
    flow_5d = sum(d.get('flow_wan', 0) for d in recent_5)
    flow_10d = sum(d.get('flow_wan', 0) for d in recent_10)

    latest = daily[-1] if daily else {}
    latest_close = latest.get('close', 0)
    latest_open = latest.get('open', 0)
    latest_chg = ((latest_close - latest_open) / latest_open * 100) if latest_open else latest.get('chg_pct', 0)

    # 5. 计算连续流入天数
    up_days = 0
    for d in reversed(daily):
        if d.get('flow_wan', 0) > 0:
            up_days += 1
        else:
            break

    # 6. 计算量比
    vol_ratio = 1.0
    if kls and len(kls) >= 6:
        recent_vols = [float(k['volume']) for k in kls[-5:]]
        prev_vols = [float(k['volume']) for k in kls[-10:-5]] if len(kls) >= 10 else [float(k['volume']) for k in kls[:-5]]
        avg_recent = sum(recent_vols) / len(recent_vols) if recent_vols else 1
        avg_prev = sum(prev_vols) / len(prev_vols) if prev_vols else 1
        vol_ratio = round(avg_recent / avg_prev, 2) if avg_prev > 0 else 1.0

    result = {
        'name': name,
        'daily': daily,
        'flow_5d_wan': round(flow_5d, 2),
        'flow_10d_wan': round(flow_10d, 2),
        'latest_flow_wan': latest.get('flow_wan', 0),
        'latest_chg': round(latest_chg, 2),
        'latest_date': latest.get('date', ''),
        'up_days_5d': up_days,
        'vol_ratio': vol_ratio,
        'latest_amount_yi': round(latest.get('volume', 0) * latest_close / 1e8, 2) if latest_close else 0,
    }

    return code, result


def main():
    print(f"=== 快速资金流更新 {datetime.now().strftime('%Y-%m-%d %H:%M')} ===")

    # 1. 从deploy/index.html提取仪表盘股票代码
    with open(DEPLOY_HTML) as f:
        html = f.read()
    codes_raw = re.findall(r'"code":\s*"((?:sh|sz)\d+)"', html)
    codes = list(set(codes_raw))
    print(f"仪表盘股票: {len(codes)}只")

    # 2. 从daily_predictions.json获取股票名称
    name_map = {}
    with open(DAILY_FILE) as f:
        dp = json.load(f)
    for r in dp.get('all_results', []):
        name_map[r['code']] = r.get('name', '')

    # 3. 构建任务列表
    stocks = []
    for code in codes:
        pure = code[2:]  # 去掉sh/sz前缀
        name = name_map.get(pure, name_map.get(code, ''))
        stocks.append((pure, name))

    # 4. 多线程获取（curl子进程，每个线程独立）
    results = {}
    success = fail = 0
    start = time.time()

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(process_stock, code, name): code for code, name in stocks}
        for i, future in enumerate(as_completed(futures)):
            code, flow = future.result()
            if flow:
                results[code] = flow
                success += 1
            else:
                fail += 1
            if (i + 1) % 10 == 0:
                print(f"  进度: {i+1}/{len(stocks)} 成功{success} 失败{fail}")

    elapsed = time.time() - start
    print(f"\n扫描完成: 成功{success} 失败{fail} 耗时{elapsed:.1f}s")

    # 5. 加载现有capital_flow.json并更新
    with open(OUTPUT_FILE) as f:
        cf = json.load(f)

    existing_stocks = cf.get('stocks', {})
    updated = 0
    for code, data in results.items():
        existing_stocks[code] = data
        updated += 1

    cf['stocks'] = existing_stocks
    cf['update_time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cf['data_source'] = 'eastmoney_level2 + sina_kline'
    cf['total'] = len(existing_stocks)

    with open(OUTPUT_FILE, 'w') as f:
        json.dump(cf, f, ensure_ascii=False, separators=(',', ':'))

    print(f"输出: {OUTPUT_FILE} ({len(existing_stocks)}只, 更新{updated}只)")

    # 6. 显示前5只票的最新数据
    print("\n=== 前5只票最新数据 ===")
    for code, data in list(results.items())[:5]:
        daily = data.get('daily', [])
        last_date = daily[-1].get('date', '') if daily else ''
        last_flow = daily[-1].get('flow_wan', 0) if daily else 0
        print(f"  {code} {data['name']}: {last_date} 流入{last_flow:+.0f}万 5日{data['flow_5d_wan']:+.0f}万")


if __name__ == '__main__':
    main()
