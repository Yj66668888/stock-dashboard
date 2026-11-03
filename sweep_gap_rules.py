# -*- coding: utf-8 -*-
"""隔夜质量规则参数扫描（2026-11-03）
比较几套排除规则：剔除比例 vs 次日跳空改善 vs 次日持有收益改善。
用法：python3 sweep_gap_rules.py"""
import json

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

WIN = 5


def cluster_t(a):
    a = np.array([x for x in a if x is not None and not np.isnan(x)], dtype=float)
    if len(a) < 5:
        return float('nan'), float('nan')
    sd = a.std(ddof=1)
    return float(a.mean()), float(a.mean() / (sd / np.sqrt(len(a)))) if sd > 0 else float('nan')


RULES = {
    'A 低开≥2次(不论回补)': lambda bc, rr, wst, wr: bc >= 2,
    'B 低开≥2次 且回补<50%': lambda bc, rr, wst, wr: bc >= 2 and not np.isnan(rr) and rr < 0.5,
    'C 低开≥2次 且回补<100%': lambda bc, rr, wst, wr: bc >= 2 and not np.isnan(rr) and rr < 0.999,
    'D 低开≥3次(不论回补)': lambda bc, rr, wst, wr: bc >= 3,
    'E 深坑≤-3%未补(当前R2)': lambda bc, rr, wst, wr: wst <= -3 and wr == 0,
    'F 深坑≤-2%未补': lambda bc, rr, wst, wr: wst <= -2 and wr == 0,
    'G 当前规则 R1|R2': lambda bc, rr, wst, wr: (bc >= 2 and not np.isnan(rr) and rr < 0.5) or (wst <= -3 and wr == 0),
    'H 低开≥2次 | 深坑≤-3未补': lambda bc, rr, wst, wr: bc >= 2 or (wst <= -3 and wr == 0),
    'I 低开≥2次 | 深坑≤-2未补': lambda bc, rr, wst, wr: bc >= 2 or (wst <= -2 and wr == 0),
}


def main():
    raw = json.load(open('.cache_daily_universe.json', encoding='utf-8'))
    bars_all = raw['bars']

    # rule -> {date: [flag per stock], gap, hold}
    acc = {k: {'flag': {}, 'gap': {}, 'hold': {}} for k in RULES}

    for code, bars in bars_all.items():
        if len(bars) < 60:
            continue
        try:
            arr = np.array([[float(b[1]), float(b[2]), float(b[3])] for b in bars], dtype=float)
            dates = [b[0] for b in bars]
        except Exception:
            continue
        op, cl, hi = arr[:, 0], arr[:, 1], arr[:, 2]
        n = len(cl)
        prev = np.concatenate(([np.nan], cl[:-1]))
        gaps = (op / prev - 1) * 100
        rec = (hi >= prev).astype(float)
        rec[0] = np.nan

        wg = sliding_window_view(gaps, WIN)
        wr = sliding_window_view(rec, WIN)
        bad = (wg <= -1.0)
        bc = bad.sum(axis=1)
        brc = (bad & (wr == 1)).sum(axis=1)
        with np.errstate(invalid='ignore'):
            rr = np.where(bc > 0, brc / np.maximum(bc, 1), np.nan)
            wst = np.nanmin(wg, axis=1)
            imin = np.nanargmin(wg, axis=1)
        wrec = np.take_along_axis(wr, imin[:, None], axis=1)[:, 0]

        for j in range(1, len(wg)):
            t = j + WIN - 1
            tp = t + 1
            if tp >= n or np.isnan(wst[j]) or np.isnan(gaps[tp]):
                continue
            d = dates[tp]
            g, h = gaps[tp], (cl[tp] / cl[t] - 1) * 100
            for k, fn in RULES.items():
                try:
                    f = bool(fn(int(bc[j]), rr[j], wst[j], wrec[j]))
                except Exception:
                    f = False
                a = acc[k]
                a['flag'].setdefault(d, []).append(f)
                a['gap'].setdefault(d, []).append(g)
                a['hold'].setdefault(d, []).append(h)

    print('%-26s %8s %10s %10s %10s %9s' % ('规则', '剔除率', '保留组跳空', '排除组跳空', '跳空差(t)', 'hold差(t)'))
    print('-' * 92)
    base_gap = [x for a in acc.values() for d in a['gap'] for x in a['gap'][d]]
    base_hold = [x for a in acc.values() for d in a['hold'] for x in a['hold'][d]]
    print('全样本均值： 跳空 %+.3f%%   hold %+.3f%%' % (np.mean(base_gap), np.mean(base_hold)))
    print('-' * 92)

    for k in RULES:
        a = acc[k]
        dates = sorted(a['flag'])
        rates, kg, dg, gh = [], [], [], []
        for d in dates:
            fl = np.array(a['flag'][d])
            if fl.all() or not fl.any():
                continue
            g = np.array(a['gap'][d])
            h = np.array(a['hold'][d])
            rates.append(fl.mean())
            kg.append(g[~fl].mean())
            dg.append(g[fl].mean())
            gh.append(h[~fl].mean() - h[fl].mean())
        kg, dg, gh = np.array(kg), np.array(dg), np.array(gh)
        mg, tg = cluster_t(kg - dg)
        mh, th = cluster_t(gh)
        print('%-26s %7.1f%% %+9.3f%% %+9.3f%% %+7.3f%%(%.1f) %+7.3f%%(%.1f)'
              % (k, 100 * np.mean(rates), kg.mean(), dg.mean(), mg, tg, mh, th))


if __name__ == '__main__':
    main()
