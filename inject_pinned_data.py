#!/usr/bin/env python3
"""
注入预计算数据到 deploy_davis/index.html:
1. KDJ_FALLBACK 补充4只固定票的技术指标
2. CAPITAL_FLOW_CACHE 替换为34只票的当日实时主力资金流
3. STOCKS 数组中固定票的 flow5d/flow10d/drop20d 等字段更新
"""
import json
import re
from datetime import datetime

# === 4只固定票完整技术指标 ===
PINNED_FB = {
    "sh603067": {
        "kd5_k": 57.33, "kd5_d": 43.04,
        "kd30_k": 49.26, "kd30_d": 44.86, "kd30_cross": "上升",
        "kd60_k": 49.25, "kd60_d": 49.28, "kd60_cross": "下降",
        "rsi": 55.88,
        "macd": "DIF:-1.65 DEA:-2.08 金叉",
        "ma": "交叉震荡", "ma5": 32.78, "ma10": 31.56, "ma20": 32.64, "maAbove": True,
        "drop20d": -9.12, "volRatio": 0.8,
        "supportPrice": 28.31, "supportDist": 14.2,
        "openRate30d": 43.3, "openRateTotal": 43.3, "openRateScore": 2,
    },
    "sh600105": {
        "kd5_k": 54.99, "kd5_d": 36.89,
        "kd30_k": 26.98, "kd30_d": 43.66, "kd30_cross": "下降",
        "kd60_k": 57.07, "kd60_d": 74.86, "kd60_cross": "下降",
        "rsi": 55.88,
        "macd": "DIF:-3.85 DEA:-4.87 金叉",
        "ma": "交叉震荡", "ma5": 35.88, "ma10": 34.2, "ma20": 37.3, "maAbove": True,
        "drop20d": -13.22, "volRatio": 0.88,
        "supportPrice": 29.11, "supportDist": 25.4,
        "openRate30d": 46.7, "openRateTotal": 46.7, "openRateScore": 2,
    },
    "sh601126": {
        "kd5_k": 53.41, "kd5_d": 37.98,
        "kd30_k": 30.93, "kd30_d": 48.92, "kd30_cross": "下降",
        "kd60_k": 58.75, "kd60_d": 73.29, "kd60_cross": "下降",
        "rsi": 51.42,
        "macd": "DIF:-3.60 DEA:-5.01 金叉",
        "ma": "交叉震荡", "ma5": 44.59, "ma10": 41.68, "ma20": 43.49, "maAbove": True,
        "drop20d": -13.31, "volRatio": 0.89,
        "supportPrice": 36.5, "supportDist": 20.0,
        "openRate30d": 43.3, "openRateTotal": 43.3, "openRateScore": 2,
    },
    "sz000688": {
        "kd5_k": 48.72, "kd5_d": 48.05,
        "kd30_k": 76.3, "kd30_d": 78.21, "kd30_cross": "下降",
        "kd60_k": 83.61, "kd60_d": 84.28, "kd60_cross": "下降",
        "rsi": 72.91,
        "macd": "DIF:-0.74 DEA:-1.61 金叉",
        "ma": "多头排列", "ma5": 30.71, "ma10": 29.41, "ma20": 28.6, "maAbove": True,
        "drop20d": 7.09, "volRatio": 0.66,
        "supportPrice": 23.88, "supportDist": 25.7,
        "openRate30d": 43.3, "openRateTotal": 43.3, "openRateScore": 2,
        "mainFlow": "5日+3797万", "mainFlowDate": "5日累计",
    },
}

# === 34只票当日主力资金流 (2026-08-10 实时) ===
CAPITAL_FLOW = {
    "sh600418": {"date": "2026-08-10", "mainNetInflow": -140065675.0, "mainFlowPct": -12.54},
    "sh600686": {"date": "2026-08-10", "mainNetInflow": 13160661.0, "mainFlowPct": 6.44},
    "sh600702": {"date": "2026-08-10", "mainNetInflow": -4724108.0, "mainFlowPct": -1.09},
    "sh601058": {"date": "2026-08-10", "mainNetInflow": -15256484.0, "mainFlowPct": -6.48},
    "sh603156": {"date": "2026-08-10", "mainNetInflow": -13600272.0, "mainFlowPct": -3.37},
    "sh603345": {"date": "2026-08-10", "mainNetInflow": 1053340.0, "mainFlowPct": 0.3},
    "sh603317": {"date": "2026-08-10", "mainNetInflow": 10401376.0, "mainFlowPct": 5.42},
    "sh600741": {"date": "2026-08-10", "mainNetInflow": 12911470.0, "mainFlowPct": 12.16},
    "sh601163": {"date": "2026-08-10", "mainNetInflow": 283025.0, "mainFlowPct": 0.8},
    "sh601966": {"date": "2026-08-10", "mainNetInflow": 8195092.0, "mainFlowPct": 12.25},
    "sh603057": {"date": "2026-08-10", "mainNetInflow": 2594185.0, "mainFlowPct": 5.56},
    "sh600737": {"date": "2026-08-10", "mainNetInflow": 45848729.0, "mainFlowPct": 5.99},
    "sh600335": {"date": "2026-08-10", "mainNetInflow": 1230034.0, "mainFlowPct": 4.01},
    "sh600166": {"date": "2026-08-10", "mainNetInflow": 5294801.0, "mainFlowPct": 3.1},
    "sh601633": {"date": "2026-08-10", "mainNetInflow": -907833.0, "mainFlowPct": -0.63},
    "sh603039": {"date": "2026-08-10", "mainNetInflow": -48762296.0, "mainFlowPct": -3.25},
    "sh600475": {"date": "2026-08-10", "mainNetInflow": -6352252.0, "mainFlowPct": -9.3},
    "sh601872": {"date": "2026-08-10", "mainNetInflow": -72135260.0, "mainFlowPct": -9.52},
    "sh600188": {"date": "2026-08-10", "mainNetInflow": 14676677.0, "mainFlowPct": 1.88},
    "sh603619": {"date": "2026-08-10", "mainNetInflow": -9194359.0, "mainFlowPct": -2.15},
    "sh600436": {"date": "2026-08-10", "mainNetInflow": 43929490.0, "mainFlowPct": 5.87},
    "sh600835": {"date": "2026-08-10", "mainNetInflow": -3334425.0, "mainFlowPct": -6.45},
    "sh601127": {"date": "2026-08-10", "mainNetInflow": -66866403.0, "mainFlowPct": -8.23},
    "sh600895": {"date": "2026-08-10", "mainNetInflow": 42196067.0, "mainFlowPct": 6.35},
    "sh601933": {"date": "2026-08-10", "mainNetInflow": -11549626.0, "mainFlowPct": -4.82},
    "sh601606": {"date": "2026-08-10", "mainNetInflow": 306011399.0, "mainFlowPct": 23.54},
    "sh603171": {"date": "2026-08-10", "mainNetInflow": -5793715.0, "mainFlowPct": -1.94},
    "sh600986": {"date": "2026-08-10", "mainNetInflow": -7238059.0, "mainFlowPct": -2.16},
    "sh600426": {"date": "2026-08-10", "mainNetInflow": -23276027.0, "mainFlowPct": -2.37},
    "sh601799": {"date": "2026-08-10", "mainNetInflow": 9016481.0, "mainFlowPct": 4.44},
    "sh603067": {"date": "2026-08-10", "mainNetInflow": 61980817.0, "mainFlowPct": 13.21},
    "sh600105": {"date": "2026-08-10", "mainNetInflow": -570320496.0, "mainFlowPct": -14.89},
    "sh601126": {"date": "2026-08-10", "mainNetInflow": 73966921.0, "mainFlowPct": 5.98},
    "sz000688": {"date": "2026-08-10", "mainNetInflow": 43102498.0, "mainFlowPct": 9.56},
}

def format_flow(val):
    if val is None or val != val: return '--'
    a = abs(val); s = '+' if val >= 0 else '-'
    if a >= 1e8: return f'{s}{a/1e8:.2f}亿'
    if a >= 1e4: return f'{s}{a/1e4:.0f}万'
    return f'{s}{a:.0f}'

def main():
    html_path = 'deploy_davis/index.html'

    with open(html_path, 'r', encoding='utf-8') as f:
        html = f.read()

    print(f'原始HTML: {len(html)} 字符')

    # === 1. 注入KDJ_FALLBACK补充 ===
    # 在 KDJ_FALLBACK 定义后添加 Object.assign
    fb_js = json.dumps(PINNED_FB, ensure_ascii=False)
    fb_inject = f'\nObject.assign(KDJ_FALLBACK, {fb_js}); // PINNED_STOCKS补充'

    # 找到 KDJ_FALLBACK 定义行
    fb_pattern = r'(var KDJ_FALLBACK\s*=\s*\{[^}]*\}\s*;)'
    m = re.search(fb_pattern, html)
    if m:
        # 检查是否已有注入标记
        if 'PINNED_STOCKS补充' not in html[m.end():m.end()+200]:
            html = html[:m.end()] + fb_inject + html[m.end():]
            print(f'[OK] KDJ_FALLBACK 补充注入成功 (4只固定票)')
        else:
            print(f'[SKIP] KDJ_FALLBACK 已有补充注入')
    else:
        print(f'[WARN] KDJ_FALLBACK 未找到')

    # === 2. 替换 CAPITAL_FLOW_CACHE ===
    # 构建新的 CAPITAL_FLOW_CACHE，包含所有34只票的当日资金流
    cf_cache = {}
    for code, data in CAPITAL_FLOW.items():
        main_net = data['mainNetInflow']
        entry = {
            "date": data['date'],
            "mainNetInflow": main_net,
            "mainFlowStr": format_flow(main_net),
        }
        if 'mainFlowPct' in data:
            entry['mainFlowPct'] = data['mainFlowPct']
        cf_cache[code] = entry

    cf_js = json.dumps(cf_cache, ensure_ascii=False)
    cf_pattern = r'const CAPITAL_FLOW_CACHE\s*=\s*\{[^}]*\}\s*;'
    new_cf = f'const CAPITAL_FLOW_CACHE = {cf_js};'
    if re.search(cf_pattern, html):
        html = re.sub(cf_pattern, new_cf, html)
        print(f'[OK] CAPITAL_FLOW_CACHE 替换成功 ({len(cf_cache)}只票)')
    else:
        print(f'[WARN] CAPITAL_FLOW_CACHE 未找到')

    # === 3. 更新 STOCKS 数组中固定票的字段 ===
    # 找到 STOCKS 数组并解析
    stocks_pattern = r'(const\s+STOCKS\s*=\s*)(\[.*?\])\s*;'
    m = re.search(stocks_pattern, html, re.S)
    if m:
        stocks_str = m.group(2)
        stocks = json.loads(stocks_str)

        updated = 0
        for s in stocks:
            if not s.get('isPinned'):
                continue
            code = s['code']
            fb = PINNED_FB.get(code, {})
            cf = CAPITAL_FLOW.get(code, {})

            # 更新技术指标字段
            if 'rsi' in fb:
                s['rsi'] = fb['rsi']
            if 'drop20d' in fb:
                s['drop20d'] = fb['drop20d']
            if 'macd' in fb:
                s['macd'] = fb['macd']
            if 'ma' in fb:
                s['ma'] = fb['ma']
            if 'ma20' in fb:
                s['ma20'] = fb['ma20']
            if 'maAbove' in fb:
                s['maAbove'] = fb['maAbove']
            if 'volRatio' in fb:
                s['volRatio'] = fb['volRatio']
            if 'supportPrice' in fb:
                s['supportPrice'] = fb['supportPrice']
            if 'supportDist' in fb:
                s['supportDist'] = fb['supportDist']
            if 'openRate30d' in fb:
                s['openRate30d'] = fb['openRate30d']
            if 'openRateTotal' in fb:
                s['openRateTotal'] = fb['openRateTotal']
            if 'openRateScore' in fb:
                s['openRateScore'] = fb['openRateScore']

            # KDJ字段
            if 'kd5_k' in fb:
                s['kd5_k'] = fb['kd5_k']
            if 'kd5_d' in fb:
                s['kd5_d'] = fb['kd5_d']
            if 'kd30_k' in fb:
                s['kd30_k'] = fb['kd30_k']
            if 'kd30_d' in fb:
                s['kd30_d'] = fb['kd30_d']
            if 'kd30_cross' in fb:
                s['kd30_cross'] = fb['kd30_cross']
            if 'kd60_k' in fb:
                s['kd60_k'] = fb['kd60_k']
            if 'kd60_d' in fb:
                s['kd60_d'] = fb['kd60_d']
            if 'kd60_cross' in fb:
                s['kd60_cross'] = fb['kd60_cross']

            # 当日主力资金流
            if cf:
                main_net = cf['mainNetInflow']
                s['mainFlow'] = format_flow(main_net)
                s['mainFlowToday'] = main_net
                s['mainFlowDate'] = cf['date']
                s['mainFlowPct'] = cf.get('mainFlowPct', 0)
                s['dailyFlow'] = main_net / 1e4  # 万元

            # 5日资金流（如果有）
            if 'mainFlow' in fb:
                s['mainFlow'] = fb['mainFlow']
                s['mainFlowDate'] = fb.get('mainFlowDate', '')

            updated += 1
            print(f'  [OK] {code} {s.get("name","")}: rsi={s.get("rsi","?")}, drop20d={s.get("drop20d","?")}, mainFlow={s.get("mainFlow","?")}')

        # 替换回HTML
        new_stocks_str = json.dumps(stocks, ensure_ascii=False)
        html = html[:m.start()] + m.group(1) + new_stocks_str + ';' + html[m.end():]
        print(f'[OK] STOCKS 数组更新完成 ({updated}只固定票)')

    # === 4. 同时更新 deploy/index.html ===
    deploy_path = 'deploy/index.html'
    try:
        with open(deploy_path, 'r', encoding='utf-8') as f:
            deploy_html = f.read()

        # 同样注入KDJ_FALLBACK
        if 'PINNED_STOCKS补充' not in deploy_html:
            m2 = re.search(r'(var KDJ_FALLBACK\s*=\s*\{[^}]*\}\s*;)', deploy_html)
            if m2:
                deploy_html = deploy_html[:m2.end()] + fb_inject + deploy_html[m2.end():]

        # 同样替换 CAPITAL_FLOW_CACHE
        if re.search(cf_pattern, deploy_html):
            deploy_html = re.sub(cf_pattern, new_cf, deploy_html)

        # 更新 STOCKS
        m3 = re.search(stocks_pattern, deploy_html, re.S)
        if m3:
            deploy_stocks = json.loads(m3.group(2))
            for s in deploy_stocks:
                if not s.get('isPinned'):
                    continue
                code = s['code']
                fb = PINNED_FB.get(code, {})
                cf = CAPITAL_FLOW.get(code, {})
                for k in ['rsi','drop20d','macd','ma','ma20','maAbove','volRatio',
                           'supportPrice','supportDist','openRate30d','openRateTotal','openRateScore',
                           'kd5_k','kd5_d','kd30_k','kd30_d','kd30_cross','kd60_k','kd60_d','kd60_cross']:
                    if k in fb:
                        s[k] = fb[k]
                if cf:
                    main_net = cf['mainNetInflow']
                    s['mainFlow'] = format_flow(main_net)
                    s['mainFlowToday'] = main_net
                    s['mainFlowDate'] = cf['date']
                    s['mainFlowPct'] = cf.get('mainFlowPct', 0)
                    s['dailyFlow'] = main_net / 1e4
                if 'mainFlow' in fb:
                    s['mainFlow'] = fb['mainFlow']

            deploy_html = deploy_html[:m3.start()] + m3.group(1) + json.dumps(deploy_stocks, ensure_ascii=False) + ';' + deploy_html[m3.end():]

        with open(deploy_path, 'w', encoding='utf-8') as f:
            f.write(deploy_html)
        print(f'[OK] deploy/index.html 也已更新')
    except Exception as e:
        print(f'[WARN] deploy/index.html 更新失败: {e}')

    # === 5. 写回 deploy_davis/index.html ===
    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f'\n[OK] {html_path} 写入完成 ({len(html)} 字符)')

    # 验证
    with open(html_path, 'r', encoding='utf-8') as f:
        verify = f.read()
    print(f'\n=== 验证 ===')
    print(f'KDJ_FALLBACK补充标记: {"PINNED_STOCKS补充" in verify}')
    print(f'CAPITAL_FLOW_CACHE票数: {len(re.findall(r"sh\d{6}|sz\d{6}", re.search(r"const CAPITAL_FLOW_CACHE = ({.*?});", verify).group(1)))}')
    # 检查固定票数据
    m4 = re.search(stocks_pattern, verify, re.S)
    if m4:
        vs = json.loads(m4.group(2))
        for s in vs:
            if s.get('isPinned'):
                print(f'  {s["code"]} {s.get("name","")}: rsi={s.get("rsi","MISSING")}, drop20d={s.get("drop20d","MISSING")}, mainFlow={s.get("mainFlow","MISSING")}, kd5_k={s.get("kd5_k","MISSING")}')

if __name__ == '__main__':
    main()
