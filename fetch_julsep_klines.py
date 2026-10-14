#!/usr/bin/env python3
"""拉大宗universe全部票的日K（腾讯ifzq fqkline, 130根, 3并发），缓存 julsep_klines.json"""
import json
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path('/Users/fuckyouasshole/WorkBuddy/2026-07-01-11-18-34')
CACHE = ROOT / 'julsep_klines.json'


def curl(url, timeout=12):
    r = subprocess.run(
        ['env', '-u', 'http_proxy', '-u', 'https_proxy',
         '-u', 'HTTP_PROXY', '-u', 'HTTPS_PROXY',
         'curl', '-s', '--noproxy', '*', '--connect-timeout', str(timeout),
         '--max-time', str(timeout + 3), url],
        capture_output=True, text=True)
    return r.stdout.strip()


def fetch_one(code):
    market = 'sh' if code.startswith('6') else ('sz' if code.startswith(('0', '3')) else 'bj')
    full = market + code
    url = (f'https://ifzq.gtimg.cn/appstock/app/fqkline/get'
           f'?param={full},day,,,130,fq')
    raw = curl(url)
    if not raw:
        return code, None
    try:
        d = json.loads(raw)
        data = d.get('data', {}).get(full, {})
        bars = data.get('qfqday') or data.get('day')
        if not bars:
            return code, None
        # [date, open, close, high, low, volume, ...]
        out = [(b[0], float(b[1]), float(b[2]), float(b[3]), float(b[4]), float(b[5])) for b in bars]
        return code, out
    except Exception:
        return code, None


def main():
    rows = json.load(open(ROOT / 'block_trades_jul_sep.json'))
    codes = sorted({r['SECURITY_CODE'] for r in rows})
    print(f'universe: {len(codes)} 只')

    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    todo = [c for c in codes if c not in cache]
    print(f'待拉: {len(todo)} 只（缓存 {len(cache)}）')

    ok = fail = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=3) as ex:
        futs = {ex.submit(fetch_one, c): c for c in todo}
        for i, fu in enumerate(as_completed(futs), 1):
            code, bars = fu.result()
            if bars:
                cache[code] = bars
                ok += 1
            else:
                fail += 1
            if i % 100 == 0:
                print(f'  {i}/{len(todo)} ok={ok} fail={fail} ({time.time()-t0:.0f}s)')
                CACHE.write_text(json.dumps(cache))

    CACHE.write_text(json.dumps(cache))
    print(f'完成: ok={ok} fail={fail} 总缓存 {len(cache)} ({time.time()-t0:.0f}s)')


if __name__ == '__main__':
    main()
