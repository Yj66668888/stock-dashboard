#!/usr/bin/env python3
"""
板块启动共振分析
从STOCKS数组提取板块映射，结合daily_predictions评分，计算每只票的板块共振情况
输出：sector_resonance.json
"""
import json, os, re
from datetime import datetime

INDEX_HTML = os.path.join(os.path.dirname(__file__), 'deploy', 'index.html')
DAILY_FILE = os.path.join(os.path.dirname(__file__), 'daily_predictions.json')
OUTPUT_FILE = os.path.join(os.path.dirname(__file__), 'sector_resonance.json')

# 启动段评分阈值（总评分>40视为启动段）
LAUNCH_THRESHOLD = 40

def load_stocks_from_html():
    """从index.html提取STOCKS数组"""
    with open(INDEX_HTML, 'r', encoding='utf-8') as f:
        html = f.read()
    m = re.search(r'const STOCKS\s*=\s*(\[.*?\]);', html, re.DOTALL)
    if not m:
        raise ValueError("未找到STOCKS数组")
    return json.loads(m.group(1))

def main():
    print(f"=== 板块启动共振分析 {datetime.now().strftime('%Y-%m-%d %H:%M')} ===")

    # 1. 加载STOCKS数组（板块映射）
    stocks = load_stocks_from_html()
    print(f"STOCKS数组: {len(stocks)}只票")

    # 2. 加载全市场评分
    with open(DAILY_FILE, 'r') as f:
        daily = json.load(f)
    all_results = {r['code']: r for r in daily.get('all_results', [])}
    print(f"全市场评分: {len(all_results)}只")

    # 3. 按板块分组
    sectors = {}
    for s in stocks:
        code = s['code'].replace('sh', '').replace('sz', '')
        sector = s.get('sector', '其他')
        sectors.setdefault(sector, []).append({
            'code': code,
            'name': s['name'],
            'full_code': s['code'],
            'sector': sector,
            'direction': s.get('direction', ''),
            'reason': s.get('reason', '')
        })

    print(f"板块数: {len(sectors)}")

    # 4. 计算每个板块的启动情况
    sector_stats = {}
    for sector, members in sectors.items():
        total = len(members)
        launch_count = 0
        scores = []
        trends = []
        launch_stocks = []

        for m in members:
            r = all_results.get(m['code'], {})
            score = r.get('total_score', 0)
            trend = r.get('metrics', {}).get('trend', 0)
            scores.append(score)
            trends.append(trend)

            if score >= LAUNCH_THRESHOLD:
                launch_count += 1
                launch_stocks.append({
                    'code': m['code'],
                    'name': m['name'],
                    'score': score,
                    'trend': trend
                })

        avg_score = sum(scores) / total if total > 0 else 0
        avg_trend = sum(trends) / total if total > 0 else 0
        launch_ratio = launch_count / total if total > 0 else 0

        # 板块状态判断
        if launch_ratio >= 0.5 and avg_trend > 0:
            status = '板块启动'  # 过半数票启动且趋势向上
        elif launch_ratio >= 0.3:
            status = '部分启动'
        elif launch_ratio > 0:
            status = '个别启动'
        else:
            status = '板块沉寂'

        sector_stats[sector] = {
            'total': total,
            'launch_count': launch_count,
            'launch_ratio': round(launch_ratio, 2),
            'avg_score': round(avg_score, 1),
            'avg_trend': round(avg_trend, 2),
            'status': status,
            'launch_stocks': launch_stocks
        }

    # 5. 为每只票计算板块共振
    stock_resonance = {}
    for s in stocks:
        code = s['code'].replace('sh', '').replace('sz', '')
        sector = s.get('sector', '其他')
        ss = sector_stats.get(sector, {})

        r = all_results.get(code, {})
        score = r.get('total_score', 0)

        stock_resonance[code] = {
            'name': s['name'],
            'sector': sector,
            'sector_total': ss.get('total', 0),
            'sector_launch_count': ss.get('launch_count', 0),
            'sector_launch_ratio': ss.get('launch_ratio', 0),
            'sector_status': ss.get('status', '未知'),
            'sector_avg_score': ss.get('avg_score', 0),
            'sector_avg_trend': ss.get('avg_trend', 0),
            'self_score': score,
            'self_is_launch': score >= LAUNCH_THRESHOLD,
            'sector_launch_stocks': [ls['name'] for ls in ss.get('launch_stocks', [])]
        }

    # 6. 输出
    output = {
        'update_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'threshold': LAUNCH_THRESHOLD,
        'sectors': sector_stats,
        'stocks': stock_resonance
    }

    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, separators=(',', ':'))

    print(f"\n输出: {OUTPUT_FILE} ({os.path.getsize(OUTPUT_FILE)/1024:.0f}KB)")

    # 打印板块状态
    print(f"\n=== 板块启动状态 ===")
    for sector, ss in sorted(sector_stats.items(), key=lambda x: -x[1]['launch_ratio']):
        launch_names = [ls['name'] for ls in ss['launch_stocks']]
        print(f"  [{ss['status']}] {sector}: {ss['launch_count']}/{ss['total']}启动 均分{ss['avg_score']} 趋势{ss['avg_trend']:+.1f}% {launch_names}")

    # 打印4只启动段票的共振
    print(f"\n=== 4只启动段票板块共振 ===")
    for code in ['601899', '600519', '600406', '002557']:
        sr = stock_resonance.get(code, {})
        print(f"  {code} {sr.get('name','')}: 板块[{sr.get('sector','')}] {sr.get('sector_launch_count',0)}/{sr.get('sector_total',0)}启动 状态={sr.get('sector_status','')}")

if __name__ == '__main__':
    main()
