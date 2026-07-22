#!/usr/bin/env python3
"""
全量选股脚本
每次从全市场重新选出最优30只票，不做增量换血，全量替换。
"""
import json, os, re

BASE = os.path.dirname(__file__)
INDEX_HTML = os.path.join(BASE, 'deploy', 'index.html')
PRED_FILE = os.path.join(BASE, 'daily_predictions.json')
CF_FILE = os.path.join(BASE, 'capital_flow.json')
SECTOR_FILE = os.path.join(BASE, 'sector_resonance.json')
SECTOR_FILTER_FILE = os.path.join(BASE, 'sector_filter.json')
KDJ_FILE = os.path.join(BASE, 'kdj_factor.json')
FUNDAMENTAL_FILE = os.path.join(BASE, 'fundamental_factors.json')
OUTPUT = os.path.join(BASE, 'dynamic_stocks.json')

# 每次全量选出30只
TARGET_POOL_SIZE = 30

# 白名单：用户指定持仓，动态换血永不踢出
PROTECTED_CODES = {}  # 白名单（用户指定做T关注票，不在STOCKS中）

def load_json(path):
    if not os.path.exists(path):
        return {}
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)

def extract_stocks_from_html():
    with open(INDEX_HTML, 'r', encoding='utf-8') as f:
        html = f.read()
    m = re.search(r'const STOCKS\s*=\s*(\[.*?\]);', html, re.DOTALL)
    if not m:
        raise ValueError("找不到STOCKS数组")
    return json.loads(m.group(1))

def pure_code(code):
    """sh600522 → 600522"""
    return code.replace('sh', '').replace('sz', '')

def to_prefixed_code(code):
    """600522 → sh600522, 002451 → sz002451"""
    if code.startswith('6'):
        return 'sh' + code
    return 'sz' + code

def is_zombie(score, flow_5d):
    """数据缺失票：dailyScore和资金流全为0 → 僵尸票"""
    return score == 0 and flow_5d == 0

# =====================================================================
# 四层漏斗选股框架
# 逻辑：①资金推动 → ②推动理由 → ③核心壁垒 → ④股性好
# =====================================================================

def calc_capital_quality(flow_5d, flow_10d, daily=None):
    """
    ① 资金层（0-25分）—— 硬性门禁 + 质量评分
    
    硬门禁：10日累计>0 或 最近3日连续流入 → 不过关直接排除
    质量分：连续流入天数 + 资金加速比 + 资金体量
    
    Returns: (passed_bool, capital_score, diagnosis_str)
    """
    daily = daily or []
    daily_flows = [d.get('flow_wan', 0) for d in daily]
    
    # === 硬性门禁 ===
    recent_3 = daily_flows[-3:] if len(daily_flows) >= 3 else daily_flows
    recent_3_inflow = len(recent_3) >= 3 and all(f > 0 for f in recent_3)
    
    # 10日累计态度
    if flow_10d is not None:
        passed_10d = flow_10d > 0
    else:
        passed_10d = None
    
    if passed_10d is not None:
        passed = passed_10d or recent_3_inflow
    else:
        passed = flow_5d > 0 or recent_3_inflow
    
    if not passed:
        diag = "资金博弈偏空" if flow_5d <= 0 else "近3日无持续流入"
        return (False, 0, diag)
    
    # === 质量评分 ===
    score = 0
    
    # 1. 连续流入天数（0-10分）
    consecutive_inflow = 0
    for f in reversed(daily_flows):
        if f > 0:
            consecutive_inflow += 1
        else:
            break
    if consecutive_inflow >= 5:
        score += 10
    elif consecutive_inflow >= 3:
        score += 7
    elif consecutive_inflow >= 2:
        score += 4
    else:
        score += 1  # 今天才开始流入
    
    # 2. 资金加速比（0-8分）— 最近5日占10日的比例
    if flow_10d is not None and flow_5d > 0 and flow_10d > 0:
        ratio = flow_5d / flow_10d
        if ratio > 0.8:
            score += 8   # 强势加速（80%流入都在最近5天）
        elif ratio > 0.6:
            score += 6
        elif ratio > 0.4:
            score += 4
        else:
            score += 2
    elif flow_5d > 0:
        score += 3  # 无10日数据，5日为正给基础分
    
    # 3. 资金体量（0-7分）
    ref_flow = flow_5d if flow_10d is None else max(flow_5d, flow_10d)
    if ref_flow > 50000:
        score += 7
    elif ref_flow > 20000:
        score += 5
    elif ref_flow > 5000:
        score += 3
    elif ref_flow > 0:
        score += 1
    
    # 诊断
    parts = []
    if consecutive_inflow >= 3:
        parts.append(f"连{consecutive_inflow}日流入")
    if flow_10d is not None and flow_5d > 0 and flow_10d > 0 and flow_5d / flow_10d > 0.6:
        parts.append("资金加速")
    if ref_flow > 20000:
        parts.append(f"{ref_flow/10000:.1f}亿体量")
    diag = " | ".join(parts) if parts else "资金正流入"
    
    return (True, min(score, 25), diag)


def calc_rationale(code, sector_tier, ratio_5d, ratio_10d, flow_5d, score):
    """
    ② 逻辑层（0-15分）—— 推动理由分析
    
    判断：这只票为什么涨/跌？
    - 板块驱动：在强势板块内，板块共振推动
    - 独立逻辑：不在板块但资金持续涌入，可能有独立催化剂
    - 无明确驱动：不属于任何强势板块，资金也一般
    
    Returns: (rationale_score, rationale_type, rationale_diag)
    """
    r_score = 0
    r_type = "无明确驱动"
    r_diag = ""
    
    # 板块共振判断
    if sector_tier is not None:
        if sector_tier == 1:
            r_score += 10
            r_type = "板块共振T1"
            r_diag = "最强板块共振"
        elif sector_tier == 2:
            r_score += 8
            r_type = "板块共振T2"
            r_diag = "强势板块内"
        elif sector_tier == 3:
            r_score += 6
            r_type = "板块共振T3"
            r_diag = "板块内共振"
        elif sector_tier == 99:
            r_score += 4
            r_type = "板块兜底"
            r_diag = "弱板块共振"
    
    # 主力占比加分（同一板块内，主力介入深的票逻辑更清晰）
    if ratio_5d is not None and ratio_5d > 3:
        r_score += 3
        r_diag += " | 主力深度介入"
    elif ratio_5d is not None and ratio_5d > 1:
        r_score += 2
    elif ratio_5d is not None and ratio_5d > 0.5:
        r_score += 1
    
    # 独立逻辑检测：不在板块但资金大幅流入（>2亿）且基本面评分高
    if sector_tier is None:
        if flow_5d > 20000 and score > 55:
            r_score += 8
            r_type = "独立逻辑"
            r_diag = "大资金独立推动 | 基本面支撑"
        elif flow_5d > 10000:
            r_score += 5
            r_type = "独立逻辑"
            r_diag = "资金独立推动"
        elif flow_5d > 5000:
            r_score += 3
            r_type = "资金关注"
            r_diag = "有资金关注"
    
    return (min(r_score, 15), r_type, r_diag)


def calc_moat(code, fundamental_data):
    """
    ③ 壁垒层（0-20分）—— 核心护城河评估
    
    评估维度：ROE(盈利能力) + PE/PB(估值合理性) + 流通市值(规模壁垒) + 机构持仓
    
    无基本面数据的票默认中性10分（不因数据缺失误杀）
    """
    fd = fundamental_data.get(code, {})
    if not fd:
        return (10, "无基本面数据")
    
    m_score = 0
    factors = []
    
    # 1. ROE 盈利能力（0-6分）— ROE > 10% 代表有持续赚钱能力
    roe = fd.get('roe', 0)
    if roe > 20:
        m_score += 6
        factors.append(f"ROE{roe:.0f}%")
    elif roe > 15:
        m_score += 5
        factors.append(f"ROE{roe:.0f}%")
    elif roe > 10:
        m_score += 4
        factors.append(f"ROE{roe:.0f}%")
    elif roe > 5:
        m_score += 2
    elif roe > 0:
        m_score += 1
    
    # 2. PE 估值合理性（0-4分）
    pe = fd.get('pe', 0)
    if 10 <= pe <= 30:
        m_score += 4
        factors.append(f"PE{pe:.0f}")
    elif 5 <= pe <= 40:
        m_score += 3
    elif 0 < pe < 50:
        m_score += 1
    # pe 为负（亏损）不加分
    
    # 3. PB 资产定价（0-3分）
    pb = fd.get('pb', 0)
    if 0 < pb <= 2:
        m_score += 3
    elif 0 < pb <= 3:
        m_score += 2
    elif 0 < pb <= 5:
        m_score += 1
    
    # 4. 流通市值——规模壁垒（0-4分）
    circ_mv = fd.get('circ_mv', 0)  # 流通市值（元）
    mv_yi = circ_mv / 1e8  # 亿
    if mv_yi > 500:
        m_score += 4   # 大市值龙头，规模壁垒强
        factors.append(f"市值{mv_yi:.0f}亿")
    elif mv_yi > 100:
        m_score += 3
    elif mv_yi > 30:
        m_score += 2
    elif mv_yi > 10:
        m_score += 1
    
    # 5. 机构持仓信号（0-3分）
    fund_bonus = fd.get('fund_bonus', 0)
    if fund_bonus >= 8:
        m_score += 3
        factors.append("机构重仓")
    elif fund_bonus >= 5:
        m_score += 2
    elif fund_bonus >= 3:
        m_score += 1
    
    diag = " | ".join(factors) if factors else "壁垒一般"
    return (min(m_score, 20), diag)


def calc_trading_quality(drop_20d, latest_chg, consecutive, avg_vol_up, kdj_bonus=0, rsi=None):
    """
    ④ 股性层（0-40分）—— 交易特性综合评估
    
    整合：位置 + 趋势 + 跌速 + 技术信号
    预判：这只票好不好做？
    """
    t_score = 0
    
    # === 位置评估（0-12分）=== — 回调-10%~-20%最优
    if -20 <= drop_20d <= -10:
        t_score += 12  # 回调到位（最优区间）
    elif -25 <= drop_20d < -20:
        t_score += 9   # 稍深
    elif -10 < drop_20d <= -5:
        t_score += 8   # 浅回调
    elif -30 <= drop_20d < -25:
        t_score += 6   # 深调
    elif -5 < drop_20d <= 0:
        t_score += 5   # 微调
    elif 0 < drop_20d <= 5:
        t_score += 3   # 小涨（非回调）
    elif drop_20d < -30:
        t_score += 3   # 太深，基本面风险
    else:
        t_score += 1   # 大涨追高
    
    # === 趋势+跌速（0-14分）=== — 启动确认+温和涨跌最优
    if drop_20d < -5:
        # 票在回调中，关注跌速
        if latest_chg > 3:
            t_score += 14  # 强反弹启动
        elif latest_chg > 1:
            t_score += 12  # 止跌回升
        elif latest_chg > 0:
            t_score += 9   # 微弱回升
        elif latest_chg > -1:
            t_score += 6   # 跌势放缓
        elif latest_chg > -2:
            t_score += 3   # 仍在阴跌
        else:
            t_score += 0   # 加速下跌
    else:
        # 票没怎么跌
        if latest_chg > 5:
            t_score += 3   # 大涨追高
        elif latest_chg > 1:
            t_score += 8   # 温和上涨
        elif latest_chg > -1:
            t_score += 6   # 横盘
        else:
            t_score += 2   # 下跌
    
    # 连续上涨惩罚（连涨>4天追高）
    if consecutive >= 6:
        t_score -= 3
    elif consecutive >= 4:
        t_score -= 1
    
    # === 技术信号（0-14分）===
    tech = 0
    if kdj_bonus >= 7:
        tech += 6
    elif kdj_bonus >= 5:
        tech += 4
    elif kdj_bonus >= 3:
        tech += 2
    
    if avg_vol_up is not None and 0 < avg_vol_up < 0.95:
        tech += 4  # 缩量止跌
    
    if rsi is not None:
        if rsi < 30:
            tech += 4  # 超卖反弹
        elif rsi < 40:
            tech += 2  # 偏低
    
    t_score += min(tech, 14)
    
    return min(t_score, 40)


def calc_health(score, flow_5d, flow_10d, drop_20d, consecutive, latest_chg, avg_vol_up,
                kdj_bonus=0, rsi=None, sector_tier=None, ratio_5d=None, ratio_10d=None,
                daily=None, code=None, fundamental_data=None):
    """
    四层漏斗综合健康度（0-100），返回 -999 表示不合格需排除
    
    ┌─────────────────────────────────────────┐
    │ ① 资金门（硬过滤）       0-25分          │
    │ → 不过关直接返回 -999                   │
    ├─────────────────────────────────────────┤
    │ ② 推动理由（逻辑层）     0-15分          │
    │ → 板块驱动 vs 独立逻辑                 │
    ├─────────────────────────────────────────┤
    │ ③ 核心壁垒（基本面）     0-20分          │
    │ → ROE/PE/PB/市值/机构                  │
    ├─────────────────────────────────────────┤
    │ ④ 股性好（交易特性）     0-40分          │
    │ → 位置+趋势+跌速+技术信号              │
    └─────────────────────────────────────────┘
    """
    # === 僵尸票检测 ===
    if is_zombie(score, flow_5d):
        return -999.0, {'capital': (False, 0, '僵尸票'), 'rationale': (0, '', ''), 'moat': (0, ''), 'trading': 0}
    
    # ========== ① 资金门 ==========
    passed, capital_score, capital_diag = calc_capital_quality(flow_5d, flow_10d, daily)
    if not passed:
        return -999.0, {'capital': (passed, capital_score, capital_diag), 'rationale': (0, '', ''), 'moat': (0, ''), 'trading': 0}
    
    # ========== ② 推动理由 ==========
    rationale_score, rationale_type, rationale_diag = calc_rationale(code, sector_tier, ratio_5d, ratio_10d, flow_5d, score)
    
    # ========== ③ 核心壁垒 ==========
    moat_score, moat_diag = calc_moat(code, fundamental_data or {})
    
    # ========== ④ 股性好 ==========
    trading_score = calc_trading_quality(drop_20d, latest_chg, consecutive, avg_vol_up, kdj_bonus, rsi)
    
    health = capital_score + rationale_score + moat_score + trading_score
    health = round(max(0, min(health, 100)), 1)
    
    details = {
        'capital': (passed, capital_score, capital_diag),
        'rationale': (rationale_score, rationale_type, rationale_diag),
        'moat': (moat_score, moat_diag),
        'trading': trading_score
    }
    return health, details

def calc_launch_health(score, flow_5d, drop_20d, consecutive, latest_chg, avg_vol_up, kdj_bonus=0, rsi=None):
    """
    启动段专属健康度算法（0-100），返回 -999 表示僵尸票/不合格

    专门筛选"回调到位 + 跌势已止 + 技术信号确认"的启动段候选票
    与 calc_health 的区别：位置和技术信号权重最高，基本面权重最低

    1. 位置评估   30分：回调-10%~-20%最优，追高直接低分
    2. 跌速判断   25分：当天必须止跌或微涨，加速下跌直接0分
    3. 技术信号   20分：KDJ反转+缩量止跌+RSI超卖+连续高开确认
    4. 资金面     15分：资金回流加分
    5. 基本面     10分：评分兜底（权重低，不主导）
    """
    # === 僵尸票检测 ===
    if is_zombie(score, flow_5d):
        return -999.0

    # ========== 1. 位置评估（0-30）==========
    if -20 <= drop_20d <= -10:
        pos_score = 30   # 回调到位（最优区间）
    elif -25 <= drop_20d < -20:
        pos_score = 24   # 稍深但有反弹空间
    elif -10 < drop_20d <= -5:
        pos_score = 22   # 浅回调
    elif -30 <= drop_20d < -25:
        pos_score = 16   # 深度超跌
    elif -5 < drop_20d <= 0:
        pos_score = 12   # 微调
    elif 0 < drop_20d <= 5:
        pos_score = 5    # 小涨（不是回调）
    elif drop_20d < -30:
        pos_score = 8    # 太深，可能有基本面问题
    else:
        pos_score = 2    # 大涨（追高，启动段不选）

    # ========== 2. 跌速判断（0-25）==========
    # 启动段必须当天止跌或微涨
    if latest_chg > 3:
        mom_score = 25   # 强反弹启动
    elif latest_chg > 1:
        mom_score = 22   # 止跌回升
    elif latest_chg > 0:
        mom_score = 18   # 微弱回升
    elif latest_chg > -1:
        mom_score = 12   # 跌势放缓
    elif latest_chg > -2:
        mom_score = 5    # 仍在阴跌
    else:
        mom_score = 0    # 加速下跌 → 不适合启动段

    # ========== 3. 技术信号（0-20）==========
    tech_score = 0
    if kdj_bonus >= 7:
        tech_score += 8
    elif kdj_bonus >= 5:
        tech_score += 6
    elif kdj_bonus >= 3:
        tech_score += 3

    if avg_vol_up is not None and 0 < avg_vol_up < 0.95:
        tech_score += 5  # 缩量止跌

    if rsi is not None and rsi < 35:
        tech_score += 4  # RSI超卖
    elif rsi is not None and rsi < 45:
        tech_score += 2  # RSI偏低

    # 连续高开1-3天 = 刚启动确认
    if 1 <= consecutive <= 3:
        tech_score += 3
    tech_score = min(tech_score, 20)

    # ========== 4. 资金面（0-15）==========
    if flow_5d > 30000:
        flow_score = 15
    elif flow_5d > 10000:
        flow_score = 13
    elif flow_5d > 0:
        flow_score = 8 + min(flow_5d / 10000 * 5, 5)
    elif flow_5d > -5000:
        flow_score = 6   # 流出减缓
    elif flow_5d > -20000:
        flow_score = 3   # 轻度流出
    else:
        flow_score = 0   # 大幅流出

    # ========== 5. 基本面（0-10）==========
    base = min(score / 70 * 10, 10)

    health = pos_score + mom_score + tech_score + flow_score + base
    return round(max(0, min(health, 100)), 1)


def select_launch_pool(all_results, cf_stocks, kdj_stocks, exclude_codes=set()):
    """
    池B：启动段专属选股
    从全市场候选池中选 30 只启动段特征最强的票
    """
    LAUNCH_COUNT = 30
    candidates = []

    for code, pr in all_results.items():
        if code in exclude_codes:
            continue
        name = pr.get('name', '')
        if 'ST' in name or '退' in name:
            continue
        if not (code.startswith('00') or code.startswith('60')):
            continue

        score = pr.get('total_score', 0)
        if score < 45:
            continue

        cf = cf_stocks.get(code, {})
        flow_5d = cf.get('flow_5d_wan', 0)
        metrics = pr.get('metrics', {})
        drop_20d = metrics.get('drop_20d', 0)
        consecutive = metrics.get('consecutive', 0)
        latest_chg = cf.get('latest_chg', 0)
        avg_vol_up = metrics.get('avg_vol_up', 1.0)
        rsi = metrics.get('rsi', None)
        kdj_bonus = kdj_stocks.get(code, {}).get('kdj_bonus', 0)

        # === 启动段硬性过滤 ===
        # 1. 僵尸票排除
        if is_zombie(score, flow_5d):
            continue
        # 2. 当天跌幅超1% → 还在加速跌，不选
        if latest_chg < -1:
            continue
        # 3. 20日涨幅>5% → 已经涨起来，不是启动段
        if drop_20d > 5:
            continue
        # 4. 20日跌幅超-35% → 可能有基本面问题
        if drop_20d < -35:
            continue
        # 5. 资金大幅流出（<-3亿）→ 不选
        if flow_5d < -30000:
            continue
        # 6. 连涨6天+且当天涨>5% → 追高风险
        if consecutive >= 6 and latest_chg > 5:
            continue

        health = calc_launch_health(score, flow_5d, drop_20d, consecutive, latest_chg, avg_vol_up, kdj_bonus, rsi)
        if health == -999 or health < 40:
            continue

        candidates.append({
            'code': code,
            'name': name,
            'score': score,
            'flow_5d': flow_5d,
            'drop_20d': drop_20d,
            'consecutive': consecutive,
            'latest_chg': latest_chg,
            'avg_vol_up': avg_vol_up,
            'rsi': rsi,
            'kdj_bonus': kdj_bonus,
            'launch_health': health,
            'reasons': pr.get('reasons', []),
            'confidence': pr.get('confidence', '')
        })

    candidates.sort(key=lambda x: x['launch_health'], reverse=True)
    return candidates[:LAUNCH_COUNT], len(candidates)


def build_launch_stocks(launch_picks):
    """把启动段候选票构造成前端 STOCKS 格式的对象"""
    sector_map = {
        '半导体': ['芯', '微', '集成', '封测', '晶圆'],
        '光通信': ['光', '纤', '缆'],
        '电力设备': ['电', '能源', '电网'],
        '有色金属': ['矿', '金属', '铜', '铝', '锂', '镍'],
        '化工': ['化工', '化学', '材料'],
        '食品饮料': ['食', '酒', '乳', '味'],
        '医药': ['药', '医', '生物', '健康'],
        '汽车': ['汽', '车', '新能源车'],
        '军工': ['航', '军工', '国防', '武器'],
    }
    def guess_sector(name):
        for sec, kws in sector_map.items():
            if any(kw in name for kw in kws):
                return sec
        return '热门标的'

    stocks = []
    for c in launch_picks:
        reasons_str = '; '.join(c['reasons'][:2]) if c['reasons'] else '启动段选入'
        tags = []
        if -20 <= c['drop_20d'] <= -10:
            tags.append('回调到位')
        elif c['drop_20d'] < -20:
            tags.append('超跌')
        if c['flow_5d'] > 10000:
            tags.append('资金流入')
        elif c['flow_5d'] > 0:
            tags.append('资金回流')
        if c['kdj_bonus'] >= 5:
            tags.append('KDJ反转')
        if c['avg_vol_up'] is not None and c['avg_vol_up'] < 0.95:
            tags.append('缩量止跌')
        if c['latest_chg'] > 1:
            tags.append('止跌回升')
        elif c['latest_chg'] > 0:
            tags.append('企稳')

        s = {
            'code': to_prefixed_code(c['code']),
            'name': c['name'],
            'sector': guess_sector(c['name']),
            'direction': ','.join(tags[:2]) if tags else '启动段',
            'pe': 0,
            'profitGrowth': 0,
            'reason': f"🚀启动段（{','.join(tags) if tags else '综合'}）：{reasons_str}",
            'roe': 0,
            'grossMargin': 0,
            'debtRatio': 0,
            'riskFlags': ['🚀启动段'] + tags,
            'healthScore': c['launch_health'],
            'dailyScore': c['score'],
            'flow5d': c['flow_5d'],
            'drop20d': round(c['drop_20d'], 1),
            'consecutive': c['consecutive'],
            'kdjBonus': c['kdj_bonus'],
            'rsi': round(c['rsi'], 1) if c.get('rsi') is not None else None,
            'isNew': True
        }
        stocks.append(s)
    return stocks


def main():
    # 0. 板块预筛选（如果文件不存在或超过4小时）
    import time as _time
    if not os.path.exists(SECTOR_FILTER_FILE) or \
       _time.time() - os.path.getmtime(SECTOR_FILTER_FILE) > 14400:
        print("🔄 运行概念板块预筛选...")
        import subprocess as _sp
        sp = os.path.join(BASE, 'sector_pre_filter.py')
        _sp.run(['/Users/fuckyouasshole/.workbuddy/binaries/python/envs/default/bin/python3', sp],
                capture_output=False, timeout=30)
    
    # 1. 加载数据
    pred = load_json(PRED_FILE)
    cf_data = load_json(CF_FILE)
    sector_data = load_json(SECTOR_FILE)
    sector_filter = load_json(SECTOR_FILTER_FILE)
    fundamental_data = load_json(FUNDAMENTAL_FILE).get('stocks', {})  # code → 基本面数据

    all_results = {r['code']: r for r in pred.get('all_results', [])}
    cf_stocks = cf_data.get('stocks', {})
    sector_stocks = sector_data.get('stocks', {})
    kdj_data = load_json(KDJ_FILE)

    # 构建板块共振映射: code → (tier, sector_name)
    code_sector_tier = {}
    code_sector_ratio = {}  # code → (ratio_5d, ratio_10d)
    qualified_codes_set = set(sector_filter.get('qualified_codes', []))
    for sector_name, sd in sector_filter.get('qualified_sectors', {}).items():
        tier = sd.get('tier', sd.get('status', ''))
        # 从状态推断tier
        if 'T1' in str(sd.get('status', '')):
            tier_num = 1
        elif 'T2' in str(sd.get('status', '')):
            tier_num = 2
        elif 'T3' in str(sd.get('status', '')):
            tier_num = 3
        else:
            tier_num = 99
        for s in sd.get('stocks', []):
            code_sector_tier[s['code']] = tier_num
            code_sector_ratio[s['code']] = (s.get('ratio_5d'), s.get('ratio_10d'))

    sector_qualified_count = len(code_sector_tier)
    print(f"全市场候选: daily_predictions {len(all_results)}只, 资金流 {len(cf_stocks)}只")
    print(f"板块预筛选: {len(sector_filter.get('qualified_sectors',{}))}个强势板块, 覆盖{sector_qualified_count}只票")
    kdj_stocks = kdj_data.get('stocks', {})
    if kdj_stocks:
        kdj_hits = sum(1 for v in kdj_stocks.values() if v.get('kdj_bonus', 0) > 0)
        print(f"KDJ因子: {len(kdj_stocks)}只已扫描, {kdj_hits}只命中低位上升")

    # 2. 全市场扫描 + 四层漏斗评分
    sector_candidates = []
    fallback_candidates = []
    for code, pr in all_results.items():
        name = pr.get('name', '')
        # 排除ST/退市
        if 'ST' in name or '退' in name:
            continue
        # 只保留00/60主板
        if not (code.startswith('00') or code.startswith('60')):
            continue
        score = pr.get('total_score', 0)
        if score < 50:
            continue
        cf = cf_stocks.get(code, {})
        flow_5d = cf.get('flow_5d_wan', 0)
        flow_10d = cf.get('flow_10d_wan')
        metrics = pr.get('metrics', {})
        drop_20d = metrics.get('drop_20d', 0)
        consecutive = metrics.get('consecutive', 0)
        latest_chg = cf.get('latest_chg', 0)
        avg_vol_up = metrics.get('avg_vol_up', 1.0)
        rsi = metrics.get('rsi', None)
        kdj_bonus = kdj_stocks.get(code, {}).get('kdj_bonus', 0)
        daily = cf.get('daily', [])

        # 排除数据缺失票
        if is_zombie(score, flow_5d):
            continue

        # 多维度过滤
        if flow_5d < -30000 and drop_20d > -10:
            continue
        if consecutive >= 6 and latest_chg > 5:
            continue
        if drop_20d > 15:
            continue
        if drop_20d < -5 and latest_chg < -3:
            continue

        # 板块共振数据
        c_tier = code_sector_tier.get(code)
        c_ratio_5d, c_ratio_10d = code_sector_ratio.get(code, (None, None))
        health, details = calc_health(score, flow_5d, flow_10d, drop_20d, consecutive, latest_chg, avg_vol_up,
                                      kdj_bonus, rsi, sector_tier=c_tier, ratio_5d=c_ratio_5d, ratio_10d=c_ratio_10d,
                                      daily=daily, code=code, fundamental_data=fundamental_data)
        if health == -999:
            continue
        cand = {
            'code': code, 'name': name, 'score': score,
            'flow_5d': flow_5d, 'flow_10d': flow_10d,
            'drop_20d': drop_20d, 'consecutive': consecutive,
            'latest_chg': latest_chg, 'avg_vol_up': avg_vol_up,
            'rsi': rsi, 'kdj_bonus': kdj_bonus,
            'health': health, 'details': details,
            'sector_tier': c_tier, 'ratio_5d': c_ratio_5d, 'ratio_10d': c_ratio_10d,
            'reasons': pr.get('reasons', []), 'confidence': pr.get('confidence', '')
        }
        # 双池策略：板块票优先，其他兜底
        if c_tier and c_tier <= 99:
            sector_candidates.append(cand)
        else:
            fallback_candidates.append(cand)

    sector_candidates.sort(key=lambda x: x['health'], reverse=True)
    fallback_candidates.sort(key=lambda x: x['health'], reverse=True)

    # 取Top30：板块票优先
    final_picks = sector_candidates[:TARGET_POOL_SIZE]
    deficit_count = TARGET_POOL_SIZE - len(final_picks)
    if deficit_count > 0:
        final_picks += fallback_candidates[:deficit_count]

    sector_count = len(sector_candidates)
    print(f"\n=== 全量选出 {len(final_picks)} 只（板块池{sector_count}只 + 兜底池{len(fallback_candidates)}只）===")

    # 3. 排名展示
    print(f"\n=== STOCKS全量排名 ①资金②逻辑③壁垒④股性 ===")
    for i, c in enumerate(final_picks):
        pos_flag = '✅回调到位' if -20 <= c['drop_20d'] <= -10 else ('💎超跌' if c['drop_20d'] < -20 else '')
        flow_flag = '💰资金流入' if c['flow_5d'] > 0 else ''
        kdj_flag = f'KD+{c["kdj_bonus"]}' if c['kdj_bonus'] > 0 else ''
        vol_flag = '📉缩量' if c['avg_vol_up'] is not None and c['avg_vol_up'] < 0.95 else ''
        mom_flag = ''
        if c['drop_20d'] < -5:
            if c['latest_chg'] > 1:
                mom_flag = '🟢止跌回升'
            elif c['latest_chg'] > 0:
                mom_flag = '🟡企稳'
            elif c['latest_chg'] < -2:
                mom_flag = '🔻跌速快'
        rsi_str = f' RSI{c["rsi"]:.0f}' if c.get('rsi') is not None else ''
        details = c.get('details', {})
        cap = details.get('capital', (0,0,''))[1] if details.get('capital') else 0
        rat = details.get('rationale', (0,'',''))[0] if details.get('rationale') else 0
        moat = details.get('moat', (0,''))[0] if details.get('moat') else 0
        trad = details.get('trading', 0)
        funnel = f' ①{cap:.0f}②{rat:.0f}③{moat:.0f}④{trad:.0f}'
        rat_type = details.get('rationale', (0,'',''))[1] if details.get('rationale') else ''
        rat_label = f' [{rat_type}]' if rat_type else ''
        c_tier_label = code_sector_tier.get(c['code'])
        c_ratio_5d, _ = code_sector_ratio.get(c['code'], (None, None))
        sector_flag = f' 🏭T{c_tier_label}' if c_tier_label else ''
        ratio_flag = f' 💹主力{c_ratio_5d:+.1f}%' if c_ratio_5d else ''
        print(f"  #{i+1:2d} {c['health']:5.1f} | {c['name']:6s} | 评分{c['score']:.1f} 资金{c['flow_5d']:+.0f}万 20日{c['drop_20d']:+.1f}% 当天{c['latest_chg']:+.1f}% 连{c['consecutive']}天{rsi_str} {pos_flag} {flow_flag} {mom_flag} {kdj_flag} {vol_flag}{rat_label}{funnel}{sector_flag}{ratio_flag}")

    # 4. 构造新STOCKS数组
    sector_map = {
        '半导体': ['芯', '微', '集成', '封测', '晶圆'],
        '光通信': ['光', '纤', '缆'],
        '电力设备': ['电', '能源', '电网'],
        '有色金属': ['矿', '金属', '铜', '铝', '锂', '镍'],
        '化工': ['化工', '化学', '材料'],
        '食品饮料': ['食', '酒', '乳', '味'],
        '医药': ['药', '医', '生物', '健康'],
        '汽车': ['汽', '车', '新能源车'],
        '军工': ['航', '军工', '国防', '武器'],
    }
    def guess_sector(name):
        for sec, kws in sector_map.items():
            if any(kw in name for kw in kws):
                return sec
        return '热门标的'

    new_stocks = []
    for c in final_picks:
        reasons_str = '；'.join(c['reasons'][:2]) if c['reasons'] else '全量优选'
        tags = []
        if -20 <= c['drop_20d'] <= -10:
            tags.append('回调到位')
        elif c['drop_20d'] < -20:
            tags.append('超跌')
        if c['flow_5d'] > 10000:
            tags.append('资金流入')
        elif c['flow_5d'] > 0:
            tags.append('资金回流')
        if c['kdj_bonus'] >= 5:
            tags.append('KDJ反转')
        if c['avg_vol_up'] is not None and c['avg_vol_up'] < 0.95:
            tags.append('缩量止跌')
        if c['drop_20d'] < -5:
            if c['latest_chg'] > 1:
                tags.append('止跌回升')
            elif c['latest_chg'] > 0:
                tags.append('企稳')
            elif c['latest_chg'] < -2:
                tags.append('跌速偏快')
        if c.get('sector_tier'):
            tags.append(f'T{c["sector_tier"]}板块共振')
        if c.get('ratio_5d') and c['ratio_5d'] > 2:
            tags.append('主力介入')
        new_stock = {
            'code': to_prefixed_code(c['code']),
            'name': c['name'],
            'sector': guess_sector(c['name']),
            'direction': ','.join(tags[:2]) if tags else '全量选入',
            'pe': 0, 'profitGrowth': 0,
            'reason': f"🔄全量选入（{','.join(tags) if tags else '综合优选'}）：{reasons_str}",
            'roe': 0, 'grossMargin': 0, 'debtRatio': 0,
            'riskFlags': ['🔄全量选入'] + tags,
            'healthScore': c['health'],
            'dailyScore': c['score'],
            'flow5d': c['flow_5d'],
            'drop20d': round(c['drop_20d'], 1),
            'consecutive': c['consecutive'],
            'kdjBonus': c['kdj_bonus'],
            'rsi': round(c['rsi'], 1) if c.get('rsi') is not None else None,
            'sectorTier': c.get('sector_tier'),
            'ratio5d': c.get('ratio_5d'),
            'ratio10d': c.get('ratio_10d'),
            'isNew': True
        }
        new_stocks.append(new_stock)

    # 5. 输出
    output = {
        'update_time': pred.get('update_time', ''),
        'total': len(new_stocks),
        'mode': '全量选股',
        'stocks': new_stocks
    }
    with open(OUTPUT, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n✓ 全量选股完成：共{len(new_stocks)}只")
    print(f"  输出: {OUTPUT}")
    print(f"\n=== 本期全量选股 ===")
    print(f"{', '.join(c['name'] for c in final_picks)}")

    # 7. 池B：启动段专属选股
    print(f"\n{'='*60}")
    print(f"=== 池B：启动段专属选股 ===")
    print(f"{'='*60}")
    # 池B排除池A已有的票，避免重复
    pool_a_codes = set(pure_code(s['code']) for s in new_stocks)
    launch_picks, launch_total = select_launch_pool(all_results, cf_stocks, kdj_stocks, exclude_codes=pool_a_codes)
    launch_stocks = build_launch_stocks(launch_picks)

    print(f"\n启动段候选池: {launch_total}只符合条件, 选入{len(launch_stocks)}只")
    print(f"\n=== 启动段池 TOP {len(launch_picks)} ===")
    for c in launch_picks:
        pos_flag = '✅回调到位' if -20 <= c['drop_20d'] <= -10 else ('💎超跌' if c['drop_20d'] < -20 else '')
        flow_flag = '💰' if c['flow_5d'] > 0 else ''
        mom_flag = '🟢回升' if c['latest_chg'] > 0 else ('🟡企稳' if c['latest_chg'] > -1 else '')
        kdj_flag = f'KD+{c["kdj_bonus"]}' if c['kdj_bonus'] > 0 else ''
        rsi_str = f' RSI{c["rsi"]:.0f}' if c.get('rsi') is not None else ''
        print(f"  {c['launch_health']:5.1f} | {c['name']:6s} | 评分{c['score']:.0f} 资金{c['flow_5d']:+.0f}万 20日{c['drop_20d']:+.1f}% 当天{c['latest_chg']:+.1f}%{rsi_str} {pos_flag} {flow_flag} {mom_flag} {kdj_flag}")

    # 输出启动段池 JSON
    launch_output = {
        'update_time': pred.get('update_time', ''),
        'total': len(launch_stocks),
        'pool': 'launch',
        'stocks': launch_stocks
    }
    LAUNCH_OUTPUT = os.path.join(BASE, 'launch_stocks.json')
    with open(LAUNCH_OUTPUT, 'w', encoding='utf-8') as f:
        json.dump(launch_output, f, ensure_ascii=False, indent=2)
    print(f"\n✓ 启动段池输出: {LAUNCH_OUTPUT}")

    # 8. 自动注入到仪表盘 HTML（同时注入池A和池B）
    print(f"\n=== 注入仪表盘 HTML ===")
    inject_to_html(new_stocks)
    inject_launch_to_html(launch_stocks)

    # 9. 推送到 GitHub Pages
    print(f"\n=== 推送 GitHub Pages ===")
    push_to_github()

    print(f"\n✅ 全流程完成：池A选票 → 池B启动段 → 注入HTML → 推送GitHub")
    print(f"⚠️ CloudStudio 部署需要 WorkBuddy 触发，请回复「部署」更新手机端")

def inject_to_html(new_stocks):
    """把新选票注入到 deploy/index.html 的 const STOCKS = [...] 中"""
    import re as _re2
    with open(INDEX_HTML, 'r', encoding='utf-8') as f:
        html = f.read()

    m = _re2.search(r'(const STOCKS\s*=\s*)(\[.*?\])(;)', html, _re2.DOTALL)
    if not m:
        print("  ❌ 找不到 STOCKS 数组")
        return False

    new_stocks_json = json.dumps(new_stocks, ensure_ascii=False, indent=2)
    new_html = html[:m.start(2)] + new_stocks_json + html[m.end(2):]

    with open(INDEX_HTML, 'w', encoding='utf-8') as f:
        f.write(new_html)

    print(f"  ✅ 池A已注入 {len(new_stocks)} 只票到 STOCKS ({len(new_html)} bytes)")
    return True


def inject_launch_to_html(launch_stocks):
    """把启动段池注入到 deploy/index.html 的 const LAUNCH_STOCKS = [...] 中"""
    import re as _re2
    with open(INDEX_HTML, 'r', encoding='utf-8') as f:
        html = f.read()

    # 查找 LAUNCH_STOCKS 数组
    m = _re2.search(r'(const LAUNCH_STOCKS\s*=\s*)(\[.*?\])(;)', html, _re2.DOTALL)
    if not m:
        # 如果不存在，在 STOCKS 数组后面创建
        print("  ℹ️  LAUNCH_STOCKS 不存在，自动创建")
        # 找到 STOCKS 数组末尾的 ;
        stocks_end = _re2.search(r'(const STOCKS\s*=\s*\[.*?\];)', html, _re2.DOTALL)
        if not stocks_end:
            print("  ❌ 找不到 STOCKS 数组，无法插入 LAUNCH_STOCKS")
            return False
        insert_pos = stocks_end.end()
        launch_json = json.dumps(launch_stocks, ensure_ascii=False, indent=2)
        new_html = html[:insert_pos] + '\n\nconst LAUNCH_STOCKS = ' + launch_json + ';\n' + html[insert_pos:]
    else:
        launch_json = json.dumps(launch_stocks, ensure_ascii=False, indent=2)
        new_html = html[:m.start(2)] + launch_json + html[m.end(2):]

    with open(INDEX_HTML, 'w', encoding='utf-8') as f:
        f.write(new_html)

    print(f"  ✅ 池B已注入 {len(launch_stocks)} 只票到 LAUNCH_STOCKS ({len(new_html)} bytes)")
    return True

def push_to_github():
    """调用 push_to_github.py 推送到 GitHub Pages"""
    import subprocess as _sp
    push_script = os.path.join(BASE, 'push_to_github.py')
    if not os.path.exists(push_script):
        print(f"  ⚠️ 推送脚本不存在: {push_script}")
        return False
    try:
        result = _sp.run(['python3', push_script], capture_output=True, text=True, timeout=60)
        print(result.stdout)
        if result.returncode != 0:
            print(f"  ⚠️ 推送失败:\n{result.stderr}")
            return False
        return True
    except Exception as e:
        print(f"  ⚠️ 推送异常: {e}")
        return False

if __name__ == '__main__':
    main()
