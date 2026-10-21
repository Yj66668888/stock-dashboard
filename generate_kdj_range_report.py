#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 scan_kdj_range.py 的扫描结果落成 Excel（0 公式，全为回测实测值）

数据来源与口径见脚本内说明；所有数字均来自
  - 30分K：腾讯 ifzq.gtimg.cn mkline sh000001 m30 640根，重放 KDJ(9,3,3)
  - 资金：东财沪市单市场日线 market_fund_flow_daily.csv
  - 样本区间：2026-07-01 ~ 2026-10-20，74 个交易日
"""
import os
import sys

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
from scan_kdj_range import build_days, stats  # noqa: E402

OUT = os.path.join(BASE, 'K区间与资金阈值扫描_K10-45.xlsx')

HDR_FILL = PatternFill('solid', fgColor='1F4E79')
HDR_FONT = Font(bold=True, color='FFFFFF', size=11)
TITLE_FONT = Font(bold=True, size=14, color='1F4E79')
NOTE_FONT = Font(size=9, color='808080')
WARN_FILL = PatternFill('solid', fgColor='FFF2CC')   # 黄底 = 重要假设/风险
GOOD_FILL = PatternFill('solid', fgColor='E2EFDA')
BAD_FILL = PatternFill('solid', fgColor='FCE4EC')


def put_header(ws, row, cols):
    for i, c in enumerate(cols, start=1):
        cell = ws.cell(row=row, column=i, value=c)
        cell.fill = HDR_FILL
        cell.font = HDR_FONT
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    # 用字符串设置冻结，避免 ws.cell() 提前占位导致 append 多出空行
    ws.freeze_panes = f'A{row + 1}'


def widths(ws, ws_widths):
    for i, w in enumerate(ws_widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w


def pct(v):
    return None if v is None else round(v, 2)


def main():
    days, base = build_days()
    N = len(days)
    wb = Workbook()

    # ---------------- Sheet 1：结论与建议 ----------------
    ws = wb.active
    ws.title = '结论与建议'
    widths(ws, [34, 26, 26, 26, 22])
    ws['A1'] = 'K 区间放宽到 10~45 + 资金回流 5~10 亿 —— 回测结论'
    ws['A1'].font = TITLE_FONT
    ws['A2'] = (f'样本：2026-07-01 ~ 2026-10-20，{N} 个交易日，期间上证 '
                f'{(days[-1]["close"]/days[0]["close"]-1)*100:+.2f}%（单边下跌市）')
    ws['A3'] = (f'基准（不设任何条件）：次日 {base["r1"][1]:+.2f}% / 胜率 {base["r1"][2]:.1f}%　'
                f'3日 {base["r3"][1]:+.2f}%　5日 {base["r5"][1]:+.2f}% / 胜率 {base["r5"][2]:.1f}%')
    ws['A3'].font = Font(size=10, bold=True)

    rows = [
        ('', '', '', '', ''),
        ('✅ 可以改，但要接受一个交换：用「能拿 3~5 天」换「提醒次数多 3 倍」', '', '', '', ''),
        ('对比项', '现用 K20~40 ∧ Δ≥10亿', '新方案 K10~45 ∧ Δ≥5亿', '差异', '数据来源'),
        ('触发频率', '2/74 天（约每 37 天一次）', '6/74 天（约每 12 天一次）', '提醒次数 ×3', 'Sheet 触发频率'),
        ('次日胜率', '100.0%（样本仅 2）', '83.3%（样本 6）', '样本变可靠，胜率回落', 'Sheet 区间扫描'),
        ('次日均收益', '+1.53%', '+0.80%', '超额 +0.93%，仍明显跑赢基准', 'Sheet 区间扫描'),
        ('5 日胜率', '0.0%', '33.3%', '仍低于基准 37.7%', 'Sheet 区间扫描'),
        ('可持有', '2 样本极端，不可靠', '只看次日，别过夜', '退回「次日短打」', 'Sheet 区间扫描'),
        ('', '', '', '', ''),
        ('⚠️ 三条必须知道的事实', '', '', '', ''),
        ('① 资金 5 亿和 10 亿在这份数据里完全等价',
         '在 K10~45 子集里 Δ≥0 / 5 / 8 / 10 / 15 / 20 / 30 亿筛出的全是同一批 6 个样本',
         '原因：真实的日改善量都在 100 亿级，5 亿门槛远低于实际分布',
         '所以「5~10 亿」怎么选都不影响历史表现', 'Sheet 资金阈值扫描'),
        ('② 放宽后拖累来自两头',
         'K10~20（10 天）5 日 -0.54%；K40~45（6 天）5 日 -0.80%、胜率 33.3%',
         '只有 K40~45 是负超额（-0.24%），是所有分档里最差的一段',
         '真想优化，与其放宽到 45，不如停在 40', 'Sheet 区间拆解'),
        ('③ 盘中触发天生弱于收盘确认',
         '盘中「曾触及低位」43 天次日胜 58.1%（无超额）；收盘停在低位才有超额',
         '本信号是盘中实时触发',
         '按「提醒」看，别按「承诺」看', '见脚本头部口径说明'),
        ('', '', '', '', ''),
        ('🚀 落地决定（已执行）', '', '', '', ''),
        ('KDJ_RANGE_LO / HI', '20.0 / 40.0', '10.0 / 45.0', '按用户要求放宽', 'index_fund_flow_watch.py'),
        ('MIN_RECALL_YI / STRONG', '10.0 / 20.0', '5.0 / 10.0', '5 亿触发、10 亿强档两档', 'index_fund_flow_watch.py'),
        ('文案基调', '偏结论式', '改为「提醒」式', '明确写着不是买入指令，由你判断', 'build_message()'),
        ('MAX_DAILY_PUSH', 2, 2, '频率仍远低于 Server酱 5 条/日额度，无需收紧', 'index_fund_flow_watch.py'),
    ]
    r = 5
    for row in rows:
        for i, v in enumerate(row, start=1):
            c = ws.cell(row=r, column=i, value=v)
            c.alignment = Alignment(vertical='top', wrap_text=True)
            if row[0].startswith(('✅', '⚠️', '🚀')) and i == 1:
                c.font = Font(bold=True, size=11)
                c.fill = WARN_FILL
        if row[0] in ('对比项', 'KDJ_RANGE_LO / HI'):
            for i in range(1, 6):
                ws.cell(row=r, column=i).font = Font(bold=True)
        r += 1
    ws.freeze_panes = 'A6'

    # ---------------- Sheet 2：区间扫描 ----------------
    ws = wb.create_sheet('区间扫描')
    widths(ws, [26, 8, 12, 11, 10, 12, 11, 10, 14, 11])
    ws['A1'] = f'收盘 30 分 K 区间平移扫描（{N} 天，基准见「结论与建议」）'
    ws['A1'].font = TITLE_FONT
    put_header(ws, 3, ['区间组合', '样本', '次日均值%', '次日胜率%', '次日均超额',
                       '3日均值%', '3日胜率%', '5日均值%', '5日胜率%', '5日均超额'])

    def add(label, s, warn=False):
        n1, a1, w1, _, _ = stats(s, 'r1')
        n3, a3, w3, _, _ = stats(s, 'r3')
        n5, a5, w5, _, _ = stats(s, 'r5')
        if n1 == 0:
            return 0
        e1 = a1 - base['r1'][1]
        e5 = (a5 - base['r5'][1]) if a5 is not None else None
        vals = [label, n1, pct(a1), round(w1, 1), round(e1, 2),
                pct(a3), round(w3, 1) if a3 is not None else None,
                pct(a5), round(w5, 1) if a5 is not None else None,
                round(e5, 2) if e5 is not None else None]
        ws.append(vals)
        rr = ws.max_row
        if warn:
            for i in range(1, 11):
                ws.cell(row=rr, column=i).fill = WARN_FILL
        elif w5 is not None and w5 > base['r5'][2] and a5 > 0:
            ws.cell(row=rr, column=1).fill = GOOD_FILL
        elif a5 is not None and a5 < 0:
            ws.cell(row=rr, column=9).fill = BAD_FILL
        return rr

    add('基准（全样本）', days)
    ws.cell(row=ws.max_row, column=1).font = Font(bold=True)
    for label, lo, hi in [('K 20~40（现用）', 20, 40), ('K 10~45（用户新提）★', 10, 45),
                          ('K 10~40', 10, 40), ('K 15~45', 15, 45), ('K 15~40', 15, 40),
                          ('K 10~50', 10, 50), ('K 20~45', 20, 45), ('K 30~45', 30, 45),
                          ('K 30~40（原 v2）', 30, 40)]:
        add(label, [d for d in days if lo <= d['k_close'] < hi])
    r = ws.max_row + 2
    ws.cell(row=r, column=1, value='区间拆解：K10~45 由哪些部分组成').font = TITLE_FONT
    put_header(ws, r + 1, ['分段', '样本', '次日均值%', '次日胜率%', '次日均超额',
                           '3日均值%', '3日胜率%', '5日均值%', '5日胜率%', '5日均超额'])
    for label, lo, hi in [('K 10~20（深度超卖）', 10, 20), ('K 20~30', 20, 30),
                          ('K 30~40', 30, 40), ('K 40~45 ★唯一负超额', 40, 45)]:
        add(label, [d for d in days if lo <= d['k_close'] < hi], warn=(hi == 45 and lo == 40))
    add('合计 K 10~45', [d for d in days if 10 <= d['k_close'] < 45])

    # ---------------- Sheet 3：资金阈值扫描 ----------------
    ws = wb.create_sheet('资金阈值扫描')
    widths(ws, [30, 10, 12, 11, 10, 12, 11, 10, 14, 11])
    ws['A1'] = '区间内资金阈值扫描 —— Δ = 当日主力净 − 前一日主力净（日线代理口径）'
    ws['A1'].font = TITLE_FONT
    ws['A2'] = ('⚠️ 分钟级资金流无历史，「日内回补」只能用日线代理；'
                '日线量级约为盘中的 12 倍，绝对额不可跨尺度直接套用，本表只做同口径相对比较')
    ws['A2'].font = NOTE_FONT
    put_header(ws, 4, ['组合', '样本', '次日均值%', '次日胜率%', '次日均超额',
                       '3日均值%', '3日胜率%', '5日均值%', '5日胜率%', '5日均超额'])
    inr = [d for d in days if 10 <= d['k_close'] < 45]
    add('K10~45 不加资金条件', inr)
    for th in (0, 5, 8, 10, 15, 20, 30):
        add(f'K10~45 ∧ Δ≥{th}亿',
            [d for d in inr if d['delta'] is not None and d['delta'] >= th])
    add('K10~45 ∧ Δ>0（资金转正）',
        [d for d in inr if d['delta'] is not None and d['delta'] > 0])
    r = ws.max_row + 2
    ws.cell(row=r, column=1, value='结论：以上 8 行筛出的都是同一批样本 → 5 亿与 10 亿无区分度').font = Font(bold=True, color='C00000')

    r += 3
    ws.cell(row=r, column=1, value='触发频率与额度核算').font = TITLE_FONT
    put_header(ws, r + 1, ['组合', '触发天数', '占交易日%', '约间隔(交易日)', '有效样本', '次日胜率%'])
    for label, lo, hi, th in [('K20~40 ∧ Δ≥10亿（现用）', 20, 40, 10),
                              ('K10~45 ∧ Δ≥5亿（新方案）', 10, 45, 5),
                              ('K10~45 ∧ Δ≥10亿', 10, 45, 10),
                              ('K10~45 ∧ Δ≥0亿', 10, 45, 0)]:
        s = [d for d in days if lo <= d['k_close'] < hi
             and d['delta'] is not None and d['delta'] >= th]
        n1, a1, w1, _, _ = stats(s, 'r1')
        ws.append([label, len(s), round(len(s) / N * 100, 1),
                   round(N / max(len(s), 1), 1), n1, round(w1, 1)])

    # ---------------- Sheet 4：逐笔明细 ----------------
    ws = wb.create_sheet('逐笔明细')
    widths(ws, [13, 10, 10, 12, 12, 10, 10, 10, 10])
    ws['A1'] = 'K10~45 ∧ Δ≥5亿 全部触发样本（6 笔）'
    ws['A1'].font = TITLE_FONT
    ws['A2'] = '次日 5 涨 1 跌，但 3 日已普遍回落 → 验证了「只做次日短打」的必要性'
    ws['A2'].font = NOTE_FONT
    put_header(ws, 4, ['日期', 'K收盘', 'D收盘', 'Δ(亿)', '主力净(亿)',
                       '次日%', '3日%', '5日%', '次日方向'])
    s = sorted([d for d in inr if d['delta'] is not None and d['delta'] >= 5],
               key=lambda x: x['date'])
    for d in s:
        ws.append([d['date'], round(d['k_close'], 1), round(d['d_close'], 1),
                   round(d['delta'], 2), round(d['net'], 2),
                   pct(d['r1']), pct(d['r3']), pct(d['r5']),
                   ('涨' if d['r1'] > 0 else '跌') if d['r1'] is not None else '—'])
        rr = ws.max_row
        if d['r1'] is not None:
            ws.cell(row=rr, column=6).fill = GOOD_FILL if d['r1'] > 0 else BAD_FILL
    ws.append([])
    ws.append(['合计 6 笔', '', '', '', '',
               pct(sum(x['r1'] for x in s if x['r1'] is not None) /
                   len([x for x in s if x['r1'] is not None])),
               pct(sum(x['r3'] for x in s if x['r3'] is not None) /
                   len([x for x in s if x['r3'] is not None])),
               pct(sum(x['r5'] for x in s if x['r5'] is not None) /
                   len([x for x in s if x['r5'] is not None])), ''])
    ws.cell(row=ws.max_row, column=1).font = Font(bold=True)

    wb.save(OUT)
    print(f'✅ 已生成 {OUT}')
    print(f'   Sheet: {wb.sheetnames}')


if __name__ == '__main__':
    main()
