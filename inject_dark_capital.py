#!/usr/bin/env python3
"""
暗盘资金注入脚本（明盘资金走前端 fetchCapitalFlow，IP 限流只影响本机）
- 暗盘: datacenter-web.eastmoney.com/.../RPT_DATA_BLOCKTRADE
- 默认拉最近4个交易日（大宗主要为盘后，盘前可能昨日数据未落库）
- 字段语义（按 T = 脚本运行当日）：
    * blockFlowNetWan           当日机构净买入（万元）
    * blockFlowTotalWan         当日总成交（万元）
    * blockFlowCount            当日笔数
    * blockFlowDate             当日日期（YYYY-MM-DD）
    * blockFlowRecentNetWan     近4日累计机构净买入（万元）
    * blockFlowRecentCount      近4日累计笔数
    * blockFlowLatestDate       最近一次有交易的日期（可能早于今日）
- 当日无数据时 blockFlowNetWan 设为 None（前端显示"-- 今日无"）
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
    """把暗盘数据合并到每只票：
    - blockFlowNetWan / blockFlowDate 只反映今日（T 日）
    - blockFlowRecentNetWan 反映近 N 日累计（含今日）
    - blockFlowLatestDate 反映最近一次大宗发生的日期
    """
    if not per_day_block:
        # 无任何数据：清空所有 block* 字段
        for s in pool:
            s['blockFlowNetWan'] = None
            s['blockFlowTotalWan'] = None
            s['blockFlowCount'] = None
            s['blockFlowDate'] = None
            s['blockFlowRecentNetWan'] = None
            s['blockFlowRecentCount'] = None
            s['blockFlowLatestDate'] = None
            s['blockFlowSource'] = 'datacenter-empty'
        return 0

    dates = sorted(per_day_block.keys(), reverse=True)
    today_str = datetime.now().strftime('%Y-%m-%d')
    matched_today = 0
    matched_recent = 0

    for s in pool:
        code_full = s['code']
        code_short = code_full[2:]  # 去掉 sh/sz

        # ----- 字段初始化 -----
        s['blockFlowNetWan'] = None
        s['blockFlowTotalWan'] = None
        s['blockFlowCount'] = None
        s['blockFlowDate'] = None

        # ----- 1) 今日 -----
        today_data = per_day_block.get(today_str, {}).get(code_short)
        if today_data:
            s['blockFlowNetWan'] = round(today_data['net'], 2)
            s['blockFlowTotalWan'] = round(today_data['total'], 2)
            s['blockFlowCount'] = today_data['count']
            s['blockFlowDate'] = today_str
            matched_today += 1

        # ----- 2) 近 4 日累计 + 最近日期 -----
        recent_net = 0.0
        recent_count = 0
        latest_date = None
        for d in dates:
            day_data = per_day_block[d].get(code_short)
            if day_data:
                recent_net += day_data['net']
                recent_count += day_data['count']
                if latest_date is None:
                    latest_date = d

        s['blockFlowRecentNetWan'] = round(recent_net, 2) if recent_count > 0 else None
        s['blockFlowRecentCount'] = recent_count if recent_count > 0 else None
        s['blockFlowLatestDate'] = latest_date
        s['blockFlowSource'] = 'datacenter'

        if recent_count > 0:
            matched_recent += 1

    print(f'  今日命中 {matched_today}/{len(pool)} · 近4日命中 {matched_recent}/{len(pool)}')
    return matched_today


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

    print('\n[3] 写入三份 HTML...')
    for fn in ['deploy/index.html', 'docs/index.html', 'index.html']:
        p = ROOT / fn
        if p.exists():
            inject_to_html(p, pool)
    print('\n✅ done (明盘资金由前端 fetchCapitalFlow 实时刷新)')


if __name__ == '__main__':
    main()
