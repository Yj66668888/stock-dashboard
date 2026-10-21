#!/usr/bin/env python3
"""
全盘 A 股 30 分钟 K 线批量抓取（2026-10-21）
=============================================
用途：为「跟随大盘 30 分钟」选股提供数据底座

数据源
  1. 股票名单：东财 push2 clist（一次分页拿全市场，约 5563 只）
     fields: f12代码 f13市场 f14名称 f2现价 f3涨幅 f6成交额 f8换手 f10量比
             f20总市值 f21流通市值
  2. 30 分 K：腾讯 ifzq mkline m30，请求 640 根 → 覆盖 2026-06-24 ~ 10-21
     格式 [time, open, close, high, low, volume]（注意 index 2 是 close）
     ⚠️ 根数必须精确填 640：
        - 320 根只到 8/18（覆盖不到 7 月）
        - 1000 根会被静默降级成 320 根（腾讯限制），比 640 还少
        - 640 根是实测能拿到最长历史的取值

坑位（血泪）
  - 本机代理会掐断连接 → curl 一律 --noproxy '*' + env -u 四个 proxy 变量
  - 新浪 K 线封本机 IP（2026-08-18）→ 只用腾讯
  - 并发过高触发限流 → 默认 5，失败重试 2 次并退避
  - 停牌票返回空 bars → 记录下来但不算失败

缓存
  .cache_m30/{code}.json  单只原始 bars，支持断点续跑（--resume 默认开）
  .cache_m30/_universe.json  股票名单
"""
import os
import sys
import json
import time
import subprocess
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

BASE = Path(__file__).resolve().parent
# 缓存目录带根数，避免 320/640 混淆后被断点续跑误判为已完成
BARS = 640
CACHE = BASE / f'.cache_m30_{BARS}'
CACHE.mkdir(exist_ok=True)

UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
ENV = {k: v for k, v in os.environ.items()
       if k.lower() not in ('http_proxy', 'https_proxy', 'all_proxy')}


def curl_json(url, timeout=12, retries=2):
    cmd = ['curl', '-s', '--noproxy', '*', '--connect-timeout', '6',
           '--max-time', str(timeout),
           '-H', f'User-Agent: {UA}', '-H', 'Referer: https://gu.qq.com/', url]
    for i in range(retries + 1):
        try:
            out = subprocess.run(cmd, capture_output=True, timeout=timeout + 5,
                                 env=ENV).stdout
            if out:
                return json.loads(out.decode('utf-8', errors='replace'))
        except Exception:
            pass
        if i < retries:
            time.sleep(0.4 * (i + 1))
    return None


def fetch_universe():
    """东财全市场 A 股名单（沪深主板+创业板+科创板，不含北交所）"""
    url = ('https://push2.eastmoney.com/api/qt/clist/get'
           '?pn={pn}&pz=100&po=1&np=1&fltt=2&invt=2&fid=f3'
           '&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23'
           '&fields=f12,f13,f14,f2,f3,f6,f8,f10,f20,f21')
    rows, total, pn = [], None, 1
    while True:
        d = curl_json(url.format(pn=pn))
        if not d or not d.get('data'):
            break
        data = d['data']
        total = total or data['total']
        diff = data.get('diff') or []
        if not diff:
            break
        rows.extend(diff)
        if len(rows) >= total or pn > 80:
            break
        pn += 1
        time.sleep(0.12)
    out = []
    for r in rows:
        code, mkt = r.get('f12'), r.get('f13')
        name = (r.get('f14') or '').strip()
        price = r.get('f2')
        if not code or not name:
            continue
        # 停牌 / 退市：现价为 '-'、0 或非数字
        if not isinstance(price, (int, float)) or price <= 0:
            continue
        if 'ST' in name.upper() or '退' in name:
            continue
        if code[0] in ('4', '8', '9'):      # 北交所
            continue
        prefix = 'sh' if mkt == 1 else 'sz'
        if code.startswith('688'):
            board = '科创板'
        elif code.startswith(('300', '301')):
            board = '创业板'
        elif code.startswith('60'):
            board = '沪主板'
        elif code.startswith('00'):
            board = '深主板'
        else:
            board = '其他'
        out.append({
            'code': code, 'symbol': prefix + code, 'name': name, 'board': board,
            'price': price, 'pct': r.get('f3'),
            'amount': r.get('f6'),          # 成交额（元）
            'turnover': r.get('f8'),
            'vol_ratio': r.get('f10'),
            'mktcap': r.get('f20'),
            'float_cap': r.get('f21'),
        })
    return out, total


def fetch_one(sym, retries=2):
    """单只 30 分 K，返回 (sym, bars, err)"""
    url = ('https://ifzq.gtimg.cn/appstock/app/kline/mkline'
           f'?param={sym},m30,,{BARS}')
    for i in range(retries + 1):
        d = curl_json(url)
        if d:
            node = (d.get('data') or {}).get(sym) or {}
            bars = node.get('m30')
            if bars:
                return sym, bars, None
            # 有响应但无 m30 → 停牌/新股，视作空数据不算错
            if node:
                return sym, [], 'empty'
        if i < retries:
            time.sleep(0.5 * (i + 1))
    return sym, None, 'fail'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--workers', type=int, default=5)
    ap.add_argument('--limit', type=int, default=0, help='只跑前 N 只（测速用）')
    ap.add_argument('--no-resume', action='store_true')
    args = ap.parse_args()

    uni_file = CACHE / '_universe.json'
    if uni_file.exists() and not args.no_resume:
        uni = json.load(open(uni_file))
        print(f'[universe] 缓存命中 {len(uni)} 只')
    else:
        print('[universe] 拉取东财全盘名单...')
        uni, total = fetch_universe()
        json.dump(uni, open(uni_file, 'w'), ensure_ascii=False)
        print(f'[universe] 全市场 {total} 只 → 过滤后 {len(uni)} 只')

    if args.limit:
        uni = uni[:args.limit]

    todo = [s for s in uni if args.no_resume or not (CACHE / f'{s["code"]}.json').exists()]
    print(f'[m30] 待抓 {len(todo)} / 总 {len(uni)}，并发 {args.workers}')
    if not todo:
        print('[m30] 全部已缓存，跳过抓取')
        return

    t0 = time.time()
    ok = empty = fail = 0
    fails = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(fetch_one, s['symbol']): s for s in todo}
        for n, fut in enumerate(as_completed(futs), 1):
            s = futs[fut]
            sym, bars, err = fut.result()
            if bars:
                json.dump(bars, open(CACHE / f'{s["code"]}.json', 'w'))
                ok += 1
            elif err == 'empty':
                json.dump([], open(CACHE / f'{s["code"]}.json', 'w'))
                empty += 1
            else:
                fail += 1
                fails.append(s['symbol'])
            if n % 100 == 0 or n == len(todo):
                el = time.time() - t0
                rate = n / el if el else 0
                eta = (len(todo) - n) / rate if rate else 0
                print(f'  {n}/{len(todo)}  ok={ok} empty={empty} fail={fail} '
                      f'{rate:.1f}只/s 已用{el:.0f}s 预计剩{eta:.0f}s')
    print(f'[done] 成功 {ok}，空数据 {empty}，失败 {fail}，耗时 {time.time()-t0:.0f}s')
    if fails:
        print('[fail] 前 10 个:', fails[:10])
        json.dump(fails, open(CACHE / '_fails.json', 'w'))


if __name__ == '__main__':
    main()
