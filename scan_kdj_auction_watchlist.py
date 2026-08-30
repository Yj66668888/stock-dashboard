# -*- coding: utf-8 -*-
"""
全市场30分KDJ竞价候选池扫描
================================
用途：为仪表盘「低位竞价」Tab 提供每日候选池。
逻辑：扫 daily_predictions.json 的全部 60/00 主板票（约3000只，无ST），
      拉30分钟K线算 KDJ(9,3,3)（丢弃未完成bar），筛：
        1. K <= 35（30分钟低位）
        2. 趋势 = 上升(K>D) 或 待升(K<=D 但 乖离收敛/K上拐)   ← 与 rescan 的 T0/T1 口径一致
      输出注入 deploy/index.html 的 KDJ_AUCTION_WATCHLIST 变量。

为什么盘前扫：竞价阶段(9:15-9:25)当天第一根30分K尚未走完，
30分KDJ仍停在上一交易日收盘状态 → 8:30/16:30 预计算即可，竞价时前端只拉实时行情。

用法：python scan_kdj_auction_watchlist.py [--limit N] [--workers N]
      --limit   限制扫描票数（调试用）
      --workers 并发数（默认3，腾讯>8并发触发风控，勿调高）
"""
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
PRED_FILE = os.path.join(BASE, 'daily_predictions.json')
HTML = os.path.join(BASE, 'deploy', 'index.html')
OUT_JSON = os.path.join(BASE, 'kdj_auction_watchlist.json')

K_MAX = 35          # 30分钟K值上限
MAX_ITEMS = 300     # 候选池上限（控制前端行情请求量：300/60=5批）
WORKERS = 3         # 腾讯并发上限
BARS_N = 60         # 拉取30分K线根数（KDJ(9,3,3)收敛足够）

_CLEAN_ENV = {k: v for k, v in os.environ.items()
              if k.lower() not in ('http_proxy', 'https_proxy', 'http_proxys', 'all_proxy')}


def curl_get(url, referer=None, retries=3, timeout=10):
    cmd = ['curl', '-s', '--noproxy', '*', '--connect-timeout', str(timeout),
           '-H', 'User-Agent: Mozilla/5.0']
    if referer:
        cmd += ['-H', f'Referer: {referer}']
    cmd.append(url)
    for _ in range(retries):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 5, env=_CLEAN_ENV)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout
        except Exception:
            pass
        time.sleep(0.3)
    return None


def curl_json(url, referer=None):
    raw = curl_get(url, referer)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def drop_incomplete_bar(bars):
    """丢弃未走完的当根K线（腾讯mkline时间戳为bar终点标注）"""
    if not bars:
        return bars
    try:
        bar_time = datetime.strptime(bars[-1][0][:12], '%Y%m%d%H%M')
    except (ValueError, TypeError, IndexError):
        return bars
    if bar_time > datetime.now():
        return bars[:-1]
    return bars


def calc_kdj_m30(code):
    """腾讯30分K线 → KDJ(9,3,3)，返回 K/D/前值/最新收盘/最后bar日期"""
    d = curl_json(f'https://ifzq.gtimg.cn/appstock/app/kline/mkline?param={code},m30,,{BARS_N}',
                  referer='https://gu.qq.com/')
    try:
        bars = drop_incomplete_bar(d['data'][code]['m30'])
    except (KeyError, TypeError):
        return None
    if len(bars) < 20:
        return None
    # 停牌/长期无数据：最后bar超过7天视为不活跃，剔除
    try:
        last_dt = datetime.strptime(bars[-1][0][:8], '%Y%m%d')
    except (ValueError, TypeError, IndexError):
        return None
    if last_dt < datetime.now() - timedelta(days=7):
        return None
    # [time, open, close, high, low, vol] → (high, low, close)
    hlc = [(float(b[3]), float(b[4]), float(b[2])) for b in bars]
    k, d_ = 50.0, 50.0
    series = []
    for i in range(len(hlc)):
        hi = max(x[0] for x in hlc[max(0, i - 8):i + 1])
        lo = min(x[1] for x in hlc[max(0, i - 8):i + 1])
        c = hlc[i][2]
        rsv = 50.0 if hi == lo else (c - lo) / (hi - lo) * 100
        k = (2 / 3) * k + (1 / 3) * rsv
        d_ = (2 / 3) * d_ + (1 / 3) * k
        series.append((k, d_))
    k_prev, d_prev = series[-2]
    return {'k': round(k, 1), 'd': round(d_, 1),
            'k_prev': round(k_prev, 1), 'd_prev': round(d_prev, 1),
            'close': float(bars[-1][2]), 'bar_date': bars[-1][0][:8]}


def classify(r):
    """与 rescan_low_position.classify_trend 同口径：
       up = K>D（上升）；prep_up = K<=D 但乖离收敛或K上拐（待升）；down = 仍下跌"""
    gap, gap_prev = r['k'] - r['d'], r['k_prev'] - r['d_prev']
    if r['k'] > r['d']:
        return 'up'
    if gap > gap_prev or r['k'] > r['k_prev']:
        return 'prep_up'
    return 'down'


def to_tencent_code(bare):
    return ('sh' if bare.startswith('6') else 'sz') + bare


def main():
    limit = 0
    if '--limit' in sys.argv:
        limit = int(sys.argv[sys.argv.index('--limit') + 1])
    if '--workers' in sys.argv:
        global WORKERS
        WORKERS = max(1, int(sys.argv[sys.argv.index('--workers') + 1]))

    pred = json.load(open(PRED_FILE))
    universe = [{'code': str(r['code']), 'name': r.get('name', '')}
                for r in pred.get('all_results') or []]
    if limit:
        universe = universe[:limit]
    print(f"=== 全市场30分KDJ竞价候选池扫描 {datetime.now().strftime('%Y-%m-%d %H:%M')} ===")
    print(f"底数: {pred.get('scan_date')} | {len(universe)}只主板票 | K≤{K_MAX} 且 上升/待升 | 并发{WORKERS}")

    t0 = time.time()
    results, fail = [], 0

    def worker(s):
        tc = to_tencent_code(s['code'])
        r = calc_kdj_m30(tc)
        if r is None:
            return None
        r['code'] = tc
        r['bare'] = s['code']
        r['name'] = s['name']
        return r

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(worker, s): s['code'] for s in universe}
        for i, fut in enumerate(as_completed(futs), 1):
            try:
                r = fut.result()
            except Exception:
                r = None
            if r is None:
                fail += 1
            else:
                r['trend'] = classify(r)
                if r['k'] <= K_MAX and r['trend'] in ('up', 'prep_up'):
                    r['tier'] = 0 if r['trend'] == 'up' else 1
                    results.append(r)
            if i % 300 == 0:
                print(f"  进度 {i}/{len(universe)} 命中{len(results)} 失败{fail} 耗时{time.time()-t0:.0f}s")

    elapsed = time.time() - t0
    # 排序：T0上升优先 → K值小者优先（更超卖）
    results.sort(key=lambda x: (x['tier'], x['k']))
    capped = results[:MAX_ITEMS]
    print(f"\n扫描完成: {len(universe)}只 耗时{elapsed:.0f}s | 失败{fail}")
    print(f"命中 K≤{K_MAX} 且上升/待升: {len(results)}只 → 取前{len(capped)}只"
          f"(T0上升 {sum(1 for x in capped if x['tier']==0)} / T1待升 {sum(1 for x in capped if x['tier']==1)})")

    # 数据日期 = 候选池最后一根30分K的日期（全市场取众数更稳，这里取capped[0]）
    bar_date = capped[0]['bar_date'] if capped else ''
    items = [{'code': r['code'], 'name': r['name'], 'k': r['k'], 'd': r['d'],
              'kPrev': r['k_prev'], 'tier': r['tier'],
              'trend': 'up' if r['tier'] == 0 else 'prep_up',
              'close': r['close']} for r in capped]

    payload = {
        'date': bar_date, 'genAt': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'scanned': len(universe), 'hits': len(results), 'count': len(items),
        'items': items
    }

    # ---- 注入 HTML ----
    html = open(HTML, encoding='utf-8').read()
    new_var = 'var KDJ_AUCTION_WATCHLIST = ' + json.dumps(payload, ensure_ascii=False, separators=(',', ':')) + ';'
    if re.search(r'var KDJ_AUCTION_WATCHLIST\s*=', html):
        html = re.sub(r'var KDJ_AUCTION_WATCHLIST\s*=\s*\{.*?\};', new_var, html, flags=re.S)
        action = '更新'
    else:
        html = html.replace('var aucState = {', new_var + '\n\nvar aucState = {', 1)
        action = '新增'
    open(HTML, 'w', encoding='utf-8').write(html)
    print(f"✅ 已{action} KDJ_AUCTION_WATCHLIST({len(items)}只) → deploy/index.html")

    # ---- 落盘 JSON 备查 ----
    json.dump(payload, open(OUT_JSON, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    print(f"✅ 备查文件 → {OUT_JSON}")

    if capped[:5]:
        print("\nTop5 预览:")
        for r in capped[:5]:
            print(f"  {r['bare']} {r['name']:<6} K={r['k']:>5} D={r['d']:>5} "
                  f"{'↑上升' if r['tier']==0 else '↗待升'} 收{r['close']}")


if __name__ == '__main__':
    main()
