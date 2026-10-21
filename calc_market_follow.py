#!/usr/bin/env python3
"""
全盘 A 股「跟随大盘 30 分钟」度量（2026-10-21）
==============================================
业务问题：用户要一批"大盘 30 分钟涨它也涨、跌它也跌"的票，
         用来把手上的大盘 30 分 KDJ 大盘信号直接套在个股上。

口径（全部基于 30 分钟 bar 的收益率）
  基准：上证指数 sh000001 的 m30 收盘序列
  个股：与基准同一 bar 时间戳对齐后取收益率

指标设计（每一条都对应一句人话）
  corr         Pearson 相关系数 —— 走势像不像
  beta         Cov(个股,大盘)/Var(大盘) —— 涨跌比例，1.0 = 同步振幅
  same_dir     方向一致率 —— 大盘涨它涨、大盘跌它跌 的 bar 占比（最直观）
  up_capture   大盘上涨 bar 中，个股均涨 ÷ 大盘均涨 —— 涨的时候跟多少
  down_capture 大盘下跌 bar 中，个股均跌 ÷ 大盘均跌 —— 跌的时候挨多少
  up_bias      up_capture - down_capture —— 正=涨得更猛跌得更少（不对称优势）
  te           跟踪误差 = 收益差序列的标准差 —— 越小越贴合
  drag_bars    参与统计的 bar 数（数据完整性）

两个榜单（分开看，别混）
  A 镜像榜 (score_mirror)：corr 高 + beta≈1 + 同向率高 + te 小
     → 用途：把大盘信号当个股信号用
  B 不对称榜 (score_asym)：corr 够用 + up_bias 高（涨跟紧、跌抗跌）
     → 用途：这才是真正有超额的方向

硬过滤（保证结果真的能买）
  - bar 数不足基准 80% 的剔除（次新股 / 长期停牌）
  - 当日成交额 < 1 亿的剔除（买不进、卖不出）
  - corr 计算要求 ≥200 组配对样本，避免小样本假相关
"""
import os
import json
import math
import statistics as stt
import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
BARS = 640
CACHE = BASE / f'.cache_m30_{BARS}'
UNI = CACHE / '_universe.json'

# ---------- 时间窗：只统计 7-10 月 ----------
D_START = '20260701'
D_END = '20261022'


def load_bench():
    """上证 30 分 K bars 列表"""
    p = CACHE / '_bench.json'
    if not p.exists():
        raise SystemExit('缺少基准数据，先抓 sh000001 存到 _bench.json')
    raw = json.load(open(p))
    # 兼容两种存法：裸 bars 列表 / 腾讯原始响应 {"data":{...}}
    if isinstance(raw, list):
        bars = raw
    else:
        bars = ((raw.get('data') or {}).get('sh000001') or {}).get('m30')
    if not bars:
        raise SystemExit('基准数据为空')
    print(f'[bench] 加载基准 {len(bars)} 根，'
          f'覆盖 {bars[0][0]} ~ {bars[-1][0]}')
    return bars


def ret(bars):
    """30 分收益率序列 [(time, r)]，跳过跨日首 bar（避免隔夜跳空污染）"""
    out = []
    prev_day = None
    prev_close = None
    for b in bars:
        t, close = b[0], float(b[2])
        day = t[:8]
        if D_START <= day <= D_END:
            if prev_close and day == prev_day and prev_close > 0:
                out.append((t, close / prev_close - 1))
            prev_close, prev_day = close, day
        elif day > D_END:
            break
    return out


def pearson(x, y):
    n = len(x)
    if n < 30:
        return None
    mx, my = sum(x) / n, sum(y) / n
    sxy = sum((x[i] - mx) * (y[i] - my) for i in range(n))
    sxx = sum((v - mx) ** 2 for v in x)
    syy = sum((v - my) ** 2 for v in y)
    if sxx <= 0 or syy <= 0:
        return None
    return sxy / math.sqrt(sxx * syy)


def analyze_one(item, bench_map):
    code = item['code']
    f = CACHE / f'{code}.json'
    if not f.exists():
        return None
    bars = json.load(open(f))
    if not bars or len(bars) < 200:
        return None
    sr = ret(bars)
    if len(sr) < 200:
        return None
    # 与基准对齐
    xs, ys = [], []
    for t, r in sr:
        if t in bench_map:
            xs.append(bench_map[t])
            ys.append(r)
    n = len(xs)
    if n < 200:
        return None

    corr = pearson(xs, ys)
    if corr is None:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((xs[i] - mx) * (ys[i] - my) for i in range(n)) / n
    var_m = sum((v - mx) ** 2 for v in xs) / n
    beta = cov / var_m if var_m > 0 else None
    if beta is None:
        return None

    # 方向一致率
    same = sum(1 for i in range(n) if (xs[i] > 0) == (ys[i] > 0) and xs[i] != 0)
    nonz = sum(1 for x in xs if x != 0)
    same_dir = same / nonz if nonz else 0

    # 上下行捕获
    up_r = [ys[i] for i in range(n) if xs[i] > 0]
    dn_r = [ys[i] for i in range(n) if xs[i] < 0]
    up_m = [xs[i] for i in range(n) if xs[i] > 0]
    dn_m = [xs[i] for i in range(n) if xs[i] < 0]
    mean_up_s = sum(up_r) / len(up_r) if up_r else 0
    mean_dn_s = sum(dn_r) / len(dn_r) if dn_r else 0
    mean_up_m = sum(up_m) / len(up_m) if up_m else 0
    mean_dn_m = sum(dn_m) / len(dn_m) if dn_m else 0
    up_cap = mean_up_s / mean_up_m if mean_up_m else None
    dn_cap = mean_dn_s / mean_dn_m if mean_dn_m else None
    if up_cap is None or dn_cap is None:
        return None

    # 跟踪误差
    diff = [ys[i] - xs[i] for i in range(n)]
    te = stt.pstdev(diff) if len(diff) > 1 else 0

    # 个股自身波动（用于算 R² 和振幅参考）
    var_s = sum((v - my) ** 2 for v in ys) / n
    sd_s = math.sqrt(var_s) * 100
    sd_m = math.sqrt(var_m) * 100

    return {
        **item,
        'bars_used': n,
        'corr': corr, 'r2': corr * corr,
        'beta': beta,
        'same_dir': same_dir,
        'up_cap': up_cap, 'dn_cap': dn_cap,
        'up_bias': up_cap - dn_cap,
        'te': te,
        'amp_s': sd_s, 'amp_m': sd_m,
        'ret_sum': sum(ys) * 100,          # 区间个股累计涨跌 %
        'mkt_ret_sum': sum(xs) * 100,
    }


def main():
    uni = json.load(open(UNI))
    bench_bars = load_bench()
    bench_map = dict(ret(bench_bars))
    print(f'[bench] 基准 bar 数 {len(bench_map)}，覆盖 {min(bench_map)} ~ {max(bench_map)}')

    rows = []
    skipped = {'no_file': 0, 'few_bars': 0, 'bad_stat': 0}
    for it in uni:
        r = analyze_one(it, bench_map)
        if r is None:
            skipped['bad_stat'] += 1
            continue
        rows.append(r)
    print(f'[calc] 有效样本 {len(rows)} / 总 {len(uni)}')

    # ---------- 硬过滤：流动性 + 数据完整 ----------
    MIN_AMT = 1e8          # 日均成交额 ≥ 1 亿
    MIN_BARS_RATIO = 0.8   # 至少覆盖基准 bar 数的 80%（剔次新股/长期停牌）
    bench_n = len(bench_map)
    liquid = [r for r in rows
              if r['bars_used'] >= bench_n * MIN_BARS_RATIO
              and isinstance(r.get('amount'), (int, float)) and r['amount'] >= MIN_AMT]
    print(f'[filter] 流动性+完整度过滤后 {len(liquid)} 只'
          f'（剔除 {len(rows)-len(liquid)} 只）')

    # ---------- 榜单 A：镜像跟随 ----------
    # corr 越高越好；beta 偏离 1 越小越好；same_dir 越高越好；te 越小越好
    def mirror(r):
        s = 0.40 * max(0.0, min(1.0, (r['corr'] - 0.30) / 0.60))     # corr 0.3→0 分，0.9→满分
        s += 0.25 * max(0.0, 1 - abs(r['beta'] - 1.0) / 0.60)        # beta=1 满分，偏离0.6 归零
        s += 0.20 * max(0.0, min(1.0, (r['same_dir'] - 0.60) / 0.28))  # 同向率 60%→0，88%→满分
        s += 0.15 * max(0.0, 1 - r['te'] / 0.012)                     # te 1.2% 归零
        return s * 100

    # ---------- 榜单 B：不对称跟随（涨跟紧、跌抗跌）----------
    # ⚠️ 硬性前置：up_bias 必须 > 0。否则 corr 高的高 beta 票会靠 corr 刷分，
    #    排出来一堆 bias 为负（跌得比涨得还多）的票，那根本不是"跌抗跌"。
    def asym(r):
        if r['corr'] < 0.55:            # 先确保是真跟随，否则 up_bias 无意义
            return -999
        if r['up_bias'] <= 0:           # 硬门槛：上行捕获必须真的超过下行捕获
            return -999
        s = 0.40 * min(1.0, r['up_bias'] / 0.60)              # bias 0.6 → 满分
        s += 0.25 * max(0.0, min(1.0, (r['corr'] - 0.45) / 0.45))
        s += 0.20 * max(0.0, min(1.0, (r['same_dir'] - 0.62) / 0.26))
        s += 0.15 * max(0.0, 1 - abs(r['beta'] - 1.0) / 1.2)   # beta 别太离谱
        return s * 100

    for r in liquid:
        r['avg_amount_yi'] = r['amount'] / 1e8
        # 相对不对称：涨/跌捕获之比，>1 才是真抗跌（对高beta票更公平）
        r['asym_ratio'] = (r['up_cap'] / r['dn_cap']
                           if r['dn_cap'] > 0.3 else None)
        r['score_mirror'] = round(mirror(r), 1)
        r['score_asym'] = round(asym(r), 1)

    out = {
        'gen_at': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'window': f'{D_START} ~ {D_END}',
        'bench_bars': bench_n,
        'total_raw': len(uni), 'total_valid': len(rows), 'total_liquid': len(liquid),
        'rows': liquid,
    }
    json.dump(out, open(BASE / 'market_follow_result.json', 'w'), ensure_ascii=False)
    print(f'[save] market_follow_result.json，{len(liquid)} 只')

    print('\n=== 榜单A 镜像跟随 TOP15 ===')
    print(f"{'代码':<8}{'名称':<10}{'corr':>6}{'beta':>7}{'同向':>7}{'上行捕':>8}{'下行捕':>8}{'TE':>7}{'A分':>7}")
    for r in sorted(liquid, key=lambda x: -x['score_mirror'])[:15]:
        print(f"{r['code']:<8}{r['name']:<10}{r['corr']:>6.2f}{r['beta']:>7.2f}"
              f"{r['same_dir']*100:>6.1f}%{r['up_cap']:>8.2f}{r['dn_cap']:>8.2f}"
              f"{r['te']*100:>6.2f}%{r['score_mirror']:>7.1f}")

    print('\n=== 榜单B 不对称跟随 TOP15（规定：bias必须>0）===')
    _b = [r for r in liquid if r['score_asym'] > -900]
    print(f'   满足 corr>=0.55 且 bias>0 的票：{len(_b)} 只')
    print(f"{'代码':<8}{'名称':<10}{'corr':>6}{'beta':>7}{'同向':>7}{'上行捕':>8}{'下行捕':>8}{'bias':>7}{'比':>7}{'B分':>7}")
    for r in sorted(_b, key=lambda x: -x['score_asym'])[:15]:
        print(f"{r['code']:<8}{r['name']:<10}{r['corr']:>6.2f}{r['beta']:>7.2f}"
              f"{r['same_dir']*100:>6.1f}%{r['up_cap']:>8.2f}{r['dn_cap']:>8.2f}"
              f"{r['up_bias']:>7.2f}{r['asym_ratio']:>7.2f}{r['score_asym']:>7.1f}")


if __name__ == '__main__':
    main()
