#!/usr/bin/env python3
"""cron 恢复验证 + 自动补救（2026-08-27 错峰修复后的观察哨）

检查今天(北京时间)的 GitHub Actions schedule 事件：
  - Cloud Full Scan 预期 8:33 触发
  - Cloud Dashboard Update 预期 9:07 起每半小时
若 full-scan 未触发 → 自动 dispatch 补救（cloud_pipeline.py full）
"""
import json
import re
import subprocess
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

REPO = 'yj66668888/stock-dashboard'
WF_FULLSCAN_ID = '331324542'   # cloud-full-scan.yml
WF_UPDATE_ID = '331324541'     # cloud-dashboard-update.yml
BJ = timezone(timedelta(hours=8))


def get_token():
    """从项目 .git/config 的 remote URL 提取内嵌 token"""
    out = subprocess.run(['git', 'config', '--get', 'remote.origin.url'],
                         capture_output=True, text=True).stdout.strip()
    m = re.search(r'https://[^:]+:([^@]+)@github\.com', out)
    return m.group(1) if m else None


def api(path, token, method='GET'):
    req = urllib.request.Request(
        f'https://api.github.com/repos/{REPO}/{path}',
        headers={'Authorization': f'token {token}', 'Accept': 'application/vnd.github+json'},
        method=method)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def main():
    token = get_token()
    if not token:
        print('❌ 无法从 .git/config 提取 token')
        sys.exit(1)

    now = datetime.now(BJ)
    today = now.strftime('%Y-%m-%d')
    print(f'检查时间(北京): {now.strftime("%Y-%m-%d %H:%M")}')
    print(f'观察点: full-scan 08:33 / update 09:07 起\n')

    runs = api(f'actions/runs?per_page=30', token).get('workflow_runs', [])
    sched_today = [r for r in runs
                   if r['event'] == 'schedule' and r['created_at'][:10] in
                   (today, (now - timedelta(hours=8)).strftime('%Y-%m-%d'))]  # UTC日期兜底

    fullscan = [r for r in sched_today if r['name'] == 'Cloud Full Scan']
    updates = [r for r in sched_today if r['name'] == 'Cloud Dashboard Update']
    midday = [r for r in sched_today if r['name'] == 'Cloud Midday Scan']

    for r in sched_today:
        bj_t = (datetime.fromisoformat(r['created_at'].replace('Z', '+00:00'))
                .astimezone(BJ).strftime('%H:%M'))
        print(f"  {bj_t} | {r['name']} | {r['conclusion']}")

    print(f'\nschedule事件统计: full-scan={len(fullscan)} update={len(updates)} midday={len(midday)}')

    if fullscan:
        print('\n✅ CRON 已恢复：full-scan 今日已由 schedule 触发')
        sys.exit(0)

    # full-scan 未触发 → 自动 dispatch 补救
    print('\n⚠️ full-scan 今日无 schedule 触发 → 自动 dispatch 补救...')
    req = urllib.request.Request(
        f'https://api.github.com/repos/{REPO}/actions/workflows/{WF_FULLSCAN_ID}/dispatches',
        data=json.dumps({'ref': 'main'}).encode(),
        headers={'Authorization': f'token {token}',
                 'Accept': 'application/vnd.github+json'},
        method='POST')
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            print(f'✅ 已 dispatch Cloud Full Scan (HTTP {r.status})，约 5-10 分钟跑完')
    except Exception as e:
        print(f'❌ dispatch 失败: {e}')
        print('请手动处理：gh workflow run cloud-full-scan.yml 或网页触发')
        sys.exit(1)


if __name__ == '__main__':
    main()
