#!/usr/bin/env python3
"""定向补齐6只暗盘哨兵票的 score/估值/资金流/reason（复用 enrich_missing_fields 的函数）"""
import json
import re
import sys
from pathlib import Path

ROOT = Path('/Users/fuckyouasshole/WorkBuddy/2026-07-01-11-18-34')
sys.path.insert(0, str(ROOT))
from enrich_missing_fields import (fetch_quote, fetch_all_flows, map_sector,
                                   load_qualified_sectors, build_reason)

TARGETS = ['sh603120', 'sz001221', 'sh600866', 'sh603256', 'sh603225', 'sz002558']


def main():
    html = (ROOT / 'deploy' / 'index.html').read_text(encoding='utf-8')
    m = re.search(r'(const STOCKS\s*=\s*)(\[.*?\])(\s*;\s*\n)', html, re.S)
    pool = json.loads(m.group(2))

    # 1) 评分：从 daily_predictions.json 取
    preds = json.load(open(ROOT / 'daily_predictions.json'))
    by_code = {s['code']: s for s in (preds.get('all_results') or [])}

    qualified = load_qualified_sectors()
    print(f'今日强势板块: {qualified or "无"}')

    # 2) 定向拉估值/资金流（只拉6只，避免限流）
    quotes = {c: fetch_quote(c) for c in TARGETS}
    flows = fetch_all_flows(TARGETS)

    for s in pool:
        if s['code'] not in TARGETS:
            continue
        p = s['code'][-6:]
        pr = by_code.get(p, {})
        if pr.get('total_score') is not None:
            s['score'] = pr['total_score']
            s['healthScore'] = pr['total_score']
        if pr.get('rsi') is not None:
            s['rsi'] = pr['rsi']
        if pr.get('drop_20d') is not None:
            s['drop_20d'] = pr['drop_20d']

        q = quotes[s['code']]
        f = flows.get(p)
        if q:
            industry = q['industry']
            sector = map_sector(industry)
            s['industry'] = industry
            s['sector'] = sector
            if q['pe']:
                s['pe'] = q['pe']
            if q['pb']:
                s['pb'] = q['pb']
            if q['roe'] is not None:
                s['roe'] = q['roe']
            if sector in qualified:
                s['sectorTier'], s['sectorResonance'] = 1, '板块启动(共振)'
            else:
                s['sectorTier'], s['sectorResonance'] = None, '独立走势'
        if f:
            s['dailyFlow'] = f['dailyFlow']
            s['flow5d'] = f['flow5d']
            s['flow10d'] = f['flow10d']
            s['flowDate'] = f['flowDate']
        s['reason'] = build_reason(s, q, f, s.get('sector', '综合'),
                                   s.get('sectorResonance', '独立走势'))
        print(f"{s['code']} {s['name']}: score={s.get('score')} sector={s.get('sector')} "
              f"pe={s.get('pe')} 当日流={s.get('dailyFlow')}万 5日={s.get('flow5d')}万")

    serialized = json.dumps(pool, ensure_ascii=False, separators=(',', ':'))
    new_html = html[:m.start(2)] + serialized + html[m.end(2):]
    (ROOT / 'deploy' / 'index.html').write_text(new_html, encoding='utf-8')
    print('✅ deploy/index.html 已写入')


if __name__ == '__main__':
    main()
