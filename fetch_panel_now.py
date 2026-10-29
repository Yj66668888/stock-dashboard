# -*- coding: utf-8 -*-
"""
面板数据抓取器（2026-10-28）
=====================================================
目标：把回测面板从「julsep_klines.json（到 10-14）」升级到 **到当日最新交易日**，
供 7/1 ~ 今天（10/27）的样本外验证使用。

数据源：**只用腾讯**
  - 日线  ifzq.gtimg.cn/appstock/app/kline/kline?param=<mkt><code>,day,,,320,
          → 320 根（2025-07-02 起），**非复权**（腾讯 qfq 参数实测返回空，已弃用）
  - 30分  ifzq.gtimg.cn/appstock/app/kline/mkline?param=<mkt><code>,m30,,800

⚠️ 本机踩坑记录：
  - ifzq 是频率型 WAF，间歇 501 → 并发压到 8、失败最多重试 2 次、失败即跳过
  - 本地必须绕开代理：curl --noproxy '*' + env -u 各 proxy 变量
  - 501 时不返回 CORS 头，是正常现象，不是数据错

输出：
  .cache_kdaily_now.json    {code: [[date,open,close,high,low,vol], ...]}（升序）
  .cache_index_m30_now.json sh000001 30 分钟原始 bars
"""
import json, os, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor

BASE = os.getcwd()
OUT_STOCK = os.path.join(BASE, '.cache_kdaily_now.json')
OUT_IDX = os.path.join(BASE, '.cache_index_m30_now.json')

_CLEAN_ENV = {k: v for k, v in os.environ.items() if 'proxy' not in k.lower()}


def mkt_of(code):
    c = str(code)
    if c.startswith(('6', '9', '8', '4')):
        return 'sh' if c.startswith('6') else 'bj'
    return 'sz'


def curl_get(url, timeout=10):
    cmd = ['curl', '-s', '--noproxy', '*', '--connect-timeout', '6',
           '--max-time', str(timeout), '-H', 'User-Agent: Mozilla/5.0',
           '-H', 'Referer: https://gu.qq.com/', url]
    try:
        r = subprocess.run(cmd, capture_output=True, env=_CLEAN_ENV, timeout=timeout + 5)
        return r.stdout
    except Exception:
        return b''


def fetch_daily(code, retries=2):
    mkt = mkt_of(code)
    sym = mkt + code
    url = f'https://ifzq.gtimg.cn/appstock/app/kline/kline?param={sym},day,,,320,'
    for attempt in range(retries + 1):
        raw = curl_get(url)
        if not raw:
            time.sleep(0.4 * (attempt + 1))
            continue
        try:
            d = json.loads(raw.decode('utf-8', errors='ignore'))
            node = d['data'][sym]
            bars = node.get('day') or []
            if len(bars) < 100:
                return None
            out = []
            for b in bars:
                out.append([b[0], float(b[1]), float(b[2]), float(b[3]),
                            float(b[4]), float(b[5])])
            return code, out
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            time.sleep(0.4 * (attempt + 1))
    return None


def main():
    codes = sorted(json.load(open(os.path.join(BASE, 'julsep_klines.json'))).keys())
    print(f'共 {len(codes)} 只待拉取…', flush=True)

    res, failed = {}, []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=8) as ex:
        for i, r in enumerate(ex.map(fetch_daily, codes)):
            if r:
                res[r[0]] = r[1]
            else:
                failed.append(codes[i])
            if (i + 1) % 100 == 0:
                print(f'  {i+1}/{len(codes)}  成功{len(res)} 失败{len(failed)} '
                      f'{time.time()-t0:.0f}s', flush=True)
    print(f'日线完成：成功 {len(res)} / {len(codes)}，失败 {len(failed)}，'
          f'耗时 {time.time()-t0:.0f}s', flush=True)
    if failed:
        print('失败清单:', ' '.join(failed[:40]), '…' if len(failed) > 40 else '')
    json.dump(res, open(OUT_STOCK, 'w'))
    print('写入', OUT_STOCK, flush=True)

    # ---- 大盘 30 分 ----
    raw = None
    for attempt in range(4):
        raw = curl_get('https://ifzq.gtimg.cn/appstock/app/kline/mkline'
                       '?param=sh000001,m30,,800', timeout=15)
        if raw:
            try:
                d = json.loads(raw.decode('utf-8', errors='ignore'))
                bars = d['data']['sh000001']['m30']
                if bars and len(bars) > 200:
                    json.dump({'data': {'sh000001': {'m30': bars}}}, open(OUT_IDX, 'w'))
                    print('大盘30分：%d 根，最新 %s' % (len(bars), bars[-1][0]), flush=True)
                    break
            except Exception:
                pass
        print('  大盘30分第%d次失败，重试…' % (attempt + 1), flush=True)
        time.sleep(2)
    else:
        print('❌ 大盘30分拉取失败', flush=True)

    # 数据新鲜度自检
    if res:
        last = {}
        for c, bs in res.items():
            last.setdefault(bs[-1][0], 0)
            last[bs[-1][0]] += 1
        top = sorted(last.items(), key=lambda x: -x[1])[:5]
        print('最新交易日分布（前5）:', top)


if __name__ == '__main__':
    main()
