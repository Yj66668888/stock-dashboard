#!/usr/bin/env python3
"""
拉取 2026-07-01 ~ 2026-09-30 全市场大宗交易（翻页版，落盘 block_trades_jul_sep.json）
用途：① 找机构净买入高频大中票（入池候选）② 明盘+暗盘双正回测
"""
import json
import subprocess
import time
from pathlib import Path

ROOT = Path('/Users/fuckyouasshole/WorkBuddy/2026-07-01-11-18-34')
OUT = ROOT / 'block_trades_jul_sep.json'


def curl(url, retry=3, timeout=20):
    for i in range(retry):
        r = subprocess.run(
            ['env', '-u', 'http_proxy', '-u', 'https_proxy',
             '-u', 'HTTP_PROXY', '-u', 'HTTPS_PROXY',
             'curl', '-s', '--noproxy', '*',
             '--connect-timeout', str(timeout), '--max-time', str(timeout + 5),
             '-H', 'User-Agent: Mozilla/5.0', url],
            capture_output=True, text=True)
        out = r.stdout.strip()
        if out and len(out) > 10:
            return out
        time.sleep(1.5)
    return ''


def pull_range(start, end):
    """翻页拉一个日期区间，返回全部 rows"""
    all_rows = []
    page = 1
    while True:
        url = (
            'https://datacenter-web.eastmoney.com/api/data/v1/get'
            '?reportName=RPT_DATA_BLOCKTRADE'
            '&columns=TRADE_DATE,SECURITY_CODE,SECURITY_NAME_ABBR,DEAL_VOLUME,DEAL_AMT,BUYER_NAME,SELLER_NAME'
            f'&filter=(TRADE_DATE%3E%3D%27{start}%27)(TRADE_DATE%3C%3D%27{end}%27)'
            f'&pageSize=500&pageNumber={page}&sortColumns=TRADE_DATE&sortTypes=-1'
        )
        raw = curl(url)
        if not raw:
            print(f'  page {page} 拉取失败，重试后放弃')
            break
        try:
            d = json.loads(raw)
        except Exception as e:
            print(f'  page {page} JSON 解析失败: {e}')
            break
        r = d.get('result') or {}
        rows = r.get('data') or []
        all_rows.extend(rows)
        pages = r.get('pages', 1)
        print(f'  page {page}/{pages} +{len(rows)}')
        if page >= pages or not rows:
            break
        page += 1
        time.sleep(0.8)
    return all_rows


def main():
    # 按月分三段拉，降低单请求翻页深度
    segs = [('2026-07-01', '2026-07-31'),
            ('2026-08-01', '2026-08-31'),
            ('2026-09-01', '2026-09-30')]
    total = []
    for s, e in segs:
        print(f'[{s} ~ {e}]')
        rows = pull_range(s, e)
        print(f'  小计 {len(rows)}')
        total.extend(rows)
        time.sleep(1)

    # 去重（同一笔记录跨页边界重复的兜底）
    seen = set()
    uniq = []
    for r in total:
        key = (r['TRADE_DATE'], r['SECURITY_CODE'], r.get('DEAL_AMT'),
               r.get('BUYER_NAME', ''), r.get('SELLER_NAME', ''))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(r)
    print(f'\n总笔数: {len(total)} → 去重后 {len(uniq)}')
    OUT.write_text(json.dumps(uniq, ensure_ascii=False), encoding='utf-8')
    print(f'落盘: {OUT} ({OUT.stat().st_size/1024:.0f} KB)')
    # 摘要
    dates = sorted({r['TRADE_DATE'][:10] for r in uniq})
    codes = {r['SECURITY_CODE'] for r in uniq}
    print(f'交易日数: {len(dates)} ({dates[0]} ~ {dates[-1]})，涉及股票: {len(codes)}')


if __name__ == '__main__':
    main()
