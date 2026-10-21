#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成「短线信号横向选型」Excel 报告 —— 0 公式错误，全部数值化"""
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

from openpyxl import Workbook  # noqa: E402
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side  # noqa: E402
from openpyxl.utils import get_column_letter  # noqa: E402

from compare_shortterm_signals import build, st  # noqa: E402

OUT = os.path.join(BASE, '短线信号横向选型报告.xlsx')

THIN = Side(style='thin', color='BFBFBF')
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
HDR_FILL = PatternFill('solid', fgColor='1F4E79')
HDR_FONT = Font(color='FFFFFF', bold=True, size=10)
TITLE_FONT = Font(bold=True, size=13, color='1F4E79')
ACT_FILL = PatternFill('solid', fgColor='C6EFCE')     # 推荐 绿
BAD_FILL = PatternFill('solid', fgColor='FFC7CE')     # 禁用 红
WARN_FILL = PatternFill('solid', fgColor='FFEB9C')    # 小样本 黄


def style_header(ws, row, ncol, width_map=None):
    for c in range(1, ncol + 1):
        cell = ws.cell(row=row, column=c)
        cell.fill = HDR_FILL
        cell.font = HDR_FONT
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        cell.border = BORDER
    ws.row_dimensions[row].height = 30
    if width_map:
        for c, w in width_map.items():
            ws.column_dimensions[get_column_letter(c)].width = w


def put(ws, r, vals, fmts=None, fill=None, bold=False):
    for i, v in enumerate(vals, 1):
        cell = ws.cell(row=r, column=i, value=v)
        cell.border = BORDER
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        if fmts and fmts[i - 1]:
            cell.number_format = fmts[i - 1]
        if fill:
            cell.fill = fill
        if bold:
            cell.font = Font(bold=True)
    return r + 1


def main():
    days = build()
    n_all = len(days)
    b1, bw1 = st(days, 'r1')[1], st(days, 'r1')[2]
    b3, bw3 = st(days, 'r3')[1], st(days, 'r3')[2]
    b5, bw5 = st(days, 'r5')[1], st(days, 'r5')[2]
    base = {'r1': b1, 'r3': b3, 'r5': b5}

    K = lambda lo, hi: (lambda x, lo=lo, hi=hi: lo <= x['k'] < hi)  # noqa: E731
    OK = lambda x: x['delta'] is not None and x['delta'] >= 0  # noqa: E731
    SIG = [
        ('① 资金转正 net>0', lambda x: x['net'] > 0, ''),
        ('② 日环比改善 Δ≥0', OK, ''),
        ('③ 回补比例≥40%', lambda x: x['ratio'] >= 0.40, 'BAD'),
        ('④ K∈[10,25) 深超卖', K(10, 25), 'BAD'),
        ('⑤ K∈[10,40)', K(10, 40), ''),
        ('⑥ K∈[10,45) 现网v2.2', K(10, 45), ''),
        ('⑦ K∈[20,40) v2.1', K(20, 40), 'ACT'),
        ('⑧ K∈[30,40)', K(30, 40), ''),
        ('⑨ K<25 真到位', lambda x: x['k'] < 25, 'BAD'),
        ('⑩ K≥70且K>D 高位强势', lambda x: x['k'] >= 70 and x['k'] > x['d'], 'BAD'),
        ('⑪ K∈[10,45)∧net>0', lambda x: 10 <= x['k'] < 45 and x['net'] > 0, ''),
        ('⑫ K∈[20,40)∧net>0', lambda x: 20 <= x['k'] < 40 and x['net'] > 0, ''),
        ('⑬ K∈[10,45)∧Δ≥0', lambda x: 10 <= x['k'] < 45 and OK(x), 'WARN'),
        ('⑭ K<25∧Δ≥0', lambda x: x['k'] < 25 and OK(x), ''),
        ('⑮ K∈[10,45)∧Δ≥0∧比例≥40%', lambda x: 10 <= x['k'] < 45 and OK(x) and x['ratio'] >= 0.40, 'WARN'),
        ('⑯ K∈[20,40)∧Δ≥0', lambda x: 20 <= x['k'] < 40 and OK(x), ''),
        ('⑰ K∈[20,40)∧比例≥40%', lambda x: 20 <= x['k'] < 40 and x['ratio'] >= 0.40, 'ACT'),
        ('⑱ K∈[20,45)∧Δ≥0', lambda x: 20 <= x['k'] < 45 and OK(x), 'WARN'),
        ('⑲ K∈[25,42)', K(25, 42), 'ACT'),
        ('⑳ K∈[25,42)∧Δ≥0', lambda x: 25 <= x['k'] < 42 and OK(x), 'WARN'),
        ('㉑ K∈[20,40)∧K>D', lambda x: 20 <= x['k'] < 40 and x['k'] > x['d'], ''),
        ('㉒ K∈[20,40)∧拐头', lambda x: 20 <= x['k'] < 40 and x['k'] > x['prev_k'], 'ACT'),
    ]

    wb = Workbook()

    # ============ Sheet1 结论与推荐 ============
    ws = wb.active
    ws.title = '结论与推荐'
    ws['A1'] = '短线信号横向选型 · 结论与推荐'
    ws['A1'].font = TITLE_FONT
    ws['A2'] = (f'样本：{n_all} 个交易日（{days[0]["date"]} ~ {days[-1]["date"]}，沪指同期单边下行 -9.17%）'
                f'｜口径：30分K 640根重放 KDJ(9,3,3) 收盘bar；资金=东财沪市单市场日线')
    ws['A2'].font = Font(size=9, color='808080')
    ws['A3'] = f'基准（不设任何条件）：次日 {b1:+.2f}% / 胜 {bw1:.1f}%　3日 {b3:+.2f}% / 胜 {bw3:.1f}%　5日 {b5:+.2f}% / 胜 {bw5:.1f}%'
    ws['A3'].font = Font(size=9, color='808080')

    r = 5
    ws.cell(row=r, column=1, value='★ 推荐方案（按你的持股周期二选一）').font = TITLE_FONT
    r += 1
    hdr = ['持股周期', '推荐信号', '样本', '次日均值', '次日胜率', '次日超额',
           '3日均值', '5日均值', '5日胜率', '最大单笔亏', '触发频率']
    style_header(ws, r, len(hdr), {i: 12 for i in range(1, 8)})
    ws.column_dimensions['A'].width = 14
    ws.column_dimensions['B'].width = 22
    for i in range(3, 12):
        ws.column_dimensions[get_column_letter(i)].width = 11
    r += 1

    def row_of(name):
        for nm, fn, tag in SIG:
            if nm == name:
                sel = [x for x in days if fn(x)]
                n1, a1, w1, _, m1 = st(sel, 'r1')
                n3, a3, w3, _, _ = st(sel, 'r3')
                n5, a5, w5, _, _ = st(sel, 'r5')
                return (len(sel), a1, w1, a1 - b1, a3, a5, w5, m1, len(sel) / n_all * 100)
        return None

    plans = [
        ('T+1 必走（1 天）', '⑲ K∈[25,42)', '次日超额最高且样本够用；缺点是第 3~5 天收益归零，不能拖'),
        ('可能拖到 3~5 天', '⑦ K∈[20,40) v2.1', '唯一「次日+5日双超基准」，短线失误有纠错空间'),
        ('加码/重仓档（慎用）', '⑰ K∈[20,40)∧比例≥40%', '6 样本全胜零亏损，但样本太少，只能小仓位试探'),
    ]
    fmts = [None, None, '0', '+0.00"%"', '0.0"%"', '+0.00"%"', '+0.00"%"', '+0.00"%"',
            '0.0"%"', '+0.00"%"', '0.0"%"']
    for period, sig, why in plans:
        v = row_of(sig)
        fill = ACT_FILL if '⑲' in sig or '⑦' in sig else WARN_FILL
        r = put(ws, r, [period, sig, v[0], v[1], v[2], v[3], v[4], v[5], v[6], v[7], v[8]],
                fmts, fill)
        ws.cell(row=r - 1, column=12, value=why).alignment = Alignment(wrap_text=True, vertical='center')
        ws.cell(row=r - 1, column=12).border = BORDER
    ws.column_dimensions['L'].width = 46

    r += 2
    ws.cell(row=r, column=1, value='✖ 明确不建议用于短线的信号').font = TITLE_FONT
    r += 1
    style_header(ws, r, 6)
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=1)
    r += 1
    bads = [
        ('⑨ K<25 真到位', '次日超额 +0.00%（等于没用），最大单笔亏 -0.49% —— 超卖钝化陷阱'),
        ('④ K∈[10,25) 深超卖', '同上：胜率 73.3% 看着高，但均值 -0.12%，一次大亏吃掉多次小赢'),
        ('⑩ K≥70 高位强势', '次日超额 -0.05%，5日 -0.55% —— 追高在这个样本里是负收益'),
        ('③ 回补比例≥40%（单用）', '触发 66.2% 的交易日，等于没筛；只配合作位置门的附属条件'),
        ('⑬⑮⑱ 各种 ∧ Δ≥0', '在日线尺度下筛出的全是同一批 6 天，资金门槛没有真实区分度'),
    ]
    for nm, why in bads:
        r = put(ws, r, [nm, why], None, BAD_FILL)
    ws.merge_cells(start_row=r - 1, start_column=2, end_row=r - 1, end_column=8)

    r += 2
    ws.cell(row=r, column=1, value='⚠ 风险与口径声明').font = TITLE_FONT
    r += 1
    notes = [
        '1. 样本仅覆盖 2026-07-01 ~ 10-20 共 74 天，且沪指单边下跌 9.17%。所有结论只在「下跌市」验证过，'
        '牛市/震荡市的 K 位置含义可能完全不同。',
        '2. 分钟级资金流无历史，「日内回补 N 亿」只能用日线代理（Δ / recall5 / 比例）。'
        '日线量级约为盘中同口径的 12 倍，绝对金额不可跨尺度直接套。',
        '3. ④⑨⑰㉒ 等小样本（n<8）信号的方向只能当假设，不足以定策略；实盘需再攒 10 笔以上复算。',
        '4. 收益按「信号日收盘 → 未来第 N 日收盘」计算，未扣手续费、未考虑开盘跳空，'
        '实盘滑点会吃掉一部分超额。',
        '5. 本表所有数字来自 market_fund_flow_daily.csv + 腾讯 mkline 实时重放，无任何主观估算。',
    ]
    for t in notes:
        ws.cell(row=r, column=1, value=t).alignment = Alignment(wrap_text=True, vertical='top')
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=12)
        ws.row_dimensions[r].height = 28
        r += 1

    # ============ Sheet2 全信号横向对比 ============
    ws2 = wb.create_sheet('全信号横向对比')
    ws2['A1'] = '全部候选信号 · 统一口径横向对比'
    ws2['A1'].font = TITLE_FONT
    hdr2 = ['信号', '样本', '触发频率', '次日均值', '次日胜率', '次日超额', '平均亏损', '最大单笔亏',
            '3日均值', '3日胜率', '3日超额', '5日均值', '5日胜率', '5日超额', '评价']
    style_header(ws2, 2, len(hdr2))
    for i, w in enumerate([24, 7, 10, 10, 10, 10, 10, 12, 10, 10, 10, 10, 10, 10, 40], 1):
        ws2.column_dimensions[get_column_letter(i)].width = w
    r = 3
    f2 = [None, '0', '0.0"%"', '+0.00"%"', '0.0"%"', '+0.00"%"', '+0.00"%"', '+0.00"%"',
          '+0.00"%"', '0.0"%"', '+0.00"%"', '+0.00"%"', '0.0"%"', '+0.00"%"', None]
    for nm, fn, tag in SIG:
        sel = [x for x in days if fn(x)]
        if not sel:
            r = put(ws2, r, [nm, 0, '—', '—', '—', '—', '—', '—', '—', '—', '—', '—', '—', '—', '无样本'])
            continue
        n1, a1, w1, al1, m1 = st(sel, 'r1')
        n3, a3, w3, _, _ = st(sel, 'r3')
        n5, a5, w5, _, _ = st(sel, 'r5')
        if n1 < 8:
            cmt = f'样本不足8（n={n1}），方向仅供参考，不足以定策略'
        elif a1 - b1 > 0.3 and a5 - b5 > 0:
            cmt = '★ 次日+5日双超基准，可放心持有数日'
        elif a1 - b1 > 0.3:
            cmt = '★ 次日超额突出，但只能做 T+1，拖到 5 日会回吐'
        elif a1 - b1 > 0.1:
            cmt = '有微弱正超额，边际不明显'
        else:
            cmt = '无超额 / 负超额，不建议单独使用'
        fill = {'ACT': ACT_FILL, 'BAD': BAD_FILL, 'WARN': WARN_FILL}.get(tag)
        r = put(ws2, r, [nm, n1, n1 / n_all * 100, a1, w1, a1 - b1, al1, m1,
                         a3, w3, a3 - b3, a5, w5, a5 - b5, cmt], f2, fill)
    for rr in range(3, r):
        ws2.cell(row=rr, column=15).alignment = Alignment(wrap_text=True, vertical='center')

    # ============ Sheet3 短线排名 ============
    ws3 = wb.create_sheet('短线排名榜')
    ws3['A1'] = '短线排名（按次日超额排序，浅绿=样本≥8 可入策略，浅黄=样本不足仅参考）'
    ws3['A1'].font = TITLE_FONT
    hdr3 = ['排名', '信号', '样本', '次日超额', '次日胜率', '触发频率', '平均几天一次', '最大单笔亏', '5日超额', '5日胜率']
    style_header(ws3, 2, len(hdr3))
    for i, w in enumerate([6, 26, 8, 12, 12, 12, 14, 13, 12, 12], 1):
        ws3.column_dimensions[get_column_letter(i)].width = w
    rows3 = []
    for nm, fn, tag in SIG:
        sel = [x for x in days if fn(x)]
        if not sel:
            continue
        n1, a1, w1, _, m1 = st(sel, 'r1')
        n5, a5, w5, _, _ = st(sel, 'r5')
        rows3.append((nm, len(sel), a1 - b1, w1, len(sel) / n_all * 100,
                      100 / (len(sel) / n_all * 100), m1, a5 - b5, w5))
    rows3.sort(key=lambda z: -z[2])
    f3 = [None, None, '0', '+0.00"%"', '0.0"%"', '0.0"%"', '0.0', '+0.00"%"', '+0.00"%"', '0.0"%"']
    r = 3
    for i, row in enumerate(rows3, 1):
        fill = ACT_FILL if row[1] >= 8 else WARN_FILL
        r = put(ws3, r, [i, row[0], row[1], row[2], row[3], row[4], row[5], row[6], row[7], row[8]], f3, fill)
    ws3.freeze_panes = 'A3'

    # ============ Sheet4 推荐信号逐笔明细 ============
    ws4 = wb.create_sheet('推荐信号逐笔明细')
    ws4['A1'] = '推荐信号命中的每一天 · 逐笔明细（可核对每一笔的来源）'
    ws4['A1'].font = TITLE_FONT
    hdr4 = ['信号', '日期', '收盘K', 'D值', '沪指收盘', '主力净(亿)', 'Δ(亿)', '回补比例',
            '次日%', '3日%', '5日%']
    style_header(ws4, 2, len(hdr4))
    for i, w in enumerate([24, 12, 9, 9, 12, 12, 10, 11, 10, 10, 10], 1):
        ws4.column_dimensions[get_column_letter(i)].width = w
    f4 = [None, None, '0.0', '0.0', '0.00', '+0.00', '+0.00', '0.0"%"', '+0.00"%"', '+0.00"%"', '+0.00"%"']
    r = 3
    for nm in ['⑲ K∈[25,42)', '⑦ K∈[20,40) v2.1', '⑰ K∈[20,40)∧比例≥40%', '㉒ K∈[20,40)∧拐头']:
        for nm2, fn, tag in SIG:
            if nm2 != nm:
                continue
            for x in days:
                if fn(x):
                    r = put(ws4, r, [nm, x['date'], x['k'], x['d'], x['close'],
                                     x['net'], x['delta'] if x['delta'] is not None else 0,
                                     x['ratio'] * 100,
                                     x['r1'], x['r3'], x['r5']], f4, ACT_FILL)
    ws4.freeze_panes = 'A3'

    wb.save(OUT)
    print('已生成:', OUT)
    print('工作表:', wb.sheetnames)


if __name__ == '__main__':
    main()
