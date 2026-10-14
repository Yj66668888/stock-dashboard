#!/usr/bin/env python3
"""一次性：把6只暗盘哨兵票注入三份 HTML 的 STOCKS（字段由后续 enrich/precompute 补齐）"""
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path

ROOT = Path('/Users/fuckyouasshole/WorkBuddy/2026-07-01-11-18-34')

NEW_STOCKS = [
    ('sh603120', '肯特催化'),
    ('sz001221', '悍高集团'),
    ('sh600866', '星湖科技'),
    ('sh603256', '宏和科技'),
    ('sh603225', '新凤鸣'),
    ('sz002558', '巨人网络'),
]


def live_quotes():
    url = 'https://qt.gtimg.cn/q=' + ','.join(c for c, _ in NEW_STOCKS)
    r = subprocess.run(['env', '-u', 'http_proxy', '-u', 'https_proxy',
                        '-u', 'HTTP_PROXY', '-u', 'HTTPS_PROXY',
                        'curl', '-s', '--noproxy', '*', url],
                       capture_output=True)
    out = r.stdout.decode('gbk', errors='replace')
    q = {}
    for line in out.split(';'):
        if '=' not in line:
            continue
        parts = line.split('~')
        if len(parts) < 50:
            continue
        full = 'sh' + parts[2] if parts[2].startswith('6') else 'sz' + parts[2]
        q[full] = {
            'close': float(parts[3]),
            'dayChg': float(parts[32]),
            'turnover': float(parts[38]),
            'volRatio': float(parts[49]),
        }
    return q


def main():
    q = live_quotes()
    today = datetime.now().strftime('%Y-%m-%d')
    entries = []
    for code, name in NEW_STOCKS:
        if code not in q:
            print(f'⚠️ {code} {name} 拿不到实时价，跳过！')
            continue
        e = {'code': code, 'name': name, **q[code], 'flowDate': today,
             'note': '暗盘哨兵(7-9月机构大宗净买入高频)'}
        entries.append(e)
    print(f'待注入 {len(entries)} 只')

    for fn in ['deploy/index.html', 'docs/index.html', 'index.html']:
        p = ROOT / fn
        html = p.read_text(encoding='utf-8')
        m = re.search(r'(const\s+STOCKS\s*=\s*)(\[.*?\])(\s*;\s*\n)', html, re.S)
        if not m:
            print(f'⚠️ {fn}: 未匹配 STOCKS')
            continue
        pool = json.loads(m.group(2))
        existing = {s['code'] for s in pool}
        added = [e for e in entries if e['code'] not in existing]
        if not added:
            print(f'⏭ {fn}: 全部已存在')
            continue
        pool.extend(added)
        serialized = json.dumps(pool, ensure_ascii=False, separators=(',', ':'))
        new_html = html[:m.start(2)] + serialized + html[m.end(2):]
        p.write_text(new_html, encoding='utf-8')
        print(f'✅ {fn}: +{len(added)} 只 → 共 {len(pool)} 只')


if __name__ == '__main__':
    main()
