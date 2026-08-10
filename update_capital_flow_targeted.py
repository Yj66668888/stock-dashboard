#!/usr/bin/env python3
"""
定向更新 capital_flow.json — 仅拉取仪表盘股票（~30只），不跑全量。
🔥 v2.0 数据源切换：东方财富Level2主力资金API（真实大单数据）
+ 新浪K线API（辅助OHLCV数据，维持字段兼容）

输出格式保持不变：flow_5d_wan / flow_10d_wan / daily[].flow_wan
"""
import urllib.request, json, time, os, sys, re
from datetime import datetime

CF_PATH = os.path.join(os.path.dirname(__file__), 'capital_flow.json')
HTML_PATH = os.path.join(os.path.dirname(__file__), 'deploy', 'index.html')

EM_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
    'Referer': 'https://data.eastmoney.com/'
}
SINA_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
    'Referer': 'https://finance.sina.com.cn/'
}

def extract_dashboard_codes(html_path):
    """从仪表盘 HTML 中提取 STOCKS + LAUNCH_STOCKS 的股票代码"""
    with open(html_path, 'r', encoding='utf-8') as f:
        html = f.read()
    
    codes = set()
    for pattern in [r'const STOCKS\s*=\s*(\[.*?\]);', r'const LAUNCH_STOCKS\s*=\s*(\[.*?\]);']:
        m = re.search(pattern, html, re.DOTALL)
        if m:
            try:
                stocks = json.loads(m.group(1))
                for s in stocks:
                    if 'code' in s:
                        raw = s['code']
                        if len(raw) > 6:
                            raw = raw.replace('sh', '').replace('sz', '')
                        codes.add(raw)
            except json.JSONDecodeError:
                found = re.findall(r'"code"\s*:\s*"(\w+)"', m.group(1))
                for c in found:
                    if len(c) > 6:
                        c = c.replace('sh', '').replace('sz', '')
                    codes.add(c)
    return sorted(codes)

def get_sina_kline(code, datalen=12):
    """新浪日K线 — 提供 OHLCV 数据"""
    sina_code = ('sh' if code.startswith('6') else 'sz') + code
    url = f"http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol={sina_code}&scale=240&ma=no&datalen={datalen}"
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=SINA_HEADERS)
            with urllib.request.urlopen(req, timeout=10) as r:
                return json.loads(r.read().decode())
        except Exception:
            if attempt < 2:
                time.sleep(0.5)
    return None

def get_eastmoney_fflow(code, datalen=12):
    """东方财富主力资金流向 — 真实Level2大单数据
    返回格式: [{'date': '2026-07-28', 'main_net': 1234567.0, 'chg_pct': 2.5}, ...]
    main_net 单位为元，需要转换为万元
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
                    'date': parts[0],                          # 日期 YYYY-MM-DD
                    'main_net': float(parts[1]) if len(parts) > 1 else 0,  # 主力净流入(元)
                    'chg_pct': float(parts[6]) if len(parts) > 6 else 0    # 涨跌幅%
                })
            return results
        except Exception:
            if attempt < 2:
                time.sleep(0.5)
    return None

def merge_data(em_data, kls, name, code):
    """
    合并东方财富真实主力资金 + 新浪 OHLCV 数据
    输出与旧版完全兼容的格式
    """
    if not em_data or not kls or len(kls) < 2:
        return None
    
    # 构建日期→新浪数据的索引
    sina_map = {}
    for k in kls:
        sina_map[k['day']] = k
    
    # 取5日和10日滑动窗口
    em_5d = em_data[-5:] if len(em_data) >= 5 else em_data
    em_10d = em_data[-10:] if len(em_data) >= 10 else em_data
    
    # ---- 构建 daily 数组（5日） ----
    daily_flows = []
    up_days = down_days = 0
    total_amount = total_flow_5d = 0
    max_upper_shadow = 0
    
    # 5日均量（不含最新日，用于量比）
    vols = []
    for item in em_5d:
        d = item['date']
        sk = sina_map.get(d)
        if sk:
            vols.append(float(sk['volume']))
    avg_vol_5 = sum(vols[:-1]) / max(len(vols) - 1, 1) if len(vols) > 1 else (vols[0] if vols else 1)
    
    for item in em_5d:
        d = item['date']
        main_net_wan = round(item['main_net'] / 10000, 0)  # 元→万元
        sk = sina_map.get(d)
        
        chg_pct = item['chg_pct']
        amount_yi = 0
        upper_shadow = 0
        
        if sk:
            o, h, l, c, v = float(sk['open']), float(sk['high']), float(sk['low']), float(sk['close']), float(sk['volume'])
            amount_yi = round(v * c / 1e8, 2)
            upper_shadow = round((h - max(o, c)) / o * 100, 2)
            chg_pct = round((c - o) / o * 100, 2)  # 用新浪的实际涨跌幅
        else:
            # 新浪没有这一天（比如最新交易日可能缺失），用东方财富的数据
            pass
        
        if chg_pct > 0:
            up_days += 1
        elif chg_pct < 0:
            down_days += 1
        
        total_amount += amount_yi * 1e8
        total_flow_5d += main_net_wan
        
        if upper_shadow > max_upper_shadow:
            max_upper_shadow = upper_shadow
        
        daily_flows.append({
            'date': d,
            'chg': round(chg_pct, 2),
            'amount_yi': amount_yi,
            'flow_wan': main_net_wan,  # 🔥 真实主力资金（万元）
            'upper_shadow': upper_shadow
        })
    
    # ---- 构建 daily_10d 数组 ----
    daily_10d_list = []
    total_flow_10d = 0
    
    for item in em_10d:
        d = item['date']
        main_net_wan = round(item['main_net'] / 10000, 0)
        sk = sina_map.get(d)
        
        amount_yi = 0
        upper_shadow = 0
        chg_pct = item['chg_pct']
        
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
            'flow_wan': main_net_wan,  # 🔥 真实主力资金（万元）
            'upper_shadow': upper_shadow
        })
    
    # 最新日数据
    latest_sina = kls[-1]
    latest_close = float(latest_sina['close'])
    latest_volume = float(latest_sina['volume'])
    latest_open = float(latest_sina['open'])
    latest_chg = (latest_close - latest_open) / latest_open * 100
    vol_ratio = latest_volume / avg_vol_5 if avg_vol_5 > 0 else 1.0
    
    return {
        'name': name,
        'code': code,
        'flow_5d_wan': round(total_flow_5d, 0),     # 🔥 真实5日主力净流入(万元)
        'flow_10d_wan': round(total_flow_10d, 0),    # 🔥 真实10日主力净流入(万元)
        'up_days_5d': up_days,
        'down_days_5d': down_days,
        'avg_amount_yi': round(total_amount / max(len(em_5d), 1) / 1e8, 2),
        'latest_chg': round(latest_chg, 2),
        'latest_amount_yi': round(latest_volume * latest_close / 1e8, 2),
        'vol_ratio': round(vol_ratio, 2),
        'max_upper_shadow': round(max_upper_shadow, 2),
        'daily': daily_flows,
        'daily_10d': daily_10d_list
    }

def main():
    print(f"=== 🔥 定向资金流更新 v2.0 (东方财富真实主力资金) {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")
    
    codes = extract_dashboard_codes(HTML_PATH)
    print(f"仪表盘股票: {len(codes)}只")
    print(f"代码: {codes}")
    
    # 加载现有 capital_flow.json（获取 name 映射和保留非仪表盘股票数据）
    cf_stocks = {}
    if os.path.exists(CF_PATH):
        with open(CF_PATH, 'r', encoding='utf-8') as f:
            cf_data = json.load(f)
        cf_stocks = cf_data.get('stocks', {})
    
    code_name_map = {}
    for code in codes:
        if code in cf_stocks:
            code_name_map[code] = cf_stocks[code].get('name', code)
        else:
            code_name_map[code] = code
    
    success = fail = 0
    for code in codes:
        name = code_name_map[code]
        print(f"  拉取 {code} {name}...", end=' ', flush=True)
        
        # 并行拉取两个数据源
        em_data = get_eastmoney_fflow(code, 12)
        kls = get_sina_kline(code, 12)
        
        if not em_data:
            print("❌ 东方财富数据获取失败")
            fail += 1
            continue
        if not kls:
            print("❌ 新浪K线获取失败")
            fail += 1
            continue
        
        flow = merge_data(em_data, kls, name, code)
        if not flow:
            print("❌ 合并失败")
            fail += 1
            continue
        
        cf_stocks[code] = flow
        latest_date = flow['daily'][-1]['date']
        flow_5d = flow['flow_5d_wan']
        flow_str = f"{flow_5d/10000:+.2f}亿" if abs(flow_5d) >= 10000 else f"{flow_5d:+.0f}万"
        print(f"✅ (最新:{latest_date}, 5日主力:{flow_str})")
        success += 1
        time.sleep(0.3)  # 礼貌间隔
    
    # 保存
    output = {
        'update_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'total': len(cf_stocks),
        'data_source': 'eastmoney_level2',
        'stocks': cf_stocks
    }
    
    with open(CF_PATH, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, separators=(',', ':'))
    
    size_kb = os.path.getsize(CF_PATH) / 1024
    print(f"\n✅ 定向更新完成: 成功{success}, 失败{fail}")
    print(f"   数据源: 东方财富 Level2 主力资金")
    print(f"   输出: {CF_PATH} ({size_kb:.0f}KB)")

if __name__ == '__main__':
    main()
