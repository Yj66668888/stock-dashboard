#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
validate_h60_kdj.py —— 60分KDJ 状态有效性检验（2026-11-04）

问题：h60_kdj.py 的四档状态，对「次日收益」有没有区分度？
     （尤其 ↓下行 / ↘高位回落 是否真的比 ✦/↑/⚡ 差）

口径：
  - 全市场随机抽样 n 只（默认 300），每只抓腾讯 m60 320 根（≈80 交易日）
  - 每天最后一根（15:00）收盘后判定状态（无未来函数：K/D 窗口截止当日）
  - 次日收益 = 次日 15:00 close / 当日 15:00 close − 1
  - 按交易日聚类 t 检验（有效样本量 = 交易日数，不是样本条数）
  - ⚠️ m60 仅 320 根 → 每只最多 ~79 个判定日，2026-08 之前的数据没有

统计归因，不构成投资建议。
"""
import argparse
import json
import random
import sys
import threading
import time
from collections import defaultdict

import urllib.request

sys.path.insert(0, '.')
import h60_kdj as H60

LOCK = threading.Lock()
CACHE = '.cache_m60_universe.json'


def tstat_cluster(days):
    """按交易日聚类的 t：日收益先平均，再对日均值做单样本 t"""
    vals = [v for v in days.values() if v['n'] >= 3]
    if len(vals) < 5:
        return None, len(vals)
    m = sum(v['mean'] for v in vals) / len(vals)
    var = sum((v['mean'] - m) ** 2 for v in vals) / (len(vals) - 1)
    se = (var / len(vals)) ** 0.5
    return (m / se if se > 0 else 0.0), len(vals)


def worker(codes, out, cache):
    for code in codes:
        with LOCK:
            if code in cache:
                out[code] = cache[code]
                continue
        ks = H60.fetch(code, retries=1)
        with LOCK:
            cache[code] = ks
        out[code] = ks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--n', type=int, default=300)
    args = ap.parse_args()

    # ---- 抽样票池：从隔夜检验的抽样名单复用，保证可比 ----
    import io
    codes = []
    try:
        d = json.load(open('.cache_daily_universe.json', encoding='utf-8'))
        allc = list(d['bars'].keys())
        random.seed(42)
        codes = random.sample(allc, min(args.n, len(allc)))
    except Exception:
        try:
            cache = json.load(open(CACHE, encoding='utf-8'))
            codes = list(cache.keys())[:args.n]
        except Exception:
            print('无票池来源')
            return
    print('抽样 %d 只' % len(codes))

    # ---- 并发抓取 ----
    try:
        cache = json.load(open(CACHE, encoding='utf-8'))
    except Exception:
        cache = {}
    out = {}
    ths = []
    CH = 24
    for i in range(0, len(codes), CH):
        t = threading.Thread(target=worker, args=(codes[i:i + CH], out, cache))
        t.start()
        ths.append(t)
        if len(ths) >= 8:
            for t in ths:
                t.join()
            ths = []
    for t in ths:
        t.join()
    json.dump(cache, open(CACHE, 'w'))
    ok_codes = {c: ks for c, ks in out.items() if ks and len(ks) >= H60.MIN_BARS}
    print('抓到 m60: %d / %d' % (len(ok_codes), len(codes)))

    # ---- 逐日状态 + 次日收益 ----
    # 判定用「截至当天末」的前缀序列（无未来函数），不是当天 4 根
    day_key = lambda x: str(x['date'])[:8]
    # 每只票按天找「天末索引」
    day_end_idx = {}          # code -> {day: 全局索引}
    for code, ks in ok_codes.items():
        m = {}
        for idx, x in enumerate(ks):
            m[day_key(x)] = idx
        day_end_idx[code] = m
    bucket = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for code, ks in ok_codes.items():
        days = defaultdict(list)
        for x in ks:
            days[day_key(x)].append(x)
        dlist = sorted(days)
        for i in range(1, len(dlist)):
            prev, cur = days[dlist[i - 1]], days[dlist[i]]
            if len(prev) < 4 or len(cur) < 4:
                continue              # 半日/停牌不判
            pc, cc = prev[-1]['close'], cur[-1]['close']
            if not pc:
                continue
            st = H60.calc(ks[:day_end_idx[code][dlist[i - 1]] + 1])
            if not st:
                continue
            ret = (cc / pc - 1) * 100
            bucket[dlist[i]][st['state']]['r'].append(ret)

    # ---- 汇总 ----
    order = ['gold', 'up', 'near', 'fade', 'down']
    names = {'gold': '✦刚金叉', 'up': '↑上行', 'near': '⚡将金叉',
             'fade': '↘高位回落', 'down': '↓下行'}
    print()
    print('%-8s %7s %8s %8s %6s' % ('状态', '日均', '聚类t', '天数', '条数'))
    res = {}
    for s in order:
        days = {}
        for d in bucket:
            rs = bucket[d].get(s, {}).get('r', [])
            if rs:
                days[d] = {'mean': sum(rs) / len(rs), 'n': len(rs)}
        m = sum(v['mean'] for v in days.values()) / max(len(days), 1) if days else 0
        cnt = sum(v['n'] for v in days.values())
        t, nd = tstat_cluster(days)
        res[s] = {'mean': m, 't': t, 'ndays': nd, 'n': cnt}
        print('%-8s %+7.3f%% %8s %6d %6d' % (
            names[s], m, ('%.2f' % t) if t is not None else '-', nd, cnt))

    # ok vs not-ok
    ok_days, bad_days = {}, {}
    for d in bucket:
        o = [r for s in ('gold', 'up', 'near') for r in bucket[d].get(s, {}).get('r', [])]
        b = [r for s in ('fade', 'down') for r in bucket[d].get(s, {}).get('r', [])]
        if o:
            ok_days[d] = {'mean': sum(o) / len(o), 'n': len(o)}
        if b:
            bad_days[d] = {'mean': sum(b) / len(b), 'n': len(b)}
    mo = sum(v['mean'] for v in ok_days.values()) / max(len(ok_days), 1)
    mb = sum(v['mean'] for v in bad_days.values()) / max(len(bad_days), 1)
    to, ndo = tstat_cluster(ok_days)
    tb, ndb = tstat_cluster(bad_days)
    print()
    print('准入组(✦/↑/⚡): %+.3f%%/天 (t=%s, %d天)  vs  排除组(↘/↓): %+.3f%%/天 (t=%s, %d天)'
          % (mo, ('%.2f' % to) if to is not None else '-', ndo,
             mb, ('%.2f' % tb) if tb is not None else '-', ndb))
    diff = mo - mb
    print('差: %+.3f%%/天' % diff)

    json.dump({'n_codes': len(ok_codes), 'per_state': res,
               'ok': {'mean': mo, 't': to, 'ndays': ndo},
               'bad': {'mean': mb, 't': tb, 'ndays': ndb}},
              open('_h60_validation.json', 'w'), ensure_ascii=False, indent=1)
    print('saved -> _h60_validation.json')


if __name__ == '__main__':
    main()
