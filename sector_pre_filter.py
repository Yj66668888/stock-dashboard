#!/usr/bin/env python3
"""
概念板块预筛选模块
==================
策略：先找强势概念板块（涨幅>1.5% + 涨停≥3家/≥1家），
      再从板块内找 5日/10日主力占比高的票。

筛选流程:
1. 全市场股票 → 关键词匹配映射到19个概念板块
2. 计算板块均涨幅和涨停家数（≥9.8%为涨停）
3. 筛选：均涨幅>1.5% AND 涨停≥N（默认3家，宽松模式1家）
4. 计算板块内每只票的5日/10日主力资金占比
5. 输出 sector_filter.json — 符合条件板块 + 个股清单 + 主力占比
"""
import json, os
from datetime import datetime

BASE = os.path.dirname(__file__)
CF_FILE = os.path.join(BASE, 'capital_flow.json')
PRED_FILE = os.path.join(BASE, 'daily_predictions.json')
OUTPUT = os.path.join(BASE, 'sector_filter.json')

# 紧凑模式：涨停门槛降低
LOOSE_MODE = True  # True=≥1家涨停, False=≥3家涨停
MIN_AVG_GAIN = 1.5  # 板块均涨幅最低阈值

# 扩展概念板块关键词（覆盖更多A股命名模式）
SECTOR_KEYWORDS = {
    '化工': ['化工', '化学', '化纤', '颜料', '染料', '涂料', '树脂', '塑料', '橡胶', '纤维',
             '氟化', '磷化', '钛白', '氯碱', '农药', '化肥', '制剂', '硅材', '碳纤维',
             '聚合', '日化', '石化', '盐化', '精细化工'],
    '医药医疗': ['医药', '药业', '制药', '医疗', '诊断', '试剂', '生物', '基因', '疫苗',
                '健康', '中药', '西药', '器械', '耗材', '医美', '药房', '医院', '保健'],
    '半导体电子': ['半导体', '芯片', '集成', '晶圆', '封测', '微电子', '电子', '电路',
                  '存储', '光电', '传感', '显示', 'PCB', '元器件', '二极管', '三极管'],
    '新能源': ['新能源', '光伏', '锂电', '电池', '储能', '充电', '风电', '太阳能',
              '核能', '氢能', '逆变器', '电站', '充电桩', '绿电'],
    '汽车产业链': ['汽车', '新能源车', '零部件', '轮胎', '底盘', '变速', '车灯', '座椅',
                  '刹车', '悬挂', '轮毂', '减震', '传动', '齿轮'],
    '电力设备': ['电力', '电网', '电气', '电缆', '变压器', '开关', '配电', '电工',
                '电机', '继电器', '接触器', '断路器', '高低压'],
    '有色金属': ['矿业', '有色', '金属', '铜业', '铝业', '锂业', '镍业', '钴业',
                '稀土', '钨业', '钛业', '锌业', '锡业', '黄金', '白银', '铂业',
                '矿产', '采矿', '冶炼'],
    '军工航天': ['军工', '国防', '武器', '导弹', '雷达', '卫星', '航天', '航空',
                '战机', '舰船', '装甲', '弹药', '火控', '无人'],
    '食品饮料': ['食品', '饮料', '酒业', '乳业', '调味', '粮油', '糖业', '饮品',
                '零食', '烘焙', '冷冻', '预制', '速冻', '肉制品', '水产'],
    '地产基建': ['地产', '房产', '建筑', '建材', '水泥', '玻璃', '钢结构',
                '装修', '工程', '设计院', '开发', '置业', '园区'],
    '通信5G': ['通信', '光纤', '宽带', '天线', '基站', '射频', '滤波器',
              '光模块', '光器件', '数通', '专网'],
    '环保': ['环保', '节能', '碳排', '水务', '垃圾', '废旧', '循环', '再生',
            '污水处理', '固废', '危废'],
    '传媒教育': ['传媒', '出版', '教育', '影视', '广告', '游戏', '动漫', '文化',
                '体育', '演艺', '展览'],
    '农林牧渔': ['农业', '农化', '种子', '粮食', '畜牧', '养殖', '饲料', '渔业',
                '林业', '种业', '兽医', '温室'],
    '计算机/软件': ['软件', '数据', '信息', '互联', '智能', '数字', '计算机',
                   '云计算', '网络', '科技', 'IT', '信创', '安防'],
    '纺织服装': ['纺织', '服装', '服饰', '鞋业', '箱包', '皮革', '面料', '纽扣',
                '拉链', '家纺', '印染'],
    '交通运输': ['交通', '运输', '物流', '港口', '铁路', '高速', '航运', '机场',
                '快递', '公交', '地铁', '铁路局'],
    '金融': ['银行', '证券', '保险', '信托', '期货', '金融', '投资', '基金'],
    '煤炭钢铁': ['煤业', '钢铁', '焦化', '炭素', '铁矿', '特钢', '不锈钢', '普钢'],
    '机械制造': ['制造', '机械', '重工', '装备', '精密', '机床', '模具', '铸件',
                '锻造', '泵阀', '轴承', '液压'],
    '家电': ['电器', '家电', '空调', '厨卫', '洗衣机', '冰箱', '热水器', '照明',
            '插座', '电表'],
    '旅游酒店': ['旅游', '酒店', '休闲', '景区', '度假', '游乐园', '民宿'],
}

def load_json(path):
    if not os.path.exists(path):
        return {}
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)

def map_stock_to_sector(name):
    """把股票名称映射到最匹配的概念板块"""
    for sector, keywords in SECTOR_KEYWORDS.items():
        if any(kw in name for kw in keywords):
            return sector
    return '其他'

def compute_capital_ratio(stock_data):
    """
    计算个股5日和10日主力资金占比
    主力占比 = 主力净流入总和 / 成交额总和
    优先使用 flow_5d_wan/flow_10d_wan 和 avg_amount_yi 快速计算
    
    Returns: (ratio_5d, ratio_10d)
    """
    flow_5d = stock_data.get('flow_5d_wan', 0)
    avg_amount = stock_data.get('avg_amount_yi', 0)
    
    # 5日占比
    if avg_amount > 0:
        ratio_5d = round(flow_5d / (avg_amount * 10000 * 5) * 100, 2)
    else:
        ratio_5d = None
    
    # 10日占比（使用 flow_10d_wan 或 daily_10d）
    flow_10d = stock_data.get('flow_10d_wan')
    daily_10d = stock_data.get('daily_10d', [])
    
    if flow_10d is not None and avg_amount > 0:
        ratio_10d = round(flow_10d / (avg_amount * 10000 * 10) * 100, 2)
    elif len(daily_10d) >= 10:
        total_flow_10d = sum(d.get('flow_wan', 0) for d in daily_10d)
        total_amount_10d = sum(d.get('amount_yi', 0) for d in daily_10d) * 10000
        ratio_10d = round(total_flow_10d / total_amount_10d * 100, 2) if total_amount_10d > 0 else None
    elif len(stock_data.get('daily', [])) >= 10:
        daily = stock_data.get('daily', [])
        days_10 = daily[-10:]
        total_flow_10d = sum(d.get('flow_wan', 0) for d in days_10)
        total_amount_10d = sum(d.get('amount_yi', 0) for d in days_10) * 10000
        ratio_10d = round(total_flow_10d / total_amount_10d * 100, 2) if total_amount_10d > 0 else None
    else:
        ratio_10d = None  # 数据不足10日
    
    return ratio_5d, ratio_10d


def main():
    print(f"{'='*60}")
    print(f"概念板块预筛选 — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*60}")
    
    # 1. 加载全市场数据
    cf_data = load_json(CF_FILE)
    cf_stocks = cf_data.get('stocks', {})
    pred_data = load_json(PRED_FILE)
    all_results = {r['code']: r for r in pred_data.get('all_results', [])}
    
    print(f"资金流覆盖: {len(cf_stocks)}只, 评分覆盖: {len(all_results)}只")
    
    # 2. 全市场股票 → 板块映射
    sector_map = {}  # sector_name → [stock_info]
    unmatched = 0
    for code, cf in cf_stocks.items():
        name = cf.get('name', '')
        chg = cf.get('latest_chg', 0)
        flow_5d = cf.get('flow_5d_wan', 0)
        avg_amount = cf.get('avg_amount_yi', 0)
        
        sector = map_stock_to_sector(name)
        if sector == '其他':
            unmatched += 1
        
        # 计算主力占比
        ratio_5d, ratio_10d = compute_capital_ratio(cf)
        
        sector_map.setdefault(sector, []).append({
            'code': code,
            'name': name,
            'chg': chg,
            'flow_5d_wan': flow_5d,
            'avg_amount_yi': avg_amount,
            'ratio_5d': ratio_5d,
            'ratio_10d': ratio_10d,
            'daily_score': all_results.get(code, {}).get('total_score', 0),
            'is_limit_up': chg >= 9.8,
            'is_strong': chg >= 7,
            'is_gain': chg > 0,
        })
    
    print(f"板块映射: {len(sector_map)}个板块, 未匹配: {unmatched}只")
    
    # 3. 计算每个板块的统计指标
    limit_up_threshold = 1 if LOOSE_MODE else 3
    print(f"筛选条件: 均涨幅>{MIN_AVG_GAIN}%, 涨停≥{limit_up_threshold}家 (模式={'宽松' if LOOSE_MODE else '严格'})")
    print()
    
    sector_stats = []
    for sector, members in sector_map.items():
        total = len(members)
        if total < 2:
            continue  # skip single-stock "sectors"
        
        avg_gain = sum(m['chg'] for m in members) / total
        limit_ups = [m for m in members if m['is_limit_up']]
        strong_gains = [m for m in members if m['is_strong']]
        gains = [m for m in members if m['is_gain']]
        high_gain = [m for m in members if m['chg'] > 5]  # 大涨但未涨停
        
        # 主力占比统计
        ratios_5d = [m['ratio_5d'] for m in members if m['ratio_5d'] is not None]
        avg_ratio_5d = round(sum(ratios_5d) / len(ratios_5d), 2) if ratios_5d else None
        
        sector_stats.append({
            'sector': sector,
            'total': total,
            'avg_gain': round(avg_gain, 2),
            'limit_up_count': len(limit_ups),
            'limit_up_names': [m['name'] for m in limit_ups],
            'strong_count': len(strong_gains),
            'gain_count': len(gains),
            'high_gain_count': len(high_gain),
            'avg_ratio_5d': avg_ratio_5d,
            'members': members
        })
    
    # 按均涨幅排序
    sector_stats.sort(key=lambda x: -x['avg_gain'])
    
    # 4. 多级筛选符合条件的板块
    print(f"{'板块':12s} | {'数量':>4s} | {'均涨幅':>7s} | {'涨停':>3s} | {'≥5%':>4s} | {'涨比':>4s} | {'主力5d':>8s} | 状态")
    print('-' * 85)
    
    # T1: 涨幅>1.5% + 涨停≥N | T2: 涨幅>1.0% + 涨停≥1 | T3: 涨幅>0.5% + (涨停≥1 或 ≥3家涨>5%)
    tier1, tier2, tier3 = [], [], []
    
    for ss in sector_stats:
        gain_ratio = ss['gain_count'] / ss['total'] * 100 if ss['total'] > 0 else 0
        ratio_str = f"{ss['avg_ratio_5d']:+.2f}%" if ss['avg_ratio_5d'] else 'N/A'
        
        if ss['avg_gain'] > 1.5 and ss['limit_up_count'] >= limit_up_threshold:
            ss['tier'] = 1; ss['status'] = 'T1严格入选'; tier1.append(ss); status = '🎯T1'
        elif ss['avg_gain'] > 1.0 and ss['limit_up_count'] >= 1:
            ss['tier'] = 2; ss['status'] = 'T2中选'; tier2.append(ss); status = '✅T2'
        elif ss['avg_gain'] > 0.5 and (ss['limit_up_count'] >= 1 or ss['high_gain_count'] >= 3):
            ss['tier'] = 3; ss['status'] = 'T3宽选'; tier3.append(ss); status = '🟡T3'
        elif ss['avg_gain'] > 1.5:
            status = '⚠缺涨停'
        elif ss['limit_up_count'] >= limit_up_threshold:
            status = '⚠涨幅低'
        else:
            status = ''
        ss['gain_ratio'] = round(gain_ratio, 1)
        
        print(f"{ss['sector']:10s} | {ss['total']:4d} | {ss['avg_gain']:+6.2f}% | {ss['limit_up_count']:3d} | "
              f"{ss['high_gain_count']:4d} | {gain_ratio:3.0f}% | {ratio_str:>8s} | {status}")
    
    # 按优先级合并
    qualified_sectors = tier1 + tier2 + tier3
    if not qualified_sectors:
        print(f"\n⚠️ 无板块达标，取涨幅Top3板块兜底")
        positive = sorted([ss for ss in sector_stats if ss['avg_gain'] > 0], key=lambda x: -x['avg_gain'])
        qualified_sectors = positive[:3]
        for ss in qualified_sectors:
            ss['tier'] = 99; ss['status'] = '兜底入选'
    
    print(f"\n{'='*60}")
    print(f"✅ 符合条件的板块: {len(qualified_sectors)}个")
    
    # 收集所有符合条件的板块内的股票代码
    qualified_codes = set()
    sector_detail = {}
    
    for qs in qualified_sectors:
        sector = qs['sector']
        members = qs['members']
        print(f"\n【{sector}】{qs['total']}只 | 均涨幅{qs['avg_gain']:+.2f}% | 涨停{qs['limit_up_count']}只 | 主力占比{qs['avg_ratio_5d']}")
        
        # 板块内按综合评分排序（涨幅 + 主力占比 + daily_score）
        ranked = []
        for m in members:
            score = (
                m['chg'] * 3 +  # 涨幅权重3
                (m['ratio_5d'] or 0) * 10 +  # 主力占比权重10
                m['daily_score'] * 0.05  # 基本面权重0.05
            )
            ranked.append((score, m))
        ranked.sort(key=lambda x: -x[0])
        
        sector_stock_list = []
        for score, m in ranked[:20]:  # 每个板块最多取20只
            code = m['code']
            qualified_codes.add(code)
            sector_stock_list.append({
                'code': code,
                'name': m['name'],
                'chg': m['chg'],
                'flow_5d_wan': m['flow_5d_wan'],
                'ratio_5d': m['ratio_5d'],
                'ratio_10d': m['ratio_10d'],
                'daily_score': m['daily_score'],
                'is_limit_up': m['is_limit_up'],
                'composite_score': round(score, 1)
            })
            flag = '🎯涨停' if m['is_limit_up'] else ('🔥大涨' if m['chg'] > 5 else '')
            print(f"  {m['name']:6s} {m['chg']:+5.1f}% | 主力{m['ratio_5d'] if m['ratio_5d'] else 'N/A'}% | 评分{m['daily_score']:.0f} | 综合{score:.0f} {flag}")
        
        sector_detail[sector] = {
            'total': qs['total'],
            'avg_gain': qs['avg_gain'],
            'limit_up_count': qs['limit_up_count'],
            'limit_up_names': qs['limit_up_names'],
            'avg_ratio_5d': qs['avg_ratio_5d'],
            'strong_count': qs['strong_count'],
            'status': qs.get('status', '入选'),
            'stocks': sector_stock_list
        }
    
    # 6. 输出结果
    output = {
        'update_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'data_source_time': cf_data.get('update_time', ''),
        'filter_config': {
            'min_avg_gain': MIN_AVG_GAIN,
            'min_limit_ups': limit_up_threshold,
            'loose_mode': LOOSE_MODE,
        },
        'market_summary': {
            'total_stocks_analyzed': len(cf_stocks),
            'sectors_mapped': len(sector_map),
            'sectors_qualified': len(qualified_sectors),
            'total_qualified_stocks': len(qualified_codes),
        },
        'all_sectors': [
            {
                'sector': ss['sector'],
                'total': ss['total'],
                'avg_gain': ss['avg_gain'],
                'limit_up_count': ss['limit_up_count'],
                'strong_count': ss['strong_count'],
                'high_gain_count': ss['high_gain_count'],
                'avg_ratio_5d': ss['avg_ratio_5d'],
                'qualified': ss in qualified_sectors,
            }
            for ss in sector_stats
        ],
        'qualified_sectors': sector_detail,
        'qualified_codes': sorted(list(qualified_codes)),
    }
    
    with open(OUTPUT, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    
    print(f"\n{'='*60}")
    print(f"✅ 输出: {OUTPUT}")
    print(f"   合格板块: {len(qualified_sectors)}个")
    print(f"   覆盖个股: {len(qualified_codes)}只")
    print(f"   文件大小: {os.path.getsize(OUTPUT)/1024:.0f}KB")
    
    return output

if __name__ == '__main__':
    main()
