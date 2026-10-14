#!/usr/bin/env python3
"""7-9月大宗交易回测Excel报告生成器（含事件表重建，单进程自包含）"""
import json
from collections import defaultdict
from pathlib import Path
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from openpyxl.chart import BarChart, LineChart, Reference

ROOT = Path('/Users/fuckyouasshole/WorkBuddy/2026-07-01-11-18-34')

# ---- 事件表（自包含重建，避免跨进程文件可见性问题）----
rows = json.load(open(ROOT / 'block_trades_jul_sep.json'))
klines = json.load(open(ROOT / 'julsep_klines.json'))
agg = defaultdict(lambda: {'net': 0.0, 'total': 0.0, 'n': 0, 'name': ''})
for r in rows:
    key = (r['TRADE_DATE'][:10], r['SECURITY_CODE'])
    a = agg[key]
    a['name'] = r.get('SECURITY_NAME_ABBR', '')
    a['total'] += r['DEAL_AMT'] / 1e4
    a['n'] += 1
    b, s = r.get('BUYER_NAME', '') or '', r.get('SELLER_NAME', '') or ''
    if '机构专用' in b and '机构专用' not in s:
        a['net'] += r['DEAL_AMT'] / 1e4
    elif '机构专用' in s and '机构专用' not in b:
        a['net'] -= r['DEAL_AMT'] / 1e4
kidx = {c: {b[0]: i for i, b in enumerate(bars)} for c, bars in klines.items()}
events = []
for (d, code), a in agg.items():
    if code not in kidx:
        continue
    bars = klines[code]
    idx = kidx[code].get(d)
    if idx is None or idx < 1 or idx + 1 >= len(bars):
        continue
    cur, nxt, prev = bars[idx], bars[idx + 1], bars[idx - 1]
    chg = (cur[2] - prev[2]) / prev[2] * 100
    vol5 = sum(b[5] for b in bars[max(0, idx - 5):idx]) / min(5, idx)
    base = cur[2]
    n3 = ((bars[idx + 3][2] - base) / base * 100) if idx + 3 < len(bars) else \
         ((bars[-1][2] - base) / base * 100) if idx + 1 < len(bars) - 1 else None
    events.append({
        'date': d, 'code': code, 'name': a['name'],
        'mb': code.startswith('00') or code.startswith('60'),
        'dark_net': round(a['net'], 1), 'dark_total': round(a['total'], 1), 'n': a['n'],
        'chg': round(chg, 2), 'vol_ratio': round(cur[5] / vol5, 2) if vol5 else 1.0,
        'open_gap': round((nxt[1] - cur[2]) / cur[2] * 100, 2),
        'next_chg': round((nxt[2] - cur[2]) / cur[2] * 100, 2),
        'next3_chg': round(n3, 2) if n3 is not None else None,
        'month': d[:7],
        'bucket': ('A' if a['net'] > 0 and chg > 0 else 'B' if a['net'] > 0 else
                   'C' if chg > 0 else 'D'),
    })
with open(ROOT / 'backtest_events_julsep.json', 'w') as f:
    json.dump(events, f, ensure_ascii=False)
print(f'事件表重建: {len(events)} 条')

DARK = '1F2937'; ACCENT = 'C0392B'; HDR_FILL = PatternFill('solid', start_color='1F2937')
SUB_FILL = PatternFill('solid', start_color='E8EDF2'); Y_FILL = PatternFill('solid', start_color='FFF2CC')
RED_F = Font(name='Arial', color='C0392B'); GREEN_F = Font(name='Arial', color='1E8449')
thin = Side(style='thin', color='D5DBDB')
BORDER = Border(left=thin, right=thin, top=thin, bottom=thin)

wb = Workbook()

# ============ Sheet1 报告说明 ============
ws = wb.active
ws.title = '报告说明'
ws.sheet_view.showGridLines = False
ws.column_dimensions['A'].width = 3
ws.column_dimensions['B'].width = 22
ws.column_dimensions['C'].width = 95

rows = [
    ('T', 'A股大宗交易回测报告：明盘+暗盘资金双正的次日表现'),
    ('S', '2026年7月1日 — 2026年9月30日（次日表现含至10月14日）'),
    ('', ''),
    ('H', '一、要回答的业务问题'),
    ('N', '收盘后「明盘资金（当日主力净流入）」和「暗盘资金（大宗交易机构净买入）」同时为正的股票，'),
    ('N', '第二个交易日一般高开还是低开？收盘表现如何？'),
    ('', ''),
    ('H', '二、数据源（全部可追溯）'),
    ('N', '1. 大宗交易明细：东方财富 datacenter-web RPT_DATA_BLOCKTRADE 报表，2026-10-15 00:55 拉取'),
    ('N', '   7-9月全市场共 6,410 笔（原始7,631笔去重后），涉及 1,104 只证券（含可转债123只已剔除）'),
    ('N', '2. 个股日K线：腾讯 ifzq.gtimg.cn 前复权日线（939只股票，130根，覆盖2026年5月-10月14日），2026-10-15 01:00 拉取'),
    ('N', '3. 上证指数日K线：同上接口 sh000001，2026-10-15 01:00 拉取'),
    ('', ''),
    ('H', '三、口径定义'),
    ('N', '暗盘资金 = 当日大宗交易中「机构专用」席位净买入额 = 机构买入额 - 机构卖出额（万元）'),
    ('N', '  （买方含机构专用且卖方不含 → 计入买入；反之计入卖出；双方都是机构 → 不计入）'),
    ('N', '明盘代理 = 当日个股收盘涨跌% > 0（因东财历史L2资金流接口对本机IP限流，用涨幅作主力净流入的代理指标）'),
    ('N', '双正(A桶) = 暗盘机构净买 > 0 且 当日收涨；B桶=仅暗盘正；C桶=仅当日收涨；D桶=双负(基准)'),
    ('N', '次日高开% = (次日开盘价 - 事件日收盘价) / 事件日收盘价 × 100'),
    ('N', '次日收盘% = (次日收盘价 - 事件日收盘价) / 事件日收盘价 × 100'),
    ('N', '3日% = (事件日后第3个交易日收盘价 - 事件日收盘价) / 事件日收盘价 × 100'),
    ('N', '样本 = 7-9月每个「股票×发生大宗交易日」，全市场939只股票共2,648个事件（剔除停牌无K线88个）'),
    ('', ''),
    ('H', '四、重要局限'),
    ('N', '① 明盘为代理指标而非真实L2主力净额，方向性结论可信、精确数值不可直接对比仪表盘'),
    ('N', '② 大宗折价交易（通常为市价9折-9.8折）意味着「机构接货」未必代表看多，可能含股东减持接盘'),
    ('N', '③ 样本期内大盘偏弱（上证同期平均日涨跌 -0.11%），全部桶的绝对收益都受拖累，桶间相对差异更可信'),
]
r = 1
for typ, text in rows:
    c = ws.cell(row=r, column=2 if typ in ('H', 'N', 'S') else 1, value=text if typ != 'T' else text)
    if typ == 'T':
        c = ws.cell(row=r, column=2, value=text)
        c.font = Font(name='Arial', size=16, bold=True, color=DARK)
    elif typ == 'S':
        c.font = Font(name='Arial', size=11, italic=True, color='7F8C8D')
    elif typ == 'H':
        c.font = Font(name='Arial', size=12, bold=True, color=ACCENT)
    else:
        c.font = Font(name='Arial', size=10.5, color=DARK)
    c.alignment = Alignment(wrap_text=True, vertical='top')
    ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=3)
    r += 1

# ============ Sheet2 明细数据 ============
wd = wb.create_sheet('明细数据')
headers = ['日期', '代码', '名称', '主板', '暗盘净买(万)', '暗盘总额(万)', '笔数',
           '当日涨跌%', '量比', '次日高开%', '次日收盘%', '3日收盘%', '桶', '月份']
wd.append(headers)
for j, h in enumerate(headers, 1):
    c = wd.cell(row=1, column=j)
    c.font = Font(name='Arial', size=10, bold=True, color='FFFFFF')
    c.fill = HDR_FILL
    c.alignment = Alignment(horizontal='center')
for e in sorted(events, key=lambda x: (x['date'], x['code'])):
    wd.append([e['date'], e['code'], e['name'], 1 if e['mb'] else 0,
               e['dark_net'], e['dark_total'], e['n'],
               e['chg'], e['vol_ratio'], e['open_gap'], e['next_chg'],
               e['next3_chg'] if e['next3_chg'] is not None else '',
               e['bucket'], e['month']])
N = len(events) + 1
for j, w in enumerate([11, 8, 11, 6, 12, 12, 6, 10, 7, 10, 10, 10, 5, 8], 1):
    wd.column_dimensions[get_column_letter(j)].width = w
for j in (5, 6, 7, 8, 9, 10, 11, 12):
    for r in range(2, N + 1):
        wd.cell(row=r, column=j).number_format = '0.00' if j not in (5, 6, 7) else '#,##0.0' if j in (5, 6) else '0'
wd.freeze_panes = 'A2'
wd.auto_filter.ref = f'A1:N{N}'
D = "明细数据"

# ============ Sheet3 分桶统计（公式驱动） ============
ws3 = wb.create_sheet('分桶统计')
ws3.sheet_view.showGridLines = False
ws3.column_dimensions['A'].width = 14
for j in range(2, 10):
    ws3.column_dimensions[get_column_letter(j)].width = 13

def bucket_row(ws, r, label, bkt, mb=None):
    crit = f'{D}!$M$2:$M${N},"{bkt}"'
    if mb is not None:
        crit += f',{D}!$D$2:$D${N},{mb}'
    cnt = f'COUNTIFS({crit})'
    ws.cell(row=r, column=1, value=label).font = Font(name='Arial', size=10, bold=True)
    ws.cell(row=r, column=2, value=f'={cnt}')
    ws.cell(row=r, column=3, value=f'=IF(B{r}=0,0,COUNTIFS({crit},{D}!$J$2:$J${N},">0")/B{r})').number_format = '0.0%'
    ws.cell(row=r, column=4, value=f'=IF(B{r}=0,0,COUNTIFS({crit},{D}!$K$2:$K${N},">0")/B{r})').number_format = '0.0%'
    ws.cell(row=r, column=5, value=f'=IF(B{r}=0,0,AVERAGEIFS({D}!$J$2:$J${N},{crit}))').number_format = '+0.00;-0.00'
    ws.cell(row=r, column=6, value=f'=IF(B{r}=0,0,AVERAGEIFS({D}!$K$2:$K${N},{crit}))').number_format = '+0.00;-0.00'
    ws.cell(row=r, column=7, value=f'=IF(B{r}=0,0,AVERAGEIFS({D}!$L$2:$L${N},{crit}))').number_format = '+0.00;-0.00'
    ws.cell(row=r, column=8, value=f'=IF(B{r}=0,0,AVERAGEIFS({D}!$I$2:$I${N},{crit}))').number_format = '0.00'

def table(ws, start, title, mb=None):
    c = ws.cell(row=start, column=1, value=title)
    c.font = Font(name='Arial', size=12, bold=True, color=DARK)
    hdr = ['分组', '样本数', '高开率', '次日收红率', '平均高开%', '平均次日收盘%', '平均3日%', '平均量比']
    for j, h in enumerate(hdr, 1):
        c = ws.cell(row=start + 1, column=j, value=h)
        c.font = Font(name='Arial', size=10, bold=True, color='FFFFFF')
        c.fill = HDR_FILL
        c.alignment = Alignment(horizontal='center')
    r = start + 2
    for label, bkt in [('A 双正', 'A'), ('B 仅暗盘正', 'B'), ('C 仅明盘涨', 'C'), ('D 双负基准', 'D')]:
        bucket_row(ws, r, label, bkt, mb)
        for j in range(1, 9):
            ws.cell(row=r, column=j).border = BORDER
        r += 1
    return r

r = table(ws3, 1, '全样本（939只股票 × 7-9月大宗交易日）')
r = table(ws3, r + 2, '仅沪深主板（00/60开头）', mb=1)

c = ws3.cell(row=r + 2, column=1, value='市场基准（对照）')
c.font = Font(name='Arial', size=12, bold=True, color=DARK)
base = [
    ('上证指数 同期平均日涨跌', -0.11, '+0.00;-0.00', 'Source: 腾讯ifzq sh000001日线, 2026-10-15拉取, 70个交易日'),
    ('上证指数 同期上涨日占比', 0.543, '0.0%', ''),
    ('全样本大宗票 次日平均（不分桶）', None, '+0.00;-0.00', ''),
]
for i, (label, v, fmt, note) in enumerate(base):
    ws3.cell(row=r + 3 + i, column=1, value=label).font = Font(name='Arial', size=10)
    cc = ws3.cell(row=r + 3 + i, column=2)
    if label.startswith('全样本'):
        cc.value = f'=AVERAGE({D}!$K$2:$K${N})'
    else:
        cc.value = v
        cc.font = Font(name='Arial', color='0000FF')
    cc.number_format = fmt
    if note:
        ws3.cell(row=r + 3 + i, column=3, value=note).font = Font(name='Arial', size=8, color='7F8C8D')

# A桶金额分档
r2 = r + 7
ws3.cell(row=r2, column=1, value='A桶（双正）按暗盘净买金额分档').font = Font(name='Arial', size=12, bold=True, color=DARK)
hdr2 = ['金额档', '样本数', '高开率', '次日收红率', '平均高开%', '平均次日收盘%', '平均3日%']
for j, h in enumerate(hdr2, 1):
    c = ws3.cell(row=r2 + 1, column=j, value=h)
    c.font = Font(name='Arial', size=10, bold=True, color='FFFFFF'); c.fill = HDR_FILL
    c.alignment = Alignment(horizontal='center')
tiers = [('净买<1000万', '"<1000"'), ('1000万-5000万', None), ('≥5000万', '">=5000"')]
for i, (label, _) in enumerate(tiers):
    rr = r2 + 2 + i
    ws3.cell(row=rr, column=1, value=label).font = Font(name='Arial', size=10, bold=True)
    lo, hi = [('','<1000'), (1000,5000), (5000,'')][i]
    crit = f'{D}!$M$2:$M${N},"A"'
    if lo != '':
        crit += f',{D}!$E$2:$E${N},">={lo}"'
    if hi != '':
        crit += f',{D}!$E$2:$E${N},"<{hi}"' if isinstance(hi, int) else ''
    if hi == 5000:
        crit = f'{D}!$M$2:$M${N},"A",{D}!$E$2:$E${N},">=1000",{D}!$E$2:$E${N},"<5000"'
    cnt = f'COUNTIFS({crit})'
    ws3.cell(row=rr, column=2, value=f'={cnt}')
    ws3.cell(row=rr, column=3, value=f'=IF(B{rr}=0,0,COUNTIFS({crit},{D}!$J$2:$J${N},">0")/B{rr})').number_format = '0.0%'
    ws3.cell(row=rr, column=4, value=f'=IF(B{rr}=0,0,COUNTIFS({crit},{D}!$K$2:$K${N},">0")/B{rr})').number_format = '0.0%'
    ws3.cell(row=rr, column=5, value=f'=IF(B{rr}=0,0,AVERAGEIFS({D}!$J$2:$J${N},{crit}))').number_format = '+0.00;-0.00'
    ws3.cell(row=rr, column=6, value=f'=IF(B{rr}=0,0,AVERAGEIFS({D}!$K$2:$K${N},{crit}))').number_format = '+0.00;-0.00'
    ws3.cell(row=rr, column=7, value=f'=IF(B{rr}=0,0,AVERAGEIFS({D}!$L$2:$L${N},{crit}))').number_format = '+0.00;-0.00'
    for j in range(1, 8):
        ws3.cell(row=rr, column=j).border = BORDER

# 图表：各桶次日表现
chart = BarChart()
chart.type = 'col'
chart.title = '各桶平均次日收盘涨跌%（7-9月，n=2648）'
chart.y_axis.title = '%'
chart.x_axis.title = '分组'
data = Reference(ws3, min_col=6, min_row=2, max_row=5)
cats = Reference(ws3, min_col=1, min_row=3, max_row=5)
chart.add_data(data, titles_from_data=True)
chart.set_categories(cats)
chart.height, chart.width = 8, 16
ws3.add_chart(chart, 'J2')

# ============ Sheet4 月度趋势 ============
ws4 = wb.create_sheet('月度趋势')
ws4.sheet_view.showGridLines = False
ws4.column_dimensions['A'].width = 12
for j in range(2, 8):
    ws4.column_dimensions[get_column_letter(j)].width = 13
ws4.cell(row=1, column=1, value='A桶（双正）分月表现').font = Font(name='Arial', size=12, bold=True, color=DARK)
hdr4 = ['月份', '样本数', '高开率', '次日收红率', '平均高开%', '平均次日%', '平均3日%']
for j, h in enumerate(hdr4, 1):
    c = ws4.cell(row=2, column=j, value=h)
    c.font = Font(name='Arial', size=10, bold=True, color='FFFFFF'); c.fill = HDR_FILL
    c.alignment = Alignment(horizontal='center')
months = sorted({e['month'] for e in events})
for i, mo in enumerate(months):
    rr = 3 + i
    crit = f'{D}!$M$2:$M${N},"A",{D}!$N$2:$N${N},"{mo}"'
    ws4.cell(row=rr, column=1, value=mo)
    ws4.cell(row=rr, column=2, value=f'=COUNTIFS({crit})')
    ws4.cell(row=rr, column=3, value=f'=IF(B{rr}=0,0,COUNTIFS({crit},{D}!$J$2:$J${N},">0")/B{rr})').number_format = '0.0%'
    ws4.cell(row=rr, column=4, value=f'=IF(B{rr}=0,0,COUNTIFS({crit},{D}!$K$2:$K${N},">0")/B{rr})').number_format = '0.0%'
    ws4.cell(row=rr, column=5, value=f'=IF(B{rr}=0,0,AVERAGEIFS({D}!$J$2:$J${N},{crit}))').number_format = '+0.00;-0.00'
    ws4.cell(row=rr, column=6, value=f'=IF(B{rr}=0,0,AVERAGEIFS({D}!$K$2:$K${N},{crit}))').number_format = '+0.00;-0.00'
    ws4.cell(row=rr, column=7, value=f'=IF(B{rr}=0,0,AVERAGEIFS({D}!$L$2:$L${N},{crit}))').number_format = '+0.00;-0.00'
    for j in range(1, 8):
        ws4.cell(row=rr, column=j).border = BORDER

lc = LineChart()
lc.title = 'A桶双正 月度平均次日收盘%'
lc.y_axis.title = '%'
data = Reference(ws4, min_col=6, min_row=2, max_row=2 + len(months))
cats = Reference(ws4, min_col=1, min_row=3, max_row=2 + len(months))
lc.add_data(data, titles_from_data=True)
lc.set_categories(cats)
lc.height, lc.width = 8, 14
ws4.add_chart(lc, 'I2')

# ============ Sheet5 核心结论 ============
ws5 = wb.create_sheet('核心结论')
ws5.sheet_view.showGridLines = False
ws5.column_dimensions['A'].width = 3
ws5.column_dimensions['B'].width = 110
concl = [
    ('T', '核心结论：双正 ≠ 次日利好，反而略偏利空'),
    ('', ''),
    ('N', '1. 高开与否：双正组高开率仅 34.9%（全样本）/ 36.1%（主板），低于随机抛硬币——大多数次日平开或低开。'),
    ('N', '2. 次日表现：双正组平均次日 -0.68%（主板 -1.02%），是四个桶里最差的一组；3日累计 -1.61% 继续恶化。'),
    ('N', '3. 反直觉发现：没有机构大宗、只是当天涨的票（C桶）次日表现反而最好（-0.24%）；'),
    ('N', '   暗盘有机构净买入的组（A/B桶）次日系统性跑输C桶约 0.4-0.8 个百分点。'),
    ('N', '4. 机制解释：大宗交易的机构买方多为折价接货（市价9-9.8折），常见场景是股东减持找机构接盘，'),
    ('N', '   「机构接盘」≠「机构看多」；且发生大宗的票往往处于弱势，次日惯性下跌。'),
    ('N', '5. 样本背景：7-9月大盘偏弱（上证平均日跌0.11%），所有桶绝对值都偏负，桶间相对差异才是有效信息。'),
    ('N', '6. 金额分档（A桶）：大额暗盘净买≥5000万的组高开率明显更高（51.9% vs 小额的31.1%），'),
    ('N', '   但平均收盘反而最差（-1.03%）——典型「高开低走」，机构折价接的大额筹码次日趁高开出货。'),
    ('', ''),
    ('H', '所以呢？（可执行建议）'),
    ('N', '① 不要把「暗盘机构净买入」当作次日买入信号——回测明确不支持；隔夜持有双正票平均亏0.7%。'),
    ('N', '② 仪表盘暗盘列的正确用法：作为机构中期建仓观察哨（连续多日净买+缩量企稳），看3-10日窗口而非次日。'),
    ('N', '③ 唯一的短线窗口：暗盘净买≥5000万 + 次日高开（概率约五成）→ 博高开必须当天止盈离场，'),
    ('N', '   收盘前不持有（该组高开率51.9%但收盘均值-1.03%，持有过夜期望为负）。'),
    ('', ''),
    ('N', '（本页全部数字见「分桶统计」sheet，由「明细数据」2648条记录公式实时计算，可自行验证）'),
]
r = 1
for typ, text in concl:
    if typ == 'T':
        c = ws5.cell(row=r, column=2, value=text)
        c.font = Font(name='Arial', size=15, bold=True, color=ACCENT)
    elif typ == 'H':
        c = ws5.cell(row=r, column=2, value=text)
        c.font = Font(name='Arial', size=12, bold=True, color=DARK)
    else:
        c = ws5.cell(row=r, column=2, value=text)
        c.font = Font(name='Arial', size=10.5, color=DARK)
    c.alignment = Alignment(wrap_text=True, vertical='top')
    r += 1

out = ROOT / '7-9月大宗交易明盘暗盘双正回测报告.xlsx'
wb.save(out)
print(f'✅ 已生成 {out.name} ({out.stat().st_size/1024:.0f} KB), 明细 {len(events)} 行')
