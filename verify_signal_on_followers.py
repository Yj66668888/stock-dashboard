#!/usr/bin/env python3
"""
验证：推荐出来的「跟随票」上，大盘30分信号是否真的有效
======================================================
光给跟随度排名是不够的 —— 必须回答「所以呢」：
  如果这批票只是「指数复制品」，用大盘信号交易它们，能赚到超额吗？

做法（对照实验）
  实验组：在大盘30分收盘 K ∈ [KLO, KHI) 的信号日**次日**，持有一天
  对照组：同一只票在**全部交易日**随机持有的次日平均收益
  结论指标：信号超额 = 实验组均值 - 对照组均值；以及信号日胜率

同时对「非跟随票」做同样计算，两者对比才是关键：
  若 跟随票信号超额 > 非跟随票信号超额 → 跟随度筛选确实加成，值得用
  若 两者差不多 → 跟随度没带来信息增量，那还不如直接买ETF

数据源：个股日K用腾讯 kline day（这里用日线，因为要的是"次日收益"）
"""
import os
import json
import time
import subprocess
import argparse
import statistics as stt
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE = Path(__file__).resolve().parent
CACHE = BASE / '.cache_m30_640'
UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
ENV = {k: v for k, v in os.environ.items()
       if k.lower() not in ('http_proxy', 'https_proxy', 'all_proxy')}

KLO, KHI = 20.0, 40.0      # 信号区间（与当前 v2.1b 生效档一致）


def curl_json(url, timeout=12, retries=2):
    cmd = ['curl', '-s', '--noproxy', '*', '--connect-timeout', '6',
           '--max-time', str(timeout), '-H', f'User-Agent: {UA}',
           '-H', 'Referer: https://gu.qq.com/', url]
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


def bench_daily_k():
    """大盘：每个交易日收盘时的30分K值 + 当日收盘价"""
    bars = json.load(open(CACHE / '_bench.json'))['data']['sh000001']['m30']
    hlc = [(float(b[3]), float(b[4]), float(b[2])) for b in bars]
    Ks, Ds = [50.0], [50.0]
    for i in range(len(hlc)):
        s = max(0, i - 8)
        hh = max(x[0] for x in hlc[s:i + 1])
        ll = min(x[1] for x in hlc[s:i + 1])
        rsv = 50.0 if hh == ll else (hlc[i][2] - ll) / (hh - ll) * 100
        k = (Ks[i] * 2 + rsv) / 3
        d = (Ds[i] * 2 + k) / 3
        Ks.append(k)
        Ds.append(d)
    daily = {}
    for i, b in enumerate(bars):
        t = b[0]
        daily[t[:8]] = {'k': Ks[i + 1], 'd': Ds[i + 1], 'close': float(b[2])}
    return daily


def fetch_day_k(sym, n=90):
    d = curl_json('https://ifzq.gtimg.cn/appstock/app/kline/kline'
                  f'?param={sym},day,,,{n}')
    if not d:
        return []
    node = (d.get('data') or {}).get(sym) or {}
    bars = node.get('day') or node.get('qfqday') or []
    out = []
    for b in bars:
        try:
            out.append((b[0].replace('-', ''), float(b[2])))
        except Exception:
            continue
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--top', type=int, default=40, help='取跟随榜前N只做验证')
    args = ap.parse_args()

    res = json.load(open(BASE / 'market_follow_result.json'))
    rows = res['rows']
    bk = bench_daily_k()
    dates = sorted(bk.keys())
    # 信号日：收盘30分K落在区间内
    sig_dates = [d for d in dates if KLO <= bk[d]['k'] < KHI]
    print(f'[bench] 交易日 {len(dates)} 天，信号日 {len(sig_dates)} 天'
          f'（K∈[{KLO:g},{KHI:g})）')
    print('       信号日:', ' '.join(f'{d[4:6]}/{d[6:8]}' for d in sig_dates))

    # 对照组窗口必须与信号日同一区间，否则 base 会掺入窗口外的天数，
    # 导致「超额」失真（股市不同月份平均收益差异很大）
    date_set = set(dates)

    top = sorted(rows, key=lambda x: -x['score_mirror'])[:args.top]
    # 对照组：跟随度最低的一批（均已通过同样的流动性/完整性过滤）
    bottom = sorted(rows, key=lambda x: x['score_mirror'])[:args.top]

    def run_group(group, tag):
        out = []
        with ThreadPoolExecutor(max_workers=5) as ex:
            futs = {ex.submit(fetch_day_k, r['symbol']): r for r in group}
            for fut in as_completed(futs):
                r = futs[fut]
                ks = fut.result()
                if len(ks) < 60:
                    continue
                closes = dict(ks)
                keys = [k for k, _ in ks]
                idx = {d: i for i, d in enumerate(keys)}
                # 次日收益：signal_day 的下一个交易日收盘 vs 当日收盘
                sig_r, all_r = [], []
                for i in range(len(keys) - 1):
                    d0, d1 = keys[i], keys[i + 1]
                    c0, c1 = closes[d0], closes[d1]
                    if c0 <= 0:
                        continue
                    rr = c1 / c0 - 1
                    if d0 in date_set:      # 基准窗口内的日子才算对照
                        all_r.append(rr)
                    if d0 in sig_dates:
                        sig_r.append(rr)
                if len(sig_r) < 8 or len(all_r) < 50:
                    continue
                ms = stt.mean(sig_r)
                ma = stt.mean(all_r)
                ex_ = ms - ma
                win = sum(1 for x in sig_r if x > 0) / len(sig_r)
                out.append({**r, 'sig_n': len(sig_r), 'all_n': len(all_r),
                            'sig_mean': ms * 100, 'base_mean': ma * 100,
                            'excess': ex_ * 100, 'sig_win': win})
        if not out:
            print(f'[{tag}] 无有效样本')
            return []
        n = len(out)
        print(f'\n[{tag}] 有效 {n} 只')
        print(f'  信号日次日均值平均   {stt.mean(x["sig_mean"] for x in out):+.2f}%')
        print(f'  全期次日均值平均     {stt.mean(x["base_mean"] for x in out):+.2f}%')
        print(f'  **信号超额平均       {stt.mean(x["excess"] for x in out):+.2f}%**')
        print(f'  信号日次日胜率平均   {stt.mean(x["sig_win"] for x in out)*100:.1f}%')
        print(f'  超额为正的票占比     {sum(1 for x in out if x["excess"]>0)/n*100:.1f}%')
        return out

    # ---- 锚点组：ETF 在同样信号下的表现（回答"要不要费劲选个股"）----
    etf_out = []
    for sym, name in [('sh510300', '沪深300ETF'), ('sh510050', '上证50ETF')]:
        ks = fetch_day_k(sym)
        if len(ks) < 60:
            continue
        closes = dict(ks)
        keys = [k for k, _ in ks]
        s_r, a_r = [], []
        for i in range(len(keys) - 1):
            d0, d1 = keys[i], keys[i + 1]
            c0, c1 = closes[d0], closes[d1]
            if c0 <= 0:
                continue
            rr = c1 / c0 - 1
            if d0 in date_set:
                a_r.append(rr)
            if d0 in sig_dates:
                s_r.append(rr)
        if len(s_r) < 8 or not a_r:
            continue
        ms, ma = stt.mean(s_r), stt.mean(a_r)
        etf_out.append({'name': name, 'sym': sym, 'sig_n': len(s_r),
                        'sig_mean': ms * 100, 'base_mean': ma * 100,
                        'excess': (ms - ma) * 100,
                        'sig_win': sum(1 for x in s_r if x > 0) / len(s_r)})
    if etf_out:
        print('\n[ETF锚点] 同样信号下 ETF 的表现')
        for e in etf_out:
            print(f"  {e['name']:<12} n={e['sig_n']:2d} 信号次日均 {e['sig_mean']:+.2f}% "
                  f"全期 {e['base_mean']:+.2f}% 超额 {e['excess']:+.2f}% "
                  f"胜率 {e['sig_win']*100:.1f}%")

    A = run_group(top, '跟随榜TOP')
    B = run_group(bottom, '跟随榜BOTTOM')

    if A and B:
        # excess 在构造时已 ×100 转成百分比，这里不能再乘
        ea = stt.mean(x['excess'] for x in A)
        eb = stt.mean(x['excess'] for x in B)
        print('\n' + '=' * 70)
        print('【关键对比】跟随度筛选有没有信息增量')
        print(f'  跟随榜TOP    信号超额 {ea:+.2f}%')
        print(f'  跟随榜BOTTOM 信号超额 {eb:+.2f}%')
        print(f'  差值         {ea - eb:+.2f}%')
        if ea - eb > 0.15:
            print('  → 跟随度筛选确实带来增量，值得在这批票上套用大盘信号')
        else:
            print('  → 跟随度没有明显信息增量，大盘信号在这批票上无额外优势')
        print('=' * 70)
        if etf_out:
            be = max(etf_out, key=lambda x: x['excess'])
            print(f'【重要】ETF锚点 {be["name"]} 超额 {be["excess"]:+.2f}% vs '
                  f'个股TOP {ea:+.2f}% → 差 {be["excess"]-ea:+.2f}%')
            if be['excess'] > ea:
                print('  → 若目标就是复制大盘，直接买ETF的信号效果优于精挑个股')
        print('=' * 70)

    json.dump({'sig_dates': sig_dates, 'gen_at': datetime.now().strftime('%F %T'),
               'top': A, 'bottom': B, 'etf': etf_out},
              open(BASE / 'follow_verify_result.json', 'w'), ensure_ascii=False)
    print('\n[save] follow_verify_result.json')

    if A:
        print('\n=== 跟随榜TOP：信号超额最高前12 ===')
        print(f"{'代码':<8}{'名称':<10}{'corr':>6}{'beta':>7}{'信号次日均':>10}"
              f"{'全期次日均':>10}{'超额':>8}{'信号胜率':>9}")
        for r in sorted(A, key=lambda x: -x['excess'])[:12]:
            print(f"{r['code']:<8}{r['name']:<10}{r['corr']:>6.2f}{r['beta']:>7.2f}"
                  f"{r['sig_mean']:>9.2f}%{r['base_mean']:>9.2f}%"
                  f"{r['excess']:>7.2f}%{r['sig_win']*100:>8.1f}%")


if __name__ == '__main__':
    main()
