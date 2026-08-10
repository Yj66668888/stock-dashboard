#!/usr/bin/env python3
"""
预计算主力资金净流入并输出为 JS 代码片段
从已有的 capital_flow.json 中提取每只股票最新一天的资金流数据，
直接嵌入 HTML，避免浏览器端 CORS/网络问题。

优于前端 fallback（显示"昨"）：注入后前端直接使用，标签显示实际日期。
"""
import json
import os
import re
import sys
import time


def extract_stock_codes(html_path):
    """从 HTML 中提取 STOCKS 的股票代码"""
    with open(html_path, 'r', encoding='utf-8') as f:
        html = f.read()

    all_codes = set()
    for pattern in [r'const STOCKS\s*=\s*(\[.*?\]);', r'const LAUNCH_STOCKS\s*=\s*(\[.*?\]);']:
        m = re.search(pattern, html, re.DOTALL)
        if m:
            try:
                stocks = json.loads(m.group(1))
                for s in stocks:
                    if 'code' in s:
                        all_codes.add(s['code'])
            except json.JSONDecodeError:
                codes = re.findall(r'"code"\s*:\s*"(\w+)"', m.group(1))
                all_codes.update(codes)

    return list(all_codes)


def load_capital_flow(cf_path):
    """加载 capital_flow.json"""
    if not os.path.exists(cf_path):
        return {}
    with open(cf_path, 'r', encoding='utf-8') as f:
        return json.load(f).get('stocks', {})


def get_latest_flow(code, cf_stocks):
    """从 capital_flow.json 获取单只股票最新一天的资金流"""
    # 尝试各种 code 格式
    sym = code.replace('sh', '').replace('sz', '')

    stock_data = cf_stocks.get(sym) or cf_stocks.get(code)
    if not stock_data:
        return None

    daily = stock_data.get('daily', [])
    if not daily:
        # 尝试 daily_10d
        daily_10d = stock_data.get('daily_10d', [])
        if daily_10d:
            daily = daily_10d

    if not daily:
        return None

    # 取最新一天
    latest = daily[-1]
    flow_wan = latest.get('flow_wan', 0)

    return {
        'date': latest.get('date', ''),
        'mainNetInflow': flow_wan,  # 万元，与 STOCKS.dailyFlow 单位一致
    }


def main():
    html_path = sys.argv[1] if len(sys.argv) > 1 else 'deploy/index.html'
    cf_path = sys.argv[2] if len(sys.argv) > 2 else 'capital_flow.json'

    codes = extract_stock_codes(html_path)
    cf_stocks = load_capital_flow(cf_path)

    if not codes:
        print("// 未找到股票代码", file=sys.stderr)
        print("const CAPITAL_FLOW_CACHE = {};")
        return

    unique_codes = list(set(codes))
    print(f"// 从 capital_flow.json 提取 {len(unique_codes)} 只股票的资金数据...", file=sys.stderr)

    results = {}
    success = 0
    fail = 0
    latest_date = ''

    for code in unique_codes:
        result = get_latest_flow(code, cf_stocks)
        if result:
            results[code] = result
            success += 1
            if result['date'] > latest_date:
                latest_date = result['date']
        else:
            fail += 1

    print(f"// 资金流预计算完成: 成功{success}, 失败{fail}, 最新数据日期={latest_date}", file=sys.stderr)

    now = time.strftime('%Y-%m-%d %H:%M:%S')
    js = f"// 主力资金预计算数据，生成时间: {now}\n"
    js += f"// 数据源: capital_flow.json (最新数据日: {latest_date})\n"
    js += f"// 字段: mainNetInflow=主力净流入(万元), date=数据日期\n"
    js += f"const CAPITAL_FLOW_CACHE = {json.dumps(results, ensure_ascii=False)};\n"

    print(js)


if __name__ == '__main__':
    main()
