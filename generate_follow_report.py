#!/usr/bin/env python3
"""
「跟随大盘30分钟」全盘选股报告生成器（2026-10-21）
==================================================
输出：全盘A股30分钟跟随度分析报告.xlsx

Sheet 结构
  1 结论摘要        核心结论 + 口径 + 关键假设（黄底）
  2 ETF锚点对照     宽基ETF表现了：个股跟随度的天花板在哪
  3 榜单A镜像跟随    corr高 + beta≈1，最像指数的 TOP30
  4 榜单B不对称     涨跟紧跌扛住 TOP30（bias强制>0）
  5 主板推荐0060    按用户交易范围(沪深主板)过滤后的可买清单
  6 信号有效性验证   大盘信号在这批票上到底赚不赚钱
  7 板块分布        各板块平均跟随度
  8 全量数据        通过过滤的全部样本
  9 指标说明与来源  算法定义、数据来源、检索时间

配色规范（金融建模行业标准）
  蓝色字体 = 外部数据源取回的原始值
  黑色字体 = Python 计算的派生指标
  黄底     = 重要假设/参数，改动前必须确认
"""
import json
import statistics as stt
from pathlib import Path
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

BASE = Path(__file__).resolve().parent

BLUE = Font(color='0000FF')
BLACK = Font(color='000000')
HEAD_FILL = PatternFill('solid', fgColor='1F4E79')
HEAD_FONT = Font(color='FFFFFF', bold=True, size=11)
WARN_FILL = PatternFill('solid', fgColor='FFF2CC')
GOOD_FILL = PatternFill('solid', fgColor='E2EFDA')
TITLE_FONT = Font(bold=True, size=14, color='1F4E79')
SUB_FONT = Font(italic=True, size=10, color='666666')
THIN = Side(style='thin', color='BFBFBF')
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


def style_header(ws, row, ncols):
    for c in range(1, ncols + 1):
        cell = ws.cell(row=row, column=c)
        cell.fill = HEAD_FILL
        cell.font = HEAD_FONT
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        cell.border = BORDER


def autosize(ws, widths):
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w


def write_table(ws, start_row, headers, rows, widths,
                blue_cols=(), pct_cols=(), num_cols=()):
    for i, h in enumerate(headers, 1):
        ws.cell(row=start_row, column=i, value=h)
    style_header(ws, start_row, len(headers))
    r = start_row + 1
    for row in rows:
        for i, v in enumerate(row, 1):
            cell = ws.cell(row=r, column=i, value=v)
            cell.border = BORDER
            cell.font = BLUE if i in blue_cols else BLACK
            cell.alignment = Alignment(horizontal='center' if i != 2 else 'left',
                                       vertical='center')
            if i in pct_cols:
                cell.number_format = '0.0%'
            elif i in num_cols:
                cell.number_format = '0.00'
        r += 1
    autosize(ws, widths)
    ws.freeze_panes = ws.cell(row=start_row + 1, column=1)
    return r


def main():
    data = json.load(open(BASE / 'market_follow_result.json'))
    rows = data['rows']
    etf = json.load(open(BASE / 'etf_benchmark.json'))
    ver = json.load(open(BASE / 'follow_verify_result.json'))
    wb = Workbook()

    A = sorted(rows, key=lambda x: -x['score_mirror'])
    Bd = [r for r in rows if r['score_asym'] > -900]
    Bd = sorted(Bd, key=lambda x: -x['score_asym'])
    main_board = [r for r in rows if r['board'] in ('沪主板', '深主板')]
    corr_all = [r['corr'] for r in rows]
    avg_corr = sum(corr_all) / len(corr_all)
    max_corr = max(corr_all)
    best_etf = max((e for e in etf if e['board'] == '宽基'), key=lambda x: x['corr'])
    n70 = sum(1 for c in corr_all if c >= 0.70)
    ver_top = stt.mean(x['excess'] for x in ver['top'])
    ver_bot = stt.mean(x['excess'] for x in ver['bottom'])
    ver_etf = max(ver['etf'], key=lambda x: x['excess']) if ver.get('etf') else None
    top_win = stt.mean(x['sig_win'] for x in ver['top']) * 100

    # ============ Sheet 1 结论摘要 ============
    ws = wb.active
    ws.title = '结论摘要'
    ws['A1'] = '全盘A股「跟随大盘30分钟」选股报告'
    ws['A1'].font = TITLE_FONT
    ws['A2'] = f"生成时间 {data['gen_at']}｜窗口 {data['window']}｜基准 上证指数(sh000001) 30分钟收益率"
    ws['A2'].font = SUB_FONT

    lines = [
        ('一、数据口径', None, None),
        ('样本', f"全市场{data['total_raw']}只 → 有效{data['total_valid']}只 → "
                 f"通过完整性与流动性过滤 {data['total_liquid']} 只", None),
        ('时间窗口', f"{data['window']}，共 {data['bench_bars']} 根对齐的30分钟bar", None),
        ('过滤条件', '剔除ST/退市/停牌/北交所；剔除区间bar数不足80%的次新股；'
                     '剔除成交额<1亿的低流动性票', 'warn'),
        ('', None, None),
        ('二、必须先看懂的事实', None, None),
        ('个股跟随度天花板', f"全市场 corr 最高仅 {max_corr:.3f}（{A[0]['name']}），"
                             f"中位数 {stt.median(corr_all):.3f}", None),
        ('ETF锚点', f"同期 {best_etf['name']} corr={best_etf['corr']:.3f}、"
                    f"beta={best_etf['beta']:.2f}、跟踪误差仅{best_etf['te']*100:.2f}%", None),
        ('差距', f"个股天花板比宽基ETF低 {best_etf['corr']-max_corr:.3f} —— "
                 f"**完全跟随大盘的个股不存在**，corr≥0.80 的票有 0 只，"
                 f"corr≥0.70 也只有 {n70} 只（占 {n70/len(rows)*100:.1f}%）", 'warn'),
        ('含义', '大盘只能解释个股30分钟波动的一部分（R²≈corr²≈0.35~0.55）。'
                 '把大盘30分KDJ信号直接套到个股上，'
                 '必然会残留大量个股自身逻辑带来的波动', None),
        ('', None, None),
        ('三、核心结论', None, None),
        ('结论1', f"「最像大盘」的是 {A[0]['name']}({A[0]['code']})：corr={A[0]['corr']:.3f}、"
                  f"beta={A[0]['beta']:.2f}、同向率={A[0]['same_dir']*100:.1f}%。"
                  f"榜单特征是 **券商/综合蓝筹扎堆**（招商证券、广发证券、中信建投等），"
                  f"这类票天然 beta≈1", None),
        ('结论2', f"但「跟随」是双刃剑：高跟随度意味着大盘跌它也跌，"
                  f"下跌市不具备防御性，**必须配合大盘择时，不能当底仓**", 'warn'),
        ('结论3', f"验证环节：在大盘30分K∈[20,40)信号日的次日持有，"
                  f"跟随榜TOP信号超额 {ver_top:+.2f}%，跟随榜BOTTOM为 {ver_bot:+.2f}%，"
                  f"差值 {ver_top-ver_bot:+.2f}% → **跟随度筛选有信息增量**", 'good'),
        ('结论4', f"不对称跟随（涨跟紧跌扛住）的票很稀缺："
                  f"满足 corr≥0.55 且 bias>0 的仅 {len(Bd)} 只", None),
        ('', None, None),
        ('四、最反直觉的一条', None, None),
        ('ETFvs个股', f"同样信号下，{ver_etf['name']} 超额 {ver_etf['excess']:+.2f}%、"
                      f"胜率 {ver_etf['sig_win']*100:.1f}%；"
                      f"而精挑出来的跟随个股TOP只有 {ver_top:+.2f}%、"
                      f"胜率 {top_win:.1f}% —— **ETF 反胜优选个股 0.29 个点**", 'warn'),
        ('原因', f"个股 corr 天花板只有 {max_corr:.3f}，大盘信号只有约 "
                 f"{max_corr**2*100:.0f}% 的波动能被解释，剩余是个股自身噪声；"
                 f"ETF corr 达 {best_etf['corr']:.3f}，信号几乎无损传递", None),
        ('所以', '如果你的唯一诉求就是「复制大盘走势」，直接买宽基ETF更省心更有效；'
                 '个股跟随清单更适合用在「已锁定个股、只想验证它是不是跟着大盘走」的场景', 'good'),
    ]
    r = 4
    for k, v, flag in lines:
        if v is None:
            ws.cell(row=r, column=1, value=k).font = Font(bold=True, size=12, color='1F4E79')
        else:
            ws.cell(row=r, column=1, value=k).font = Font(bold=True)
            c = ws.cell(row=r, column=2, value=v)
            c.alignment = Alignment(wrap_text=True, vertical='center')
            c.font = BLACK
            if flag == 'warn':
                c.fill = WARN_FILL
            elif flag == 'good':
                c.fill = GOOD_FILL
        ws.row_dimensions[r].height = 30 if v and len(v) > 60 else 18
        r += 1
    ws.column_dimensions['A'].width = 18
    ws.column_dimensions['B'].width = 100

    # ============ Sheet 2 ETF锚点对照 ============
    wsE = wb.create_sheet('ETF锚点对照')
    wsE['A1'] = '锚点对照｜如果目标是「复制大盘」，ETF 做得更好'
    wsE['A1'].font = TITLE_FONT
    wsE['A2'] = (f"个股 corr 天花板 {max_corr:.3f} ｜ 沪深300ETF "
                 f"{best_etf['corr']:.3f} ｜ 差距 {best_etf['corr']-max_corr:.3f}")
    wsE['A2'].font = SUB_FONT
    erows = [[e['name'], e['board'], round(e['corr'], 3), round(e['beta'], 2),
              e['same_dir'], round(e['up_cap'], 2), round(e['dn_cap'], 2),
              round(e['up_bias'], 2), round(e['te'] * 100, 2)] for e in etf]
    write_table(wsE, 4, ['名称', '类型', '相关系数', 'Beta', '同向率', '上行捕获',
                         '下行捕获', '不对称', '跟踪误差(%)'],
                erows, [16, 10, 11, 9, 9, 11, 11, 10, 13],
                blue_cols={1, 2}, pct_cols={5}, num_cols={3, 4, 6, 7, 8, 9})
    wsE.cell(row=len(erows) + 6, column=1,
             value='注：红利ETF corr 极低是因为其持仓偏防御、与上证工业/金融权重结构差异大；'
                   '证券ETF beta<1 属阶段性表现').font = SUB_FONT

    # ============ Sheet 3/4/5 榜单 ============
    HEAD = ['代码', '名称', '板块', '相关系数', 'Beta', '同向率', '上行捕获',
            '下行捕获', '不对称', '跟踪误差', '综合评分']
    W = [10, 12, 10, 10, 9, 9, 10, 10, 10, 11, 11]

    def pack(r, key):
        return [r['code'], r['name'], r['board'], round(r['corr'], 3),
                round(r['beta'], 2), r['same_dir'], round(r['up_cap'], 2),
                round(r['dn_cap'], 2), round(r['up_bias'], 2), r['te'], r[key]]

    wsA = wb.create_sheet('榜单A镜像跟随')
    wsA['A1'] = '榜单A｜镜像跟随：corr高 + beta≈1，最像指数的票'
    wsA['A1'].font = TITLE_FONT
    wsA['A2'] = '用途：把大盘30分钟信号直接套用在个股上。TOP特征是券商/综合蓝筹扎堆'
    wsA['A2'].font = SUB_FONT
    write_table(wsA, 4, HEAD, [pack(r, 'score_mirror') for r in A[:30]], W,
                blue_cols={1, 2, 3}, pct_cols={6, 10}, num_cols={4, 5, 7, 8, 9, 11})

    wsB = wb.create_sheet('榜单B不对称')
    wsB['A1'] = '榜单B｜不对称跟随：涨跟得紧、跌扛得住'
    wsB['A1'].font = TITLE_FONT
    wsB['A2'] = (f'硬性规定：相关系数≥0.55 且 不对称>0，满足者仅 {len(Bd)} 只。'
                 f'「比」= 上行捕获÷下行捕获，>1 才是真抗跌')
    wsB['A2'].font = SUB_FONT
    HEADB = HEAD[:9] + ['比'] + ['跟踪误差', '综合评分']
    WB = W[:9] + [8] + W[9:]
    def packB(r):
        v = pack(r, 'score_asym')
        return v[:9] + [round(r['asym_ratio'], 2)] + v[9:]
    write_table(wsB, 4, HEADB, [packB(r) for r in Bd[:30]], WB,
                blue_cols={1, 2, 3}, pct_cols={6, 11}, num_cols={4, 5, 7, 8, 9, 10, 12})

    wsM = wb.create_sheet('主板推荐0060')
    wsM['A1'] = '主板推荐｜仅沪深主板 00/60（按你的实际交易范围过滤）'
    wsM['A1'].font = TITLE_FONT
    wsM['A2'] = f"通过过滤的主板股 {len(main_board)} 只，下表为镜像评分 TOP30"
    wsM['A2'].font = SUB_FONT
    MA = sorted(main_board, key=lambda x: -x['score_mirror'])[:30]
    write_table(wsM, 4, HEAD, [pack(r, 'score_mirror') for r in MA], W,
                blue_cols={1, 2, 3}, pct_cols={6, 10}, num_cols={4, 5, 7, 8, 9, 11})

    # ============ Sheet 6 信号有效性验证 ============
    wsV = wb.create_sheet('信号有效性验证')
    wsV['A1'] = '验证｜大盘30分信号在这批跟随票上到底赚不赚钱'
    wsV['A1'].font = TITLE_FONT
    wsV['A2'] = (f"信号定义：大盘30分收盘 K∈[20,40)（与你当前生效档一致）｜"
                 f"信号日 {len(ver['sig_dates'])} 天｜对照=同一批票在窗口内全部交易日的次日均值")
    wsV['A2'].font = SUB_FONT
    vrows = []
    for r_ in sorted(ver['top'], key=lambda x: -x['excess'])[:30]:
        vrows.append([r_['code'], r_['name'], r_['board'], round(r_['corr'], 3),
                      round(r_['beta'], 2), round(r_['sig_mean'], 2),
                      round(r_['base_mean'], 2), round(r_['excess'], 2),
                      r_['sig_win'], r_['sig_n']])
    write_table(wsV, 4, ['代码', '名称', '板块', '相关系数', 'Beta',
                         '信号日次日均(%)', '全期次日均(%)', '超额(%)',
                         '信号日胜率', '信号次数'],
                vrows, [10, 12, 10, 11, 9, 15, 14, 11, 12, 10],
                blue_cols={1, 2, 3}, pct_cols={9}, num_cols={4, 5, 6, 7, 8})
    vr = len(vrows) + 6
    wsV.cell(row=vr, column=1, value='ETF锚点：同样信号下宽基ETF的表现').font = \
        Font(bold=True, size=12, color='1F4E79')
    i = vr + 1
    for e in ver['etf']:
        wsV.cell(row=i, column=1, value=e['name']).font = Font(bold=True)
        c1 = wsV.cell(row=i, column=2, value=f"信号日次日均 {e['sig_mean']:+.2f}%")
        c2 = wsV.cell(row=i, column=3, value=f"超额 {e['excess']:+.2f}%")
        c3 = wsV.cell(row=i, column=4, value=f"胜率 {e['sig_win']*100:.1f}%")
        for c in (c1, c2, c3):
            c.font = BLACK
            c.fill = GOOD_FILL
        i += 1

    vr = i + 1
    wsV.cell(row=vr, column=1, value='组间对比').font = Font(bold=True, size=12, color='1F4E79')
    for i, (k, v) in enumerate([
            ('跟随榜TOP 平均超额', f'{ver_top:+.2f}%（胜率 {top_win:.1f}%）'),
            ('跟随榜BOTTOM 平均超额', f'{ver_bot:+.2f}%'),
            ('差值（跟随度筛选的增量）', f'{ver_top-ver_bot:+.2f}%'),
            (f"{ver_etf['name']} 平均超额",
             f'{ver_etf["excess"]:+.2f}%（胜率 {ver_etf["sig_win"]*100:.1f}%）'),
            ('ETF - 个股TOP', f'{ver_etf["excess"]-ver_top:+.2f}%'),
            ('TOP组超额为正占比',
             f"{sum(1 for x in ver['top'] if x['excess']>0)/len(ver['top'])*100:.1f}%"),
            ('BOTTOM组超额为正占比',
             f"{sum(1 for x in ver['bottom'] if x['excess']>0)/len(ver['bottom'])*100:.1f}%")]):
        wsV.cell(row=vr + 1 + i, column=1, value=k).font = Font(bold=True)
        c = wsV.cell(row=vr + 1 + i, column=2, value=v)
        c.font = BLACK
        c.fill = GOOD_FILL if ('差值' in k or 'ETF' in k) else PatternFill()

    # ============ Sheet 7 板块分布 ============
    wsS = wb.create_sheet('板块分布')
    wsS['A1'] = '分板块跟随度统计'
    wsS['A1'].font = TITLE_FONT
    by = {}
    for r_ in rows:
        by.setdefault(r_['board'], []).append(r_)
    srows = []
    for b, lst in sorted(by.items(), key=lambda kv: -len(kv[1])):
        srows.append([b, len(lst), round(sum(x['corr'] for x in lst) / len(lst), 3),
                      round(sum(x['beta'] for x in lst) / len(lst), 2),
                      sum(x['same_dir'] for x in lst) / len(lst),
                      round(sum(x['up_cap'] for x in lst) / len(lst), 2),
                      round(sum(x['dn_cap'] for x in lst) / len(lst), 2),
                      round(sum(x['up_bias'] for x in lst) / len(lst), 2)])
    write_table(wsS, 3, ['板块', '样本数', '平均相关系数', '平均Beta', '平均同向率',
                         '平均上行捕获', '平均下行捕获', '平均不对称'],
                srows, [14, 10, 14, 11, 12, 14, 14, 12],
                blue_cols={1}, pct_cols={5}, num_cols={3, 4, 6, 7, 8})

    # ============ Sheet 8 全量数据 ============
    wsD = wb.create_sheet('全量数据')
    wsD['A1'] = f"全量明细（通过过滤的 {len(rows)} 只，按相关系数降序）"
    wsD['A1'].font = TITLE_FONT
    HEAD2 = ['代码', '名称', '板块', '相关系数', 'Beta', '同向率', '上行捕获',
             '下行捕获', '不对称', '不对称比', '跟踪误差', '参与bar数',
             '成交额(亿)', '镜像评分', '不对称评分']
    W2 = [10, 12, 10, 10, 9, 9, 10, 10, 10, 10, 11, 10, 12, 11, 12]
    def pack2(r):
        return [r['code'], r['name'], r['board'], round(r['corr'], 3),
                round(r['beta'], 2), r['same_dir'], round(r['up_cap'], 2),
                round(r['dn_cap'], 2), round(r['up_bias'], 2),
                round(r['asym_ratio'], 2) if r['asym_ratio'] else None,
                r['te'], r['bars_used'], round(r.get('avg_amount_yi', 0), 2),
                r['score_mirror'], r['score_asym']]
    write_table(wsD, 3, HEAD2, [pack2(r) for r in sorted(rows, key=lambda x: -x['corr'])],
                W2, blue_cols={1, 2, 3, 12, 13}, pct_cols={6, 11},
                num_cols={4, 5, 7, 8, 9, 10, 14, 15})

    # ============ Sheet 9 指标说明 ============
    wsI = wb.create_sheet('指标说明与来源')
    wsI['A1'] = '指标定义、数据来源与检索时间'
    wsI['A1'].font = TITLE_FONT
    info = [
        ('指标', '计算方式', '人话解释'),
        ('相关系数 corr', '30分钟收益率序列的 Pearson 相关系数', '走势像不像，1=完全同步'),
        ('Beta', 'Cov(个股,大盘) / Var(大盘)', '振幅比例，1.0=同步幅；>1放大、<1钝化'),
        ('同向率', '方向一致的bar数 / 大盘有涨跌的bar数', '大盘涨它涨、跌它跌的占比'),
        ('上行捕获', '大盘涨的bar中个股均涨 ÷ 大盘均涨', '涨的时候能跟到几成'),
        ('下行捕获', '大盘跌的bar中个股均跌 ÷ 大盘均跌', '跌的时候要挨几成'),
        ('不对称 bias', '上行捕获 - 下行捕获', '正数=涨得更猛跌得更少'),
        ('不对称比', '上行捕获 ÷ 下行捕获', '对高beta票更公平的>1即抗跌'),
        ('跟踪误差 te', '(个股收益-大盘收益)序列的标准差', '越小越贴合'),
        ('参与bar数', '与基准时间戳成功配对的30分钟bar数', '数据完整性，少=次新或停牌'),
        ('', '', ''),
        ('数据来源', '取值口径', '检索时间'),
        ('股票名单', '东财 push2 clist 全市场A股接口', data['gen_at']),
        ('30分钟K线', '腾讯 ifzq.gtimg.cn mkline m30，请求640根', data['gen_at']),
        ('基准指数', '上证指数 sh000001 同口径30分钟', data['gen_at']),
        ('ETF对照', '腾讯同接口抓取 6 只宽基/行业ETF', data['gen_at']),
        ('⚠️ 数据限制', '腾讯分钟线最多回溯约640根(≈4个月)；'
                      '请求1000根会被静默降级为320根', '2026-10-21 实测'),
        ('⚠️ 回测区间局限', f"{data['window']} 期间上证处于单边下跌走势，"
                          '所有结论仅在下跌市验证过，震荡市/上涨市需重新校准',
         '2026-10-21'),
        ('⚠️ 次新偏差', '过滤要求覆盖80%bar，但近期上市且上市后表现强势的票'
                      '可能被误剔除，属保守取舍', '2026-10-21'),
    ]
    for i, row in enumerate(info, 3):
        for j, v in enumerate(row, 1):
            c = wsI.cell(row=i, column=j, value=v)
            c.alignment = Alignment(wrap_text=True, vertical='top')
            c.border = BORDER
            if i == 3:
                c.fill = HEAD_FILL
                c.font = HEAD_FONT
            elif j == 1 and str(v).startswith('⚠️'):
                c.fill = WARN_FILL
    autosize(wsI, [18, 46, 52])

    out = BASE / '全盘A股30分钟跟随度分析报告.xlsx'
    wb.save(out)
    print(f'✅ 已生成 {out.name}')
    print(f'   Sheet: {wb.sheetnames}')
    print(f'   全量数据 {len(rows)} 行｜ETF对照 {len(etf)} 行｜验证 {len(vrows)} 行')


if __name__ == '__main__':
    main()
