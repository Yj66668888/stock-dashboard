#!/usr/bin/env python3
"""
暗盘资金注入脚本（明盘资金走前端 fetchCapitalFlow，IP 限流只影响本机）
- 暗盘: datacenter-web.eastmoney.com/.../RPT_DATA_BLOCKTRADE
- 默认拉最近2个交易日（因为大宗主要为盘后，盘前可能昨日数据未落库）
- 把每只股票的: 净买入(机构万元)、总成交(万元)、笔数、数据日期 注入 STOCKS[].blockFlow*
"""
import json
import subprocess
import time
import re
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path('/Users/fuckyouasshole/WorkBuddy/2026-07-01-11-18-34')


def curl(url, retry=2, timeout=15):
    for i in range(retry):
        r = subprocess.run(
            ['env', '-u', 'http_proxy', '-u', 'https_proxy',
             '-u', 'HTTP_PROXY', '-u', 'HTTPS_PROXY',
             'curl', '-s', '--noproxy', '*',
             '--connect-timeout', str(timeout),
             '--max-time', str(timeout + 5),
             url],
            capture_output=True, text=True
        )
        out = r.stdout.strip()
        if out and len(out) > 10:
            return out
        time.sleep(1)
    return ''


def get_pool_codes():
    """从 docs/index.html 的 STOCKS 数组提取所有 code（带 name）"""
    html = (ROOT / 'docs' / 'index.html').read_text(encoding='utf-8')
    m = re.search(r'const STOCKS\s*=\s*(\[[^\]]*?\])\s*;', html, re.S)
    if not m:
        m = re.search(r'const STOCKS\s*=\s*(\[.*?\])\s*;', html, re.S)
    pool = json.loads(m.group(1))
    return pool


def fetch_dark_range(start_date, end_date):
    """拉日期区间内全市场大宗交易（按日聚合）"""
    url = (
        'https://datacenter-web.eastmoney.com/api/data/v1/get'
        '?reportName=RPT_DATA_BLOCKTRADE'
        '&columns=TRADE_DATE,SECURITY_CODE,DEAL_VOLUME,DEAL_AMT,'
        'BUYER_NAME,SELLER_NAME'
        f'&filter=(TRADE_DATE%3E=%27{start_date}%27)'
        f'(TRADE_DATE%3C=%27{end_date}%27)'
        '&pageSize=500&pageNumber=1&sortColumns=TRADE_DATE&sortTypes=-1'
    )
    raw = curl(url, retry=2, timeout=20)
    if not raw:
        return {}, []
    try:
        d = json.loads(raw)
        rows = d.get('result', {}).get('data', [])
        # 按日期+code 聚合（每只票每天一条）
        per_day = defaultdict(lambda: defaultdict(lambda: {'net': 0.0, 'total': 0.0, 'count': 0}))
        for r in rows:
            d_str = r['TRADE_DATE'][:10]
            code = r['SECURITY_CODE']
            amt_wan = r['DEAL_AMT'] / 10000
            per_day[d_str][code]['total'] += amt_wan
            per_day[d_str][code]['count'] += 1
            buyer = r.get('BUYER_NAME', '')
            seller = r.get('SELLER_NAME', '')
            if '机构专用' in buyer and '机构专用' not in seller:
                per_day[d_str][code]['net'] += amt_wan
            elif '机构专用' in seller and '机构专用' not in buyer:
                per_day[d_str][code]['net'] -= amt_wan
        return {k: dict(v) for k, v in per_day.items()}, rows
    except Exception as e:
        print(f'  datacenter parse err: {e}')
        return {}, []


def merge_into_stocks(pool, per_day_block):
    """把暗盘数据合并到每只票（取最近一个有数据的一天作为'当日'）"""
    if not per_day_block:
        return 0
    # 按日期排序，从最新往旧回退
    dates = sorted(per_day_block.keys(), reverse=True)
    matched = 0
    for s in pool:
        code_full = s['code']
        code_short = code_full[2:]  # 去掉 sh/sz
        # 找最近一天该票有大宗记录的
        for d in dates:
            day_data = per_day_block[d].get(code_short)
            if day_data:
                s['blockFlowNetWan'] = round(day_data['net'], 2)
                s['blockFlowTotalWan'] = round(day_data['total'], 2)
                s['blockFlowCount'] = day_data['count']
                s['blockFlowDate'] = d
                s['blockFlowSource'] = 'datacenter'
                matched += 1
                break
        else:
            # 该票当日及往前 N 天都无大宗
            s.setdefault('blockFlowNetWan', None)
            s.setdefault('blockFlowTotalWan', None)
            s.setdefault('blockFlowCount', None)
            s.setdefault('blockFlowDate', None)
    return matched


def inject_to_html(html_path, pool):
    """把新的 pool 写回 HTML 的 STOCKS 数组（贪婪匹配到第一个 '];'）"""
    html = html_path.read_text(encoding='utf-8')
    serialized = json.dumps(pool, ensure_ascii=False, separators=(',', ':'))
    new_html, n = re.subn(
        r'(const\s+STOCKS\s*=\s*)\[.*?\](\s*;\s*\n)',
        lambda m: m.group(1) + serialized + m.group(2),
        html, count=1, flags=re.S
    )
    if n == 0:
        print(f'  ⚠️ {html_path.name}: 未匹配到 STOCKS 数组')
        return False
    if new_html == html:
        print(f'  ⏭ {html_path.name}: 内容已是最新（无变更）')
        return True
    html_path.write_text(new_html, encoding='utf-8')
    print(f'  ✅ {html_path.name}: STOCKS 已更新（{len(pool)} 只）')
    return True


def main():
    pool = get_pool_codes()
    print(f'池子: {len(pool)} 只')

    end_d = datetime.now().strftime('%Y-%m-%d')
    start_d = (datetime.now() - timedelta(days=4)).strftime('%Y-%m-%d')
    print(f'\n[1/1] 拉 {start_d} ~ {end_d} 全市场大宗...')
    per_day_block, raw_rows = fetch_dark_range(start_d, end_d)
    print(f'  涉及股票-日对: {sum(len(v) for v in per_day_block.values())}')
    total_days = len(per_day_block)
    print(f'  日期数: {total_days}')
    for d in sorted(per_day_block.keys()):
        print(f'    {d}: {len(per_day_block[d])} 只票')

    print(f'\n[2/2] 合并注入 STOCKS...')
    matched = merge_into_stocks(pool, per_day_block)
    print(f'  暗盘命中 {matched}/{len(pool)}')

    print('\n[3] 写入三份 HTML...')
    for fn in ['deploy/index.html', 'docs/index.html', 'index.html']:
        p = ROOT / fn
        if p.exists():
            inject_to_html(p, pool)
    print('\n✅ done (明盘资金由前端 fetchCapitalFlow 实时刷新)')


if __name__ == '__main__':
    main()
