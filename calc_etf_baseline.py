#!/usr/bin/env python3
"""
对照组：宽基 ETF 的 30 分钟跟随度（2026-10-21）
================================================
个股到底"跟不跟"得有多紧，缺个参照物是看不出来的。
这里拉几只流动性最好的宽基 ETF 算同一套指标，给个股榜单一个锚。

逻辑：若 ETF corr ≈ 0.99 而个股天花板只有 0.75，
      那"用大盘信号交易个股"这件事本身就自带大量残差风险。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import calc_market_follow as C

ETFS = [
    ('sh510300', '沪深300ETF', '宽基'),
    ('sh510050', '上证50ETF', '宽基'),
    ('sh510500', '中证500ETF', '宽基'),
    ('sh512880', '证券ETF', '行业'),
    ('sh512480', '半导体ETF', '行业'),
    ('sh510880', '红利ETF', '策略'),
]


def main():
    import fetch_all_m30 as F
    bmap = dict(C.ret(json.load(open(C.CACHE / '_bench.json'))['data']['sh000001']['m30']))

    print(f"{'名称':<12}{'corr':>7}{'beta':>7}{'同向率':>8}{'上行捕':>8}{'下行捕':>8}"
          f"{'不对称':>8}{'跟踪误差':>9}")
    print('-' * 70)
    out = []
    for sym, name, kind in ETFS:
        _, bars, err = F.fetch_one(sym)
        if not bars:
            print(f'{name:<12} 抓取失败 {err}')
            continue
        # analyze_one 从缓存读数据，ETF 不在原始名单里，先落盘
        json.dump(bars, open(C.CACHE / f'{sym[2:]}.json', 'w'))
        item = {'code': sym[2:], 'symbol': sym, 'name': name, 'board': kind}
        r = C.analyze_one(item, bmap)
        if not r:
            print(f'{name:<12} 计算失败')
            continue
        out.append(r)
        print(f"{name:<12}{r['corr']:>7.3f}{r['beta']:>7.2f}{r['same_dir']*100:>7.1f}%"
              f"{r['up_cap']:>8.2f}{r['dn_cap']:>8.2f}{r['up_bias']:>8.2f}"
              f"{r['te']*100:>8.2f}%")

    json.dump(out, open(C.BASE / 'etf_benchmark.json', 'w'), ensure_ascii=False)
    print('\n[save] etf_benchmark.json')

    # 与个股对比
    rows = json.load(open(C.BASE / 'market_follow_result.json'))['rows']
    idx_a = [r for r in out if r['name'] in ('沪深300ETF', '上证50ETF')]
    if idx_a and rows:
        maxc = max(r['corr'] for r in rows)
        etfc = max(r['corr'] for r in idx_a)
        print(f'\n【锚点对比】宽基ETF corr 最高 {etfc:.3f}｜'
              f'全市场个股 corr 最高 {maxc:.3f}｜差距 {etfc-maxc:.3f}')


if __name__ == '__main__':
    main()
