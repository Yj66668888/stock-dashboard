#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fetch_pool_daily.py —— 补齐**用户自有 40 只票池**的日线，供 slump_stage5_pool 使用

背景：`.cache_kdaily_now.json`（899 只）是上一次全抓取时的宇宙，里面只命中了
今天票池 40 只中的 12 只（30%）。用 12 只做过拟合式结论不可信，所以单独把这
40 只抓一遍，覆盖到 2026-04（slump 面板起点 2026-04-20）。

数据源与 fetch_panel_now 完全一致（腾讯 kline 320 根，未复权），保证口径同源。
"""
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, '.cache_pool_daily.json')
OLDER = os.path.join(BASE, '_archive_20261028', 'cache', '.cache_kdaily_now.json')

sys.path.insert(0, BASE)
from fetch_panel_now import fetch_daily          # noqa: E402
from slump_stage5_pool import load_pool_codes    # noqa: E402


def main():
    codes = load_pool_codes()
    print('票池 %d 只' % len(codes))
    old = {}
    if os.path.exists(OLDER):
        old = json.load(open(OLDER))
    todo = [c for c in codes if c not in old or len(old.get(c, [])) < 100]
    print('需现抓 %d 只（其余复用旧缓存）' % len(todo))

    got = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        for res in ex.map(lambda c: fetch_daily(c), todo):
            if res:
                got[res[0]] = res[1]

    out = {}
    for c in codes:
        rows = got.get(c) or old.get(c)
        if rows and len(rows) >= 60:
            out[c] = rows
    json.dump(out, open(OUT, 'w'))

    print('落盘 %s：%d/%d 只' % (OUT, len(out), len(codes)))
    miss = [c for c in codes if c not in out]
    if miss:
        print('  ⚠️ 抓不到（次新/停牌/北交所）：', ','.join(miss))
    # 覆盖检查：能否覆盖到面板起点 2026-04-20
    ok = 0
    for c, rows in out.items():
        first = rows[0][0]
        last = rows[-1][0]
        if first <= '2026-04-20':
            ok += 1
        else:
            print('  ⚠️ %s 数据起点 %s，晚于面板起点（不参与统计）' % (c, first))
    print('可覆盖面板起点的：%d/%d' % (ok, len(out)))
    if out:
        anyc = list(out.values())[0]
        print('样本末期：', anyc[-1][0])
    return 0


if __name__ == '__main__':
    sys.exit(main())
