#!/usr/bin/env python3
"""
全量选股脚本
每次从全市场重新选出最优30只票，不做增量换血，全量替换。
"""
import json, os, re, sys

BASE = os.path.dirname(__file__)
INDEX_HTML = os.path.join(BASE, 'deploy', 'index.html')
DAVIS_HTML = os.path.join(BASE, 'deploy_davis', 'index.html')  # 🔥 戴维斯双击仪表盘
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

# 🔥 固定票：用户指定永远保留在仪表盘的票，不受选股算法筛选影响
PINNED_STOCKS = [
    {'code': 'sh603067', 'name': '振华股份', 'sector': '化工'},
    {'code': 'sh600105', 'name': '永鼎股份', 'sector': '通信'},
    {'code': 'sh601126', 'name': '四方股份', 'sector': '电力设备'},
    {'code': 'sz000688', 'name': '国城矿业', 'sector': '有色金属'},
]

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

# 换手率最低阈值：低于此值的票不选入（流动性太差）
MIN_TURNOVER = 2.0

def fetch_turnover_batch(codes):
    """
    从腾讯行情接口批量获取换手率。
    codes: ['600522', '002451', ...] 纯数字代码列表
    返回: {'600522': 3.45, '002451': 0.8, ...}  换手率(%)
    """
    import subprocess
    result = {}
    # 腾讯接口一次最多~50只，分批
    batch_size = 40
    for i in range(0, len(codes), batch_size):
        batch = codes[i:i+batch_size]
        prefixed = [to_prefixed_code(c) for c in batch]
        url = 'https://qt.gtimg.cn/q=' + ','.join(prefixed)
        try:
            p = subprocess.run(['curl', '-s', '--max-time', '10', url],
                               capture_output=True, timeout=15)
            text = p.stdout.decode('gbk', errors='replace')
            # 解析: v_sh600522="1~名称~code~...~换手率(fields[38])~..."
            for m in re.finditer(r'v_(s[hz]\d+)="([^"]*)"', text):
                raw_code = m.group(1)
                pure = pure_code(raw_code)
                fields = m.group(2).split('~')
                if len(fields) > 38:
                    try:
                        t = float(fields[38])
                        if t > 0:
                            result[pure] = t
                    except (ValueError, IndexError):
                        pass
        except Exception as e:
            print(f"  ⚠️ 换手率获取失败(batch {i//batch_size+1}): {e}")
    return result

# =====================================================================
# 四层漏斗选股框架
# 逻辑：①资金推动 → ②推动理由 → ③核心壁垒 → ④股性好
# =====================================================================

def calc_capital_quality(flow_5d, flow_10d, daily=None, drop_20d=None, latest_chg=None, avg_vol_up=None, consecutive=None):
    """
    ① 资金层（0-25分）—— 硬性门禁 + 质量评分 + 资金类型判别
    
    硬门禁：10日累计>0 或 最近3日连续流入 → 不过关直接排除
    质量分：连续流入天数 + 资金加速比 + 资金体量
    资金类型：区分增量建仓 vs 存量倒手（出货/分歧），churn类型降权
    
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
    
    # 4. 资金类型判别（增量进场 vs 高位存量倒手）—— 🔥新增
    cap_type, cap_type_bonus, cap_type_diag = classify_capital_type(
        flow_5d, flow_10d, daily, latest_chg, drop_20d, avg_vol_up, consecutive
    )
    score += cap_type_bonus
    
    # 诊断
    parts = []
    if consecutive_inflow >= 3:
        parts.append(f"连{consecutive_inflow}日流入")
    if flow_10d is not None and flow_5d > 0 and flow_10d > 0 and flow_5d / flow_10d > 0.6:
        parts.append("资金加速")
    if ref_flow > 20000:
        parts.append(f"{ref_flow/10000:.1f}亿体量")
    diag = " | ".join(parts) if parts else "资金正流入"
    if cap_type_diag:
        diag += f" | {cap_type_diag}"
    
    return (True, min(score, 25), diag)


def classify_capital_type(flow_5d, flow_10d, daily, latest_chg, drop_20d, avg_vol_up, consecutive):
    """
    资金类型判别 —— 区分增量进场 vs 高位存量倒手 vs 反转试探
    
    核心问题：看到"资金流入"不代表安全，需要判断：
    1. 是增量资金在建仓（低位+持续流入+涨幅温和）？
    2. 还是存量筹码在倒手（高位+放量滞涨/下跌）？→ 可能是出货/分歧
    
    Returns: (type_str, bonus_penalty, diag_str)
        type_str: 'incremental'(增量建仓) | 'churn'(存量倒手/出货) | 'reversal'(反转试探) | 'neutral'(中性)
        bonus_penalty: 建议加分/扣分值（-8 ~ +5）
        diag_str: 诊断描述
    """
    daily = daily or []
    daily_flows = [d.get('flow_wan', 0) for d in daily[-5:]]
    
    # ====== 1. 高位存量倒手/出货检测 ======
    # 逻辑：涨超20%→极端鱼尾；涨超10%→结合量价/资金减速判断
    
    # 高位鱼尾区间（已涨>20%）→ 无论如何都风险极高
    if drop_20d is not None and drop_20d > 20:
        return ('churn', -6, '⚠️高位鱼尾区间(涨>20%)→不追')
    
    if drop_20d is not None and drop_20d > 10:
        # 高位放量下跌 → 明确出货信号（量比正常即可，不需1.2倍以上）
        if latest_chg is not None and latest_chg < -1 and avg_vol_up is not None and avg_vol_up > 1.0:
            return ('churn', -8, '⚠️高位放量下跌→疑似出货')
        # 高位暴跌(>5%) → 无论是否放量都是危险信号
        if latest_chg is not None and latest_chg < -5:
            return ('churn', -6, '⚠️高位暴跌→主力跑路')
        # 高位连涨4天+ → 存量博弈，随时变盘
        if consecutive is not None and consecutive >= 4:
            return ('churn', -5, '⚠️高位连涨→警惕存量博弈')
        # 高位+资金减速(5日<10日的60%) → 主力在撤退
        if flow_10d is not None and flow_10d > 0 and flow_5d < flow_10d * 0.6:
            return ('churn', -3, '⚠️高位资金减速→主力撤退')
    
    # ====== 2. 增量建仓检测 ======
    # 逻辑：低位(已跌>5%) + 资金持续流入 + 5日流入量>10日 → 增量资金在建仓
    if drop_20d is not None and drop_20d < -5 and flow_5d > 0:
        if flow_10d is not None and flow_10d > 0:
            # 资金加速：最近5日流入远超10日均速
            if flow_5d > flow_10d * 1.5:
                return ('incremental', 5, '💰低位增量建仓(资金加速)')
            if flow_5d > flow_10d:
                return ('incremental', 3, '💰低位增量建仓')
        # 10日没有数据但有5日流入+低位 → 也视为增量试探
        if flow_10d is None and flow_5d > 5000:
            return ('incremental', 2, '💰低位增量试探')
    
    # ====== 3. 反转试探检测 ======
    # 逻辑：10日流出→5日流入 → 资金在掉头，关注是否持续
    if flow_10d is not None and flow_10d < 0 and flow_5d > 0:
        # 确认不是一日游：近3日有小额持续流入
        recent_3_in = sum(1 for f in daily_flows[-3:] if f > 0)
        if recent_3_in >= 2:
            return ('reversal', 4, '🔄资金反转试探(有持续性)')
        return ('reversal', 2, '🔄资金反转试探')
    
    # ====== 4. 中性 ======
    # 10日和5日都在流入 → 持续偏多但无特别信号
    if flow_10d is not None and flow_10d > 0 and flow_5d > 0:
        return ('neutral', 1, '')
    
    # 资金博弈不明朗
    return ('neutral', 0, '')


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
    # 🔥 新增：对比3日/5日/10日主力占比趋势，判断主力态度一致性
    if ratio_5d is not None and ratio_10d is not None and ratio_5d > 0 and ratio_10d > 0:
        # 主力占比递增 → 主力在持续加码（最强信号）
        if ratio_5d > ratio_10d * 1.5:
            r_score += 4
            r_diag += " | 主力加速加码"
        elif ratio_5d > ratio_10d:
            r_score += 3
            r_diag += " | 主力持续加码"
        # 主力占比递减 → 主力可能在撤退
        elif ratio_5d < ratio_10d * 0.5:
            r_score -= 2
            r_diag += " | ⚠️主力撤退"
        elif ratio_5d < ratio_10d:
            r_score -= 1
            r_diag += " | 主力减弱"
    elif ratio_5d is not None and ratio_5d > 3:
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
    elif 5 < drop_20d <= 10:
        t_score += 2   # 小涨偏高
    elif 10 < drop_20d <= 15:
        t_score += 1   # 偏高追入
    else:
        t_score += 0   # 高位不追
    
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
    
    # === 高位鱼尾最终风控（🔥新增）===
    # 无论前面技术信号多好，如果已在鱼尾区间，强制降权
    fish_tail_penalty = 0
    if drop_20d > 20:
        # 已涨超20% → 强势鱼尾区间，大幅降权
        fish_tail_penalty = -8
        # 叠加连涨+放量 → 更危险
        if consecutive >= 4 and avg_vol_up is not None and avg_vol_up > 1.1:
            fish_tail_penalty = -12
    elif drop_20d > 15:
        # 已涨超15% → 进入鱼尾预警区间
        fish_tail_penalty = -5
        if consecutive >= 5:
            fish_tail_penalty = -7
    elif drop_20d > 10:
        # 已涨超10% → 偏高，谨慎
        fish_tail_penalty = -2
    
    t_score += fish_tail_penalty
    
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
    passed, capital_score, capital_diag = calc_capital_quality(flow_5d, flow_10d, daily, drop_20d, latest_chg, avg_vol_up, consecutive)
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

# ==================== 启动段四层漏斗 ====================

def calc_launch_capital_quality(flow_5d, flow_10d, daily=None, drop_20d=None, latest_chg=None, avg_vol_up=None, consecutive=None):
    """
    ① 启动段资金门（信号前移版，0-20分）
    
    🔥 核心改进：不等资金转正，抓"流出减速→即将反转"的前兆信号
    
    三条路径：
    A. 10日>0 且 5日>0 → 资金双正，正常评分（路径不变）
    B. 10日>0 但 5日≤0 → 回调中，检查回调深度即可通过
    C. 10日≤0 → 🆕 预启动路径：收集5个信号(回调到位/流出减速/缩量/止跌/流入迹象)
                需≥2个信号通过门禁，不再硬拒绝
    
    churn（高位倒手）仍硬拒绝
    """
    has_10d = (flow_10d is not None)
    if not has_10d:
        return (False, 0, '⛔10日资金缺失')
    
    # 检测近N日连续流入
    consecutive_inflow = 0
    if daily:
        recent_flows = [d.get('flow_wan', d.get('net_flow', 0)) for d in daily[-5:]]
        for f in reversed(recent_flows):
            if f > 0:
                consecutive_inflow += 1
            else:
                break
    
    # 🔥 资金类型判别（先做，churn硬拒绝不变）
    cap_type, cap_type_bonus, cap_type_diag = classify_capital_type(
        flow_5d, flow_10d, daily, latest_chg, drop_20d, avg_vol_up, consecutive
    )
    if cap_type == 'churn':
        return (False, 0, f'⛔{cap_type_diag or "高位存量倒手"}')
    
    score = 0
    diag_parts = []
    
    # ================================================================
    # 路径A：10日>0 且 5日>0 → 资金双正，正常评分
    # ================================================================
    if flow_10d > 0 and (flow_5d or 0) > 0:
        gate_level = '🟢资金双正'
        
        # 连续流入 (0-8)
        if consecutive_inflow >= 5:
            score += 8; diag_parts.append(f'连{consecutive_inflow}日')
        elif consecutive_inflow >= 3:
            score += 6; diag_parts.append(f'连{consecutive_inflow}日')
        elif consecutive_inflow >= 2:
            score += 4; diag_parts.append('连2日')
        elif consecutive_inflow == 1:
            score += 2; diag_parts.append('今日流入')
        
        # 资金加速比 (0-3)
        if flow_10d > 0:
            ratio = flow_5d / flow_10d
            if ratio > 0.8: score += 3
            elif ratio > 0.5: score += 2
            elif ratio > 0.3: score += 1
        
        # 体量 (0-3)
        ref = max(abs(flow_5d or 0), abs(flow_10d))
        if ref > 30000: score += 3
        elif ref > 10000: score += 2
        elif ref > 3000: score += 1
        
        # 类型加分
        if cap_type == 'incremental':
            score += 3; diag_parts.append(cap_type_diag)
        elif cap_type == 'reversal':
            score += 2; diag_parts.append(cap_type_diag)
    
    # ================================================================
    # 路径B：10日>0 但 5日≤0 → 回调中
    # ================================================================
    elif flow_10d > 0:
        gate_level = '🟡回调中'
        if drop_20d is not None and -20 <= drop_20d <= -5:
            score += 5; diag_parts.append('回调到位')
        elif drop_20d is not None and -10 <= drop_20d <= -2:
            score += 3; diag_parts.append('浅回调')
        elif consecutive_inflow >= 2:
            score += 2; diag_parts.append('近转正')
        else:
            score += 1
        
        if consecutive_inflow >= 1: score += 1
        if cap_type == 'reversal': score += 2
    
    # ================================================================
    # 路径C：10日≤0 → 🆕 预启动路径！
    # ================================================================
    else:
        # 数据异常（10日=0且5日≤0）→ 踢
        if flow_10d == 0 and (flow_5d or 0) <= 0:
            return (False, 0, '⛔10日资金异常')
        
        # 收集5个预启动信号
        signals = []
        
        # 信号1：回调到位（drop_20d 在 -20% ~ -2%）
        if drop_20d is not None and -20 <= drop_20d <= -2:
            signals.append('回调到位')
        
        # 信号2：流出减速（|5日流出| / |10日流出| < 0.6）
        if flow_10d < 0 and flow_5d is not None:
            decel = abs(flow_5d / flow_10d) if flow_10d != 0 else 999
            if decel < 0.4:
                signals.append('强减速')
            elif decel < 0.6:
                signals.append('流出减速')
            elif decel < 0.8:
                signals.append('弱减速')
        
        # 信号3：缩量（avg_vol_up < 0.85）
        if avg_vol_up is not None and 0 < avg_vol_up < 0.85:
            signals.append('缩量')
        
        # 信号4：止跌（latest_chg 在 -1% ~ 1%）
        if latest_chg is not None and -1 <= latest_chg <= 1:
            signals.append('止跌')
        elif latest_chg is not None and -2 <= latest_chg < -1:
            signals.append('跌速放缓')
        
        # 信号5：近2日有流入迹象
        if consecutive_inflow >= 1:
            signals.append(f'近{consecutive_inflow}日流入')
        
        # 🔥 门禁：需 ≥ 2 个预启动信号
        if len(signals) < 2:
            return (False, 0, f'⛔预启动不足({len(signals)}/5)')
        
        gate_level = '🔵预启动'
        score += min(len(signals) * 2, 10)
        diag_parts.extend(signals)
        
        # 反转加分
        if cap_type == 'reversal':
            score += 3; diag_parts.append('反转试探')
        elif cap_type == 'incremental':
            score += 2; diag_parts.append('增量迹象')
        
        # 反转幅度加分
        if flow_10d < 0 and (flow_5d or 0) > flow_10d:
            if flow_10d != 0:
                rev = abs((flow_5d - flow_10d) / flow_10d)
                if rev > 0.5: score += 3
                elif rev > 0.3: score += 2
    
    diag_parts.insert(0, gate_level)
    diag = ' | '.join(diag_parts) if diag_parts else '弱信号'
    
    return (True, min(score, 20), diag)


def calc_launch_rationale(code, sector_tier, ratio_5d, ratio_10d, flow_5d, score, sector_persistent=False):
    """
    ② 启动段逻辑层（轻量版，0-10分）

    启动段不要求强板块共振，有即可加分。
    板块持续性验证：一日游板块（非persistent）逻辑分减半。
    """
    r_score = 0
    r_type = "无明确驱动"
    r_diag = ""

    if sector_tier is not None:
        if sector_tier == 1:
            r_score += 7
            r_type = "板块共振T1"
        elif sector_tier == 2:
            r_score += 6
            r_type = "板块共振T2"
        elif sector_tier == 3:
            r_score += 4
            r_type = "板块共振T3"
        elif sector_tier == 99:
            r_score += 2
            r_type = "板块兜底"

        # 持续性惩罚：一日游板块逻辑分减半（最少保留1分）
        if not sector_persistent:
            r_score = max(1, round(r_score * 0.5))
            r_type += "(一日游)"
            r_diag = "板块未持续"

    # 主力占比加分
    if ratio_5d is not None and ratio_5d > 3:
        r_score += 2
    elif ratio_5d is not None and ratio_5d > 1:
        r_score += 1

    # 独立逻辑（不受持续性影响）
    if sector_tier is None:
        if flow_5d > 20000 and score > 55:
            r_score += 6
            r_type = "独立逻辑"
            r_diag = "大资金独立推动"
        elif flow_5d > 10000:
            r_score += 4
            r_type = "独立逻辑"
            r_diag = "资金独立推动"
        elif flow_5d > 3000:
            r_score += 2
            r_type = "资金关注"

    return (min(r_score, 10), r_type, r_diag)


def calc_pre_launch_setup(drop_20d, latest_chg, flow_5d, flow_10d, daily,
                          kdj_bonus=0, rsi=None, avg_vol_up=None):
    """
    预启动信号检测（0-100分）
    
    不要求当天已涨！专门检测"跌到位了但还没涨"的前置信号：
    1. 回调到位          0-25分  — 跌到黄金回调位
    2. 跌速放缓          0-20分  — 跌不动了（最佳预启动信号）
    3. 资金反转          0-30分  — 跌势中资金掉头 ← 核心！
    4. 筑底技术信号       0-25分  — KDJ钝化 + 极度缩量 + RSI超卖
    
    Returns: (score, phase, signals_list)
        phase: 'on_deck'(启动在即,>=65) | 'approaching'(接近启动,>=45) | 'building'(筑底,>=30) | 'weak'(弱)
    """
    score = 0
    signals = []
    
    # ========== 1. 回调到位（0-25）==========
    if -20 <= drop_20d <= -10:
        score += 25
        signals.append('黄金回调位')
    elif -15 <= drop_20d <= -8:
        score += 20
        signals.append('回调到位')
    elif -25 <= drop_20d < -20:
        score += 15
        signals.append('深度回调')
    elif -30 <= drop_20d < -25:
        score += 10
        signals.append('超跌区')
    elif -8 < drop_20d <= -3:
        score += 8
    elif -3 < drop_20d <= 0:
        score += 4
    else:
        score += 1  # 没跌/已涨 → 不是预启动场景
    
    # ========== 2. 跌速放缓（0-20）==========
    # 核心逻辑：预启动不看涨了多少，看"跌不动了"
    if -0.5 <= latest_chg <= 0.5:
        score += 20       # 🎯 止跌企稳 → 最佳预启动信号！
        signals.append('止跌企稳')
    elif 0.5 < latest_chg <= 1.5:
        score += 15       # 微幅反弹（还没飞）
    elif -1 <= latest_chg < -0.5:
        score += 12       # 跌速放缓
        signals.append('跌速放缓')
    elif -2 <= latest_chg < -1:
        score += 6        # 仍在阴跌
    elif latest_chg > 1.5:
        score += 5        # 已启动（预启动不给高分，这是"已涨"票）
        signals.append('已启动')
    else:
        score += 0        # 加速下跌
    
    # ========== 3. 资金反转（0-30）==========
    # 🔥 最关键的前瞻信号：跌市中资金开始掉头
    # 10日数据必检：无10日数据 → 无法判断反转 → 降级
    has_10d = (flow_10d is not None)
    has_daily = daily and len(daily) >= 3
    
    if has_10d:
        if flow_10d < 0 and flow_5d > 0:
            # 🎯 资金反转！跌市中资金掉头 → 最强信号
            reversal_ratio = abs(flow_5d / flow_10d) if flow_10d != 0 else 0
            if reversal_ratio > 1.0:
                score += 30       # 超级反转（5日流入覆盖了10日全部流出）
                signals.append('💰超级反转')
            elif reversal_ratio > 0.5:
                score += 27       # 强力反转
                signals.append('💰强资金反转')
            elif reversal_ratio > 0.3:
                score += 24       # 明显反转
                signals.append('💰资金反转')
            else:
                score += 18       # 弱反转
                signals.append('💰弱反转')
        elif flow_10d < -50000 and flow_5d > 0:
            # 10日大幅流出(>5亿)但5日已转正 → 更确定的反转
            score += 30
            signals.append('💰巨量反转')
        elif flow_5d > 10000 and flow_10d > 0:
            # 10日和5日都在流入且体量大 → 持续强势
            score += 22
            signals.append('💰持续强流入')
        elif flow_5d > 5000:
            score += 18
            signals.append('💰持续流入')
        elif 0 < flow_5d <= 5000:
            score += 12
        elif flow_5d < 0 and flow_5d > flow_10d * 0.5:
            # 流出减速 → 接近反转
            score += 10
            signals.append('流出减缓')
        elif -5000 <= flow_5d <= 0:
            score += 3  # 流出收敛
        elif flow_10d < 0 and flow_5d < flow_10d:
            # 5日比10日流出更多 → 还在恶化 → 不加分
            score += 0
    else:
        # ⚠️ 无10日数据 → 无法判断反转 → 降级
        if flow_5d > 5000:
            score += 12  # 只有5日数据，只能给基础分
            signals.append('⚠️缺10日数据')
        elif flow_5d > 0:
            score += 8
        else:
            score += 2
    
    # 连续小额试探（缩量小买）
    if has_daily:
        recent = daily[-3:]
        small_probe = sum(1 for d in recent if 0 < d.get('net_flow', 0) < 5000)
        if small_probe >= 2:
            score += 5
            signals.append('持续试探')
    
    # ========== 4. 筑底技术信号（0-25）==========
    tech = 0
    
    # KDJ 底部钝化
    if kdj_bonus >= 3:
        tech += 8
    elif kdj_bonus >= 1:
        tech += 4
    
    # 极度缩量 → 卖盘枯竭，快变盘了
    if avg_vol_up is not None:
        if avg_vol_up < 0.7:
            tech += 10
            signals.append('极度缩量')
        elif avg_vol_up < 0.85:
            tech += 7
            signals.append('缩量筑底')
        elif avg_vol_up < 0.95:
            tech += 3
    
    # RSI 超卖
    if rsi is not None:
        if rsi < 25:
            tech += 7
            signals.append('RSI深度超卖')
        elif rsi < 35:
            tech += 5
            signals.append('RSI超卖')
        elif rsi < 45:
            tech += 2
    
    score += min(tech, 25)
    
    # 确定阶段
    score = min(score, 100)
    if score >= 65:
        phase = 'on_deck'
    elif score >= 45:
        phase = 'approaching'
    elif score >= 30:
        phase = 'building'
    else:
        phase = 'weak'
    
    return score, phase, signals


def calc_launch_trading_quality(drop_20d, latest_chg, consecutive, avg_vol_up, kdj_bonus=0, rsi=None):
    """
    ④ 启动段股性层（重仓版，0-55分）
    
    位置(22) + 跌速(15) + 技术(18) = 55
    改动要点：跌速评分不再偏袒已涨票，止跌企稳比"已涨2%"更高分
    """
    t_score = 0
    
    # === 位置评估（0-22分）=== — 核心！回调-10%~-20%最优
    if -20 <= drop_20d <= -10:
        t_score += 22   # 回调到位
    elif -25 <= drop_20d < -20:
        t_score += 18   # 稍深有反弹空间
    elif -10 < drop_20d <= -5:
        t_score += 15   # 浅回调
    elif -30 <= drop_20d < -25:
        t_score += 11   # 深度超跌
    elif -5 < drop_20d <= 0:
        t_score += 8    # 微调
    elif 0 < drop_20d <= 5:
        t_score += 4    # 小涨
    elif drop_20d < -30:
        t_score += 5    # 太深
    else:
        t_score += 1    # 追高不选
    
    # === 跌速判断（0-15分）=== — 重新平衡！
    # 核心改动：不追已涨票，止跌企稳拿最高分
    if -0.5 <= latest_chg <= 1:
        t_score += 15   # 🎯 止跌企稳 → 最高分（还没飞，最佳介入点）
    elif -1 <= latest_chg < -0.5:
        t_score += 12   # 跌速放缓（接近止跌）
    elif 1 < latest_chg <= 2:
        t_score += 10   # 小幅启动（已经有点晚了）
    elif -2 <= latest_chg < -1:
        t_score += 6    # 阴跌
    elif latest_chg > 2:
        t_score += 4    # 已起飞（不追）
    else:
        t_score += 0    # 加速下跌
    
    # 连续上涨惩罚
    if consecutive >= 6:
        t_score -= 5
    elif consecutive >= 4:
        t_score -= 2
    
    # === 技术信号（0-18分）=== — 增强缩量和超卖权重
    tech = 0
    if kdj_bonus >= 7:
        tech += 7
    elif kdj_bonus >= 5:
        tech += 5
    elif kdj_bonus >= 3:
        tech += 3
    
    # 缩量 — 分级强化
    if avg_vol_up is not None and avg_vol_up > 0:
        if avg_vol_up < 0.7:
            tech += 8  # 极度缩量 → 变盘前兆
        elif avg_vol_up < 0.85:
            tech += 6  # 明显缩量
        elif avg_vol_up < 0.95:
            tech += 4  # 轻微缩量
    
    # RSI 超卖
    if rsi is not None:
        if rsi < 25:
            tech += 5  # 深度超卖
        elif rsi < 35:
            tech += 3  # 超卖区
        elif rsi < 45:
            tech += 1  # 偏低
    
    t_score += min(tech, 18)
    
    return min(t_score, 55)


def calc_launch_health(score, flow_5d, flow_10d, drop_20d, consecutive, latest_chg, avg_vol_up,
                        kdj_bonus=0, rsi=None, sector_tier=None, ratio_5d=None, ratio_10d=None,
                        daily=None, code=None, fundamental_data=None, sector_persistent=False):
    """
    启动段四层漏斗健康度（0-100），返回 (-999, None) 表示不合格
    
    与主池 calc_health 的区别：资金门放宽(20)、逻辑轻量(10)、壁垒标准(15)、股性重仓(55)
    
    ┌─────────────────────────────────────────┐
    │ ① 资金门（信号前移版）      0-20分       │
    │ → 10日≤0不再硬踢，需≥2个预启动信号       │
    ├─────────────────────────────────────────┤
    │ ② 推动理由（逻辑轻量）     0-10分         │
    │ → 板块驱动 vs 独立逻辑（弱化）           │
    ├─────────────────────────────────────────┤
    │ ③ 核心壁垒（基本面）       0-15分         │
    │ → 复用 calc_moat，75%缩放                │
    ├─────────────────────────────────────────┤
    │ ④ 股性好（启动段核心）     0-55分         │
    │ → 位置(20)+跌速(20)+技术(15)              │
    └─────────────────────────────────────────┘
    """
    # === 僵尸票检测 ===
    if is_zombie(score, flow_5d):
        return -999.0, None
    
    # ========== ① 资金门（放宽版，0-20）==========
    passed, capital_score, capital_diag = calc_launch_capital_quality(flow_5d, flow_10d, daily, drop_20d, latest_chg, avg_vol_up, consecutive)
    if not passed:
        return -999.0, None
    
    # ========== ② 推动理由（轻量版，0-10）==========
    rationale_score, rationale_type, rationale_diag = calc_launch_rationale(
        code, sector_tier, ratio_5d, ratio_10d, flow_5d, score, sector_persistent
    )
    
    # ========== ③ 核心壁垒（0-15）==========
    moat_full, moat_diag = calc_moat(code, fundamental_data or {})
    moat_score = min(round(moat_full * 0.75, 1), 15)  # 75%缩放
    
    # ========== ④ 股性好（启动段核心，0-55）==========
    trading_score = calc_launch_trading_quality(drop_20d, latest_chg, consecutive, avg_vol_up, kdj_bonus, rsi)
    
    health = capital_score + rationale_score + moat_score + trading_score
    health = round(max(0, min(health, 100)), 1)
    
    details = {
        'capital': (passed, capital_score, capital_diag),
        'rationale': (rationale_score, rationale_type, rationale_diag),
        'moat': (moat_score, moat_diag),
        'trading': trading_score
    }
    return health, details


def select_launch_pool(all_results, cf_stocks, kdj_stocks, fundamental_data=None,
                       code_sector_tier=None, code_sector_ratio=None, code_sector_persistent=None,
                       exclude_codes=set()):
    """
    池B：启动段四层漏斗选股
    从全市场候选池中选 30 只启动段特征最强的票
    
    四层漏斗权重：资金门(20)+逻辑(10)+壁垒(15)+股性(55)
    """
    LAUNCH_COUNT = 30
    candidates = []
    if fundamental_data is None:
        fundamental_data = {}
    if code_sector_tier is None:
        code_sector_tier = {}
    if code_sector_ratio is None:
        code_sector_ratio = {}
    if code_sector_persistent is None:
        code_sector_persistent = {}

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
        flow_10d = cf.get('flow_10d_wan')
        metrics = pr.get('metrics', {})
        drop_20d = metrics.get('drop_20d', 0)
        consecutive = metrics.get('consecutive', 0)
        latest_chg = cf.get('latest_chg', 0)
        avg_vol_up = metrics.get('avg_vol_up', 1.0)
        rsi = metrics.get('rsi', None)
        kdj_bonus = kdj_stocks.get(code, {}).get('kdj_bonus', 0)
        daily = cf.get('daily', [])

        # === 启动段硬性过滤 ===
        # 0. 🔥 10日资金必检：无10日数据或数据异常 → 跳过
        if flow_10d is None:
            continue  # 没有10日资金数据，不可信任
        if flow_10d == 0 and flow_5d <= 0:
            continue  # 10日累计为0且5日也无流入 → 数据异常或停牌
        
        # 1. 僵尸票排除
        if is_zombie(score, flow_5d):
            continue
        # 2. 当天跌幅超1% → 预启动检测（不再硬踢，先算预启动分再决定）
        #    只有加速下跌(<-2)且无资金反转的才踢
        if latest_chg < -2:
            # 检查资金反转信号：5日流入>0 但 10日<0
            if not (flow_10d is not None and flow_10d < 0 and flow_5d > 0):
                continue  # 加速下跌+无资金反转 → 踢
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

        # 板块共振数据
        c_tier = code_sector_tier.get(code)
        c_ratio_5d, c_ratio_10d = code_sector_ratio.get(code, (None, None))
        c_persistent = code_sector_persistent.get(code, False)

        health, details = calc_launch_health(score, flow_5d, flow_10d, drop_20d, consecutive,
                                              latest_chg, avg_vol_up, kdj_bonus, rsi,
                                              sector_tier=c_tier, ratio_5d=c_ratio_5d, ratio_10d=c_ratio_10d,
                                              daily=daily, code=code, fundamental_data=fundamental_data,
                                              sector_persistent=c_persistent)
        
        # 计算预启动分数（不要求当天已涨）
        pre_score, pre_phase, pre_signals = calc_pre_launch_setup(
            drop_20d, latest_chg, flow_5d, flow_10d, daily,
            kdj_bonus, rsi, avg_vol_up
        )
        
        # 放宽门槛：预启动≥60 或 健康度≥32（原35，配合10日必检微调）
        if health == -999:
            continue
        if health < 32 and pre_score < 60:
            continue

        candidates.append({
            'code': code,
            'name': name,
            'score': score,
            'flow_5d': flow_5d,
            'flow_10d': flow_10d,
            'drop_20d': drop_20d,
            'consecutive': consecutive,
            'latest_chg': latest_chg,
            'avg_vol_up': avg_vol_up,
            'rsi': rsi,
            'kdj_bonus': kdj_bonus,
            'launch_health': health,
            'details': details,
            'sector_tier': c_tier,
            'ratio_5d': c_ratio_5d,
            'ratio_10d': c_ratio_10d,
            'reasons': pr.get('reasons', []),
            'confidence': pr.get('confidence', ''),
            'pre_launch_score': pre_score,
            'pre_launch_phase': pre_phase,
            'pre_launch_signals': pre_signals
        })

    # 复合排序：70%健康度 + 30%预启动分，预启动高分的票不被埋没
    candidates.sort(key=lambda x: x['launch_health'] * 0.7 + x.get('pre_launch_score', 0) * 0.3, reverse=True)

    # === 换手率过滤：踢掉换手率 <2% 的 ===
    if candidates:
        launch_codes = [c['code'] for c in candidates]
        launch_turnover = fetch_turnover_batch(launch_codes)
        for c in candidates:
            c['turnover'] = launch_turnover.get(c['code'], None)
        before_count = len(candidates)
        candidates = [c for c in candidates if c['turnover'] is None or c['turnover'] >= MIN_TURNOVER]
        kicked = before_count - len(candidates)
        if kicked > 0:
            print(f"  🚫 启动段换手率<{MIN_TURNOVER}% 被踢: {kicked}只")

    return candidates[:LAUNCH_COUNT], len(candidates)


def get_latest_daily_flow(code, cf_stocks):
    """从 capital_flow.json 获取最近一个交易日的主力净流入（万）"""
    cf = cf_stocks.get(code, {})
    daily = cf.get('daily', [])
    if daily:
        return daily[-1].get('flow_wan', 0)
    return None

def build_launch_stocks(launch_picks, fundamental_data=None, cf_stocks=None):
    """把启动段候选票构造成前端 STOCKS 格式的对象（含四层漏斗数据）"""
    if fundamental_data is None:
        fundamental_data = {}
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
        details = c.get('details', {})
        
        # 基本面数据
        fd = fundamental_data.get(c['code'], {})
        pe_val = fd.get('pe', 0) or 0
        roe_val = fd.get('roe', 0) or 0
        
        # 标签
        tags = []
        # 预启动标签优先
        pre_phase = c.get('pre_launch_phase', '')
        pre_signals = c.get('pre_launch_signals', [])
        if pre_phase == 'on_deck':
            tags.append('⏳启动在即')
        elif pre_phase == 'approaching':
            tags.append('🔍接近启动')
        
        if -20 <= c['drop_20d'] <= -10:
            tags.append('回调到位')
        elif c['drop_20d'] < -20:
            tags.append('超跌')
        # 🔥 10日资金状态标签（启动段专属）
        flow_10d_val = c.get('flow_10d') or 0
        if flow_10d_val < 0 and c['flow_5d'] > 0:
            tags.append('资金反转')
        elif c['flow_5d'] > 10000:
            tags.append('资金流入')
        elif c['flow_5d'] > 0:
            tags.append('资金回流')
        elif flow_10d_val < 0:
            tags.append('10日偏空')
        # 资金反转信号
        if '💰资金反转' in pre_signals or '💰强资金反转' in pre_signals or '💰超级反转' in pre_signals:
            tags.append('资金反转')
        if c['kdj_bonus'] >= 5:
            tags.append('KDJ反转')
        if c['avg_vol_up'] is not None and c['avg_vol_up'] < 0.85:
            tags.append('缩量筑底')
        elif c['avg_vol_up'] is not None and c['avg_vol_up'] < 0.95:
            tags.append('缩量止跌')
        if -0.5 <= c['latest_chg'] <= 0.5:
            tags.append('止跌企稳')
        elif c['latest_chg'] > 0:
            tags.append('企稳')
        elif c['latest_chg'] > -1:
            tags.append('跌势放缓')
        if c.get('sector_tier'):
            tags.append(f'T{c["sector_tier"]}板块共振')
        if c.get('ratio_5d') and c['ratio_5d'] > 2:
            tags.append('主力介入')
        if roe_val > 10:
            tags.append(f'ROE{roe_val:.0f}%')

        # 四层漏斗诊断
        funnel_parts = []
        cap_diag = details.get('capital', (True, 0, ''))[2] if details.get('capital') else ''
        rat_type = details.get('rationale', (0, '', ''))[1] if details.get('rationale') else ''
        moat_diag = details.get('moat', (0, ''))[1] if details.get('moat') else ''
        if cap_diag:
            funnel_parts.append(f'①{cap_diag}')
        if rat_type and rat_type != '无明确驱动':
            funnel_parts.append(f'②{rat_type}')
        if moat_diag and moat_diag != '无基本面数据':
            funnel_parts.append(f'③{moat_diag}')

        # 区分预启动 vs 已启动的阶段标识
        phase_tag = '🚀启动段'
        if pre_phase == 'on_deck':
            phase_tag = '⏳预启动'
        elif pre_phase == 'approaching':
            phase_tag = '🔍待启动'

        s = {
            'code': to_prefixed_code(c['code']),
            'name': c['name'],
            'sector': guess_sector(c['name']),
            'direction': ','.join(tags[:2]) if tags else '启动段',
            'pe': pe_val,
            'profitGrowth': 0,
            'reason': f"{phase_tag}（{','.join(tags[:3]) if tags else '综合'}）{' | '.join(funnel_parts[:2])}｜{reasons_str}",
            'roe': roe_val,
            'grossMargin': 0,
            'debtRatio': fd.get('debt_ratio', 0) or 0,
            'riskFlags': [phase_tag] + tags[:5],
            'healthScore': c['launch_health'],
            'dailyScore': c['score'],
            'flow5d': c['flow_5d'],
            'flow10d': c.get('flow_10d') or 0,  # 🔥 10日资金数据
            'drop20d': round(c['drop_20d'], 1),
            'consecutive': c['consecutive'],
            'kdjBonus': c['kdj_bonus'],
            'rsi': round(c['rsi'], 1) if c.get('rsi') is not None else None,
            'sectorTier': c.get('sector_tier'),
            'ratio5d': c.get('ratio_5d'),
            'ratio10d': c.get('ratio_10d'),
            'preLaunchScore': c.get('pre_launch_score', 0),
            'preLaunchPhase': pre_phase,
            'preLaunchSignals': pre_signals[:3],
            'dailyFlow': get_latest_daily_flow(c['code'], cf_stocks) if cf_stocks else None,  # 🔥 当日主力净流入
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
        _sp.run([sys.executable, sp],
                capture_output=False, timeout=60)
    
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
    code_sector_persistent = {}  # code → bool (板块是否连续≥2天强势)
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
        # 板块持续性：连续≥2天强势才为True
        is_persistent = sd.get('persistent', False)
        for s in sd.get('stocks', []):
            code_sector_tier[s['code']] = tier_num
            code_sector_ratio[s['code']] = (s.get('ratio_5d'), s.get('ratio_10d'))
            code_sector_persistent[s['code']] = is_persistent

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
        if score < 45:
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

    # === 换手率过滤：批量获取候选票换手率，踢掉 <2% 的 ===
    all_candidates_ordered = sector_candidates + fallback_candidates
    all_codes = [c['code'] for c in all_candidates_ordered]
    print(f"\n=== 换手率过滤（阈值≥{MIN_TURNOVER}%）: 批量获取 {len(all_codes)} 只候选票换手率 ===")
    turnover_map = fetch_turnover_batch(all_codes)
    print(f"  获取到换手率: {len(turnover_map)}/{len(all_codes)} 只")

    # 标注换手率并过滤
    low_turnover_kicked = []
    for c in all_candidates_ordered:
        c['turnover'] = turnover_map.get(c['code'], None)
    sector_candidates = [c for c in sector_candidates if c['turnover'] is None or c['turnover'] >= MIN_TURNOVER]
    fallback_candidates = [c for c in fallback_candidates if c['turnover'] is None or c['turnover'] >= MIN_TURNOVER]
    kicked_by_turnover = [c for c in all_candidates_ordered
                          if c['turnover'] is not None and c['turnover'] < MIN_TURNOVER]
    if kicked_by_turnover:
        print(f"  🚫 换手率<{MIN_TURNOVER}% 被踢: {len(kicked_by_turnover)} 只")
        for c in kicked_by_turnover[:10]:
            print(f"     {c['name']:6s} {c['code']} 换手率{c['turnover']:.2f}%")
        if len(kicked_by_turnover) > 10:
            print(f"     ... 及其他 {len(kicked_by_turnover)-10} 只")

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
        f10 = c.get('flow_10d') or 0
        if f10 < 0 and c['flow_5d'] > 0:
            flow_flag = f'💰反转10日{f10/10000:.1f}亿'
        elif c['flow_5d'] > 0:
            flow_flag = f'💰+{c["flow_5d"]/10000:.1f}亿'
        else:
            flow_flag = ''
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
        turnover_flag = f' 换手{c["turnover"]:.1f}%' if c.get('turnover') else ''
        print(f"  #{i+1:2d} {c['health']:5.1f} | {c['name']:6s} | 评分{c['score']:.1f} 资金{c['flow_5d']:+.0f}万 20日{c['drop_20d']:+.1f}% 当天{c['latest_chg']:+.1f}% 连{c['consecutive']}天{rsi_str} {pos_flag} {flow_flag} {mom_flag} {kdj_flag} {vol_flag}{rat_label}{funnel}{sector_flag}{ratio_flag}{turnover_flag}")

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
            'flow10d': c.get('flow_10d') or 0,  # 🔥 10日资金数据
            'drop20d': round(c['drop_20d'], 1),
            'consecutive': c['consecutive'],
            'kdjBonus': c['kdj_bonus'],
            'rsi': round(c['rsi'], 1) if c.get('rsi') is not None else None,
            'sectorTier': c.get('sector_tier'),
            'ratio5d': c.get('ratio_5d'),
            'ratio10d': c.get('ratio_10d'),
            'dailyFlow': get_latest_daily_flow(c['code'], cf_stocks),  # 🔥 当日主力净流入
            'turnover': c.get('turnover'),  # 🔥 换手率(%)
            'isNew': True
        }
        new_stocks.append(new_stock)

    # 🔥 合并固定票：确保用户指定的票永远在仪表盘中
    existing_codes = set(s['code'] for s in new_stocks)
    pinned_added = []
    for ps in PINNED_STOCKS:
        if ps['code'] not in existing_codes:
            # 从 daily_predictions.json 和 capital_flow.json 补数据
            pcode = pure_code(ps['code'])
            pred_item = None
            for item in all_results.values():
                if item.get('code') == pcode:
                    pred_item = item
                    break
            metrics = pred_item.get('metrics', {}) if pred_item else {}
            cf_item = cf_stocks.get(pcode, {}) if cf_stocks else {}

            drop_20d = metrics.get('drop_20d', 0) or 0
            latest_close = metrics.get('latest_close')
            latest_chg = 0
            if latest_close and isinstance(latest_close, (list, tuple)) and len(latest_close) >= 2:
                try:
                    latest_chg = ((latest_close[-1] - latest_close[-2]) / latest_close[-2]) * 100 if latest_close[-2] else 0
                except:
                    pass
            elif metrics.get('latest_chg') is not None:
                latest_chg = metrics.get('latest_chg', 0)

            flow_5d = cf_item.get('flow_5d_wan', 0) or 0
            flow_10d = cf_item.get('flow_10d_wan', 0) or 0
            rsi_val = metrics.get('rsi')
            consecutive = metrics.get('consecutive', 0) or 0
            avg_vol_up = metrics.get('avg_vol_up')

            tags = ['📌固定关注']
            if drop_20d and -20 <= drop_20d <= -10:
                tags.append('回调到位')
            elif drop_20d and drop_20d < -20:
                tags.append('超跌')
            if flow_5d > 10000:
                tags.append('资金流入')
            elif flow_5d > 0:
                tags.append('资金回流')
            if avg_vol_up is not None and avg_vol_up < 0.7:
                tags.append('极度缩量')
            elif avg_vol_up is not None and avg_vol_up < 0.95:
                tags.append('缩量止跌')

            pinned_stock = {
                'code': ps['code'],
                'name': ps['name'],
                'sector': ps['sector'],
                'direction': ','.join(tags[1:3]) if len(tags) > 1 else '固定关注',
                'pe': 0, 'profitGrowth': 0,
                'reason': f"📌用户固定关注票",
                'roe': 0, 'grossMargin': 0, 'debtRatio': 0,
                'riskFlags': tags,
                'healthScore': 0,  # 固定票不参与算法评分，前端实时修正
                'dailyScore': 0,
                'flow5d': flow_5d,
                'flow10d': flow_10d,
                'drop20d': round(drop_20d, 1) if drop_20d else 0,
                'consecutive': consecutive,
                'kdjBonus': 0,
                'rsi': round(rsi_val, 1) if rsi_val is not None else None,
                'sectorTier': None,
                'ratio5d': None,
                'ratio10d': None,
                'dailyFlow': cf_item.get('daily', [{}])[-1].get('flow_wan', 0) if cf_item.get('daily') else 0,
                'turnover': None,
                'isNew': True,
                'isPinned': True
            }
            new_stocks.append(pinned_stock)
            pinned_added.append(ps['name'])
            print(f"  📌 固定票补入: {ps['name']} {ps['code']} (drop20d={drop_20d:.1f}%, flow5d={flow_5d:+.0f}万)")

    if pinned_added:
        print(f"  📌 固定票共补入 {len(pinned_added)} 只: {', '.join(pinned_added)}")

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

    # 7. 池B：启动段专属选股（已停用 — 用户要求只用综合最优池）
    # 以下代码保留但不执行，如需恢复去掉 if False 即可
    if os.environ.get('ENABLE_LAUNCH_POOL') == '1':
        print(f"\n{'='*60}")
        print(f"=== 池B：启动段专属选股 ===")
        print(f"{'='*60}")

        pool_a_codes = set(pure_code(s['code']) for s in new_stocks)
        launch_picks, launch_total = select_launch_pool(all_results, cf_stocks, kdj_stocks,
                                                          fundamental_data=fundamental_data,
                                                          code_sector_tier=code_sector_tier,
                                                          code_sector_ratio=code_sector_ratio,
                                                          code_sector_persistent=code_sector_persistent,
                                                          exclude_codes=pool_a_codes)
        deploy_launch = build_launch_stocks(launch_picks, fundamental_data=fundamental_data, cf_stocks=cf_stocks)

        print(f"\n启动段候选池: {launch_total}只符合条件, 选入{len(deploy_launch)}只")
        pre_on_deck = sum(1 for c in launch_picks if c.get('pre_launch_phase') == 'on_deck')
        pre_approaching = sum(1 for c in launch_picks if c.get('pre_launch_phase') == 'approaching')
        print(f"  ⏳预启动在即: {pre_on_deck}只 | 🔍接近启动: {pre_approaching}只 | 🚀已启动: {len(launch_picks) - pre_on_deck - pre_approaching}只")
        print(f"\n=== 启动段池 TOP {len(launch_picks)} ①资金②逻辑③壁垒④股性 | 预启动 ===")
        for c in launch_picks:
            pos_flag = '✅回调到位' if -20 <= c['drop_20d'] <= -10 else ('💎超跌' if c['drop_20d'] < -20 else '')
            f10 = c['flow_10d'] or 0
            if f10 < 0 and c['flow_5d'] > 0:
                flow_flag = f'💰反转10日{f10/10000:.1f}→5日{c["flow_5d"]/10000:.1f}亿'
            elif f10 > 0:
                flow_flag = f'💰10日+{f10/10000:.1f}亿'
            elif f10 < 0:
                flow_flag = f'🔴10日{f10/10000:.1f}亿'
            else:
                flow_flag = '⚠️10日无数据'
            mom_flag = '🟢已回升' if c['latest_chg'] > 0.5 else ('🟡止跌' if c['latest_chg'] > -0.5 else ('🔴阴跌' if c['latest_chg'] > -2 else ''))
            kdj_flag = f'KD+{c["kdj_bonus"]}' if c['kdj_bonus'] > 0 else ''
            rsi_str = f' RSI{c["rsi"]:.0f}' if c.get('rsi') is not None else ''
            pre_flag = f'⏳{c.get("pre_launch_score",0):.0f}' if c.get('pre_launch_phase') in ('on_deck', 'approaching') else ''
            vol_flag = '📉极度缩量' if (c.get('avg_vol_up') is not None and c['avg_vol_up'] < 0.7) else ('📉缩量' if (c.get('avg_vol_up') is not None and c['avg_vol_up'] < 0.85) else '')
            details = c.get('details', {})
            cap = details.get('capital', (0,0,''))[1] if details.get('capital') else 0
            rat = details.get('rationale', (0,'',''))[0] if details.get('rationale') else 0
            moat = details.get('moat', (0,''))[0] if details.get('moat') else 0
            trad = details.get('trading', 0)
            funnel = f' ①{cap:.0f}②{rat:.0f}③{moat:.0f}④{trad:.0f}'
            rat_type = details.get('rationale', (0,'',''))[1] if details.get('rationale') else ''
            rat_label = f' [{rat_type}]' if rat_type and rat_type != '无明确驱动' else ''
            print(f"  {c['launch_health']:5.1f} | {c['name']:6s}{funnel}{rat_label} | 评分{c['score']:.0f} 资金{c['flow_5d']:+.0f}万 20日{c['drop_20d']:+.1f}% 当天{c['latest_chg']:+.1f}%{rsi_str} {pos_flag} {flow_flag} {mom_flag} {kdj_flag} {vol_flag} {pre_flag}")

        launch_output = {
            'update_time': pred.get('update_time', ''),
            'total': len(deploy_launch),
            'pool': 'launch',
            'stocks': deploy_launch
        }
        LAUNCH_OUTPUT = os.path.join(BASE, 'launch_stocks.json')
        with open(LAUNCH_OUTPUT, 'w', encoding='utf-8') as f:
            json.dump(launch_output, f, ensure_ascii=False, indent=2)
        print(f"\n✓ 启动段池输出: {LAUNCH_OUTPUT}")

        # 8. 自动注入到仪表盘 HTML
        print(f"\n=== 注入仪表盘 HTML ===")
        inject_to_html(new_stocks)
        inject_launch_to_html(deploy_launch)
    else:
        print(f"\nℹ️ 启动段候选池已停用（ENABLE_LAUNCH_POOL 未设为1）")
        # 只注入池A
        print(f"\n=== 注入仪表盘 HTML ===")
        inject_to_html(new_stocks)

    # 9. 自动生成政策双轮驱动板块
    print(f"\n=== 生成政策驱动板块 ===")
    policy_dirs, earnings_verified = build_policy_sectors(sector_filer_data=sector_filter, 
                                                           sector_resonance=sector_data,
                                                           final_picks=final_picks)
    inject_policy_to_html(policy_dirs, earnings_verified)

    # 10. 推送到 GitHub Pages
    print(f"\n=== 推送 GitHub Pages ===")
    push_to_github()

    print(f"\n✅ 全流程完成：池A选票(30+4固定) → 政策板块 → 注入HTML → 推送GitHub")
    print(f"⚠️ CloudStudio 部署需要 WorkBuddy 触发，请回复「部署」更新手机端")


# ==================== 政策双轮驱动数据生成 ====================

# 概念板块 → 政策方向映射
SECTOR_TO_POLICY = {
    '半导体电子': {
        'dualWheel': '半导体设备/材料(供需双驱)',
        'supply': ['半导体设备国产替代', '存储芯片国产化'],
        'demand': ['AI算力基建'],
        'earnings': ['AI算力产业链(业绩落地)', '半导体先进封装(业绩落地)'],
    },
    '电力设备': {
        'dualWheel': '储能电力(供需双驱)',
        'supply': [],
        'demand': ['电力设备(业绩落地)'],
        'earnings': ['电力设备/电网(业绩落地)'],
    },
    '新能源': {
        'dualWheel': '储能电力(供需双驱)',
        'supply': [],
        'demand': ['新能源储能'],
        'earnings': ['储能逆变器(业绩落地)'],
    },
    '计算机/软件': {
        'dualWheel': 'AI算力硬件(供需双驱)',
        'supply': [],
        'demand': ['AI算力基建'],
        'earnings': ['AI算力产业链(业绩落地)'],
    },
    '机械制造': {
        'dualWheel': '',
        'supply': [],
        'demand': ['机器人产业'],
        'earnings': [],
    },
    '化工': {
        'dualWheel': '',
        'supply': ['电子特气自主可控'],
        'demand': [],
        'earnings': ['电子特气/材料(业绩落地)'],
    },
    '通信5G': {
        'dualWheel': '',
        'supply': ['光通信自主可控'],
        'demand': ['AI算力基建'],
        'earnings': [],
    },
    '医药医疗': {
        'dualWheel': '',
        'supply': ['创新药国产替代'],
        'demand': ['医疗基建'],
        'earnings': ['医药创新(业绩落地)'],
    },
    '有色金属': {
        'dualWheel': '',
        'supply': ['战略资源自主'],
        'demand': ['新能源材料'],
        'earnings': [],
    },
    '军工航天': {
        'dualWheel': '',
        'supply': ['军工装备国产化'],
        'demand': ['低空经济'],
        'earnings': [],
    },
}

# 政策方向模板（用于生成 policy 和 logic 文本）
POLICY_TEMPLATES = {
    'dualWheel': {
        '半导体设备/材料(供需双驱)': {
            'policy': '供给端海外管制持续收紧,国内晶圆厂加速扩产+设备采购国产化率提升;需求端AI/存储/汽车芯片需求爆发,设备/材料订单确定性最高',
            'logic': '海外管制倒逼国产替代加速(供给催化)+AI/新能源需求井喷(需求催化),设备厂商在手订单已覆盖未来2年产能,业绩确定性和弹性均最强',
        },
        '储能电力(供需双驱)': {
            'policy': '供给端特别国债定向储能/特高压设备,降本增效;需求端用电负荷创新高+新能源消纳压力倒逼储能装机加速',
            'logic': '政策补贴降低储能度电成本(供给端)+用电高峰+新能源消纳创造刚需(需求端),户储/大储/电网级储能装机量有望翻倍',
        },
        'AI算力硬件(供需双驱)': {
            'policy': '供给端国产AI芯片补贴+大模型备案开放;需求端AI应用爆发→算力需求每6个月翻倍,服务器/光模块/液冷持续放量',
            'logic': '国产AI芯片政策降本(供给)+AI应用落地催化算力需求(需求),算力基建确定性最高、业绩弹性最大',
        },
    },
    'supply': {
        '半导体设备国产替代': {
            'policy': '美国出口管制持续加码,七部委硬科技方案研发费200%加计扣除;国产设备渗透率15%→40%目标,晶圆厂扩产周期持续',
            'logic': '海外管制收紧+政策补贴双轮驱动,国产设备厂商订单确定性提升,从"只能相信"变为"实际交付"',
        },
        '电子特气自主可控': {
            'policy': '商务部6N高纯电子氦禁止出口,反向制约海外半导体;电子特气国产化率加速提升,相关企业业绩已开始兑现',
            'logic': '氦气管制+政策补贴驱动国产替代加速,产品价格上涨已体现在财报,从预期变为已赚钱',
        },
        '存储芯片国产化': {
            'policy': '长鑫存储IPO扩产国产DRAM,存储供应链国产化率20%→50%;海外存储巨头确认需求指数级增长',
            'logic': '长鑫供应链替代空间大,封测/材料/设备全链条受益,国产化加速推进',
        },
        '光通信自主可控': {
            'policy': 'AI算力需求驱动高速光模块/光器件需求爆发,国产光通信龙头全球份额持续提升',
            'logic': '海外AI基建扩产→800G/1.6T光模块需求持续放量,国产厂商技术已追平海外,份额提升确定性高',
        },
        '创新药国产替代': {
            'policy': '集采政策边际缓和+创新药审批提速,国产创新药出海加速',
            'logic': '政策支持创新药研发→临床数据读出+出海放量,龙头管线估值有望重塑',
        },
        '战略资源自主': {
            'policy': '海外关键矿产管制+国内战略收储,稀土/锂/钴等战略资源自主可控提速',
            'logic': '海外供应不确定性推高价格,国内龙头企业产能+成本优势凸显',
        },
        '军工装备国产化': {
            'policy': '国防预算持续增长+装备现代化加速,军工产业链国产化率持续提升',
            'logic': '十四五装备采购放量+国产替代深化,军工电子/材料/发动机环节确定性高',
        },
    },
    'demand': {
        'AI算力基建': {
            'policy': '全国智算中心建设加速,特别国债定向算力设备采购;AI应用从大模型→智能体→机器人,算力需求持续爆发',
            'logic': 'AI应用落地+政策基建刺激,服务器PCB/光模块/液冷/IDC需求持续放量,赛道6-12个月内确定性最强',
        },
        '新能源储能': {
            'policy': '用电负荷创新高+新能源消纳压力→储能装机加速;电池消费税政策落地,户储/大储/电网级储能全面放量',
            'logic': '高温保供+新能源消纳双重需求,逆变器/电池/PCS全链条受益,政策+市场双驱动',
        },
        '机器人产业': {
            'policy': '工信部人形机器人创新发展指导意见,纳入战略性新兴产业;多地设立机器人产业基金,人形机器人从实验室走向量产',
            'logic': 'AI+机器人融合加速,减速器/伺服/视觉零部件千亿市场开启,赛道处于0→1爆发前夜',
        },
        '电力设备(业绩落地)': {
            'policy': '电网集采加速+特高压建设,变压器/开关/电缆中标量同比大幅增长,设备厂商订单饱满',
            'logic': '特高压+配网改造是十四五确定性最强投资方向,设备厂商排产已到明年,业绩确定性强',
        },
        '医疗基建': {
            'policy': '医疗新基建投资加速,基层医疗机构设备升级需求释放',
            'logic': '政策推动医疗资源下沉,影像设备/IVD/病房基建需求放量',
        },
        '新能源材料': {
            'policy': '新能源车+储能双赛道驱动,锂/钴/镍等关键材料需求持续增长',
            'logic': '需求端新能源渗透率提升+供给端海外矿山减产,价格中枢上移利好龙头',
        },
        '低空经济': {
            'policy': '低空经济政策密集出台,无人机物流/城市空中交通商业化加速',
            'logic': '低空经济从概念走向落地,eVTOL/无人机/空管系统产业链受益',
        },
    },
    'earnings': {
        'AI算力产业链(业绩落地)': {
            'status': '中报密集验证期',
            'policy': 'AI服务器出货量每季度翻倍→PCB/光模块/液冷订单爆满,H1利润增速70%-150%,从预期进入业绩兑现期',
            'logic': 'PCB/液冷/光模块订单已转化为营收,中报集中验证,从预期→兑现确定性最强',
        },
        '半导体先进封装(业绩落地)': {
            'status': '中报密集验证期',
            'policy': '存储/算力需求驱动先进封装产能紧缺,龙头封测厂产能利用率接近满产,H1利润大幅增长',
            'logic': 'Chiplet/3D封装是AI芯片必需环节,产能紧缺→涨价+扩产,龙头业绩已进入兑现期',
        },
        '电子特气/材料(业绩落地)': {
            'status': '价格传导期',
            'policy': '氦气管制推高电子特气价格30%+,国产替代从"只能相信"变为"已经赚钱",H1利润增速60%+',
            'logic': '政策催化+产品涨价=业绩双击,电子特气/前驱体/光刻胶企业利润已实际兑现',
        },
        '电力设备/电网(业绩落地)': {
            'status': '招标放量期',
            'policy': '电网集采开标,变压器/特高压中标量同比+50%,特别国债从"计划"→"开标"→"交付"',
            'logic': '特高压/配网设备企业订单已实际落地,营收增长可提前确认,业绩确定性行业第一',
        },
        '储能逆变器(业绩落地)': {
            'status': '海外放量期',
            'policy': '海外户储需求爆发+国内用电负荷创新高→逆变器出货量翻倍,龙头海外渠道已铺开',
            'logic': '国内政策(消费税)+海外需求(户储/大储)双驱动,逆变器/PCS企业营收从底部反转',
        },
        '医药创新(业绩落地)': {
            'status': '订单验证期',
            'policy': '创新药出海加速+国内审批提速,龙头管线进入临床后期/NDA阶段,海外授权收入开始兑现',
            'logic': '从"管线预期"到"授权收入",出海+医保谈判双通道放量,创新药企进入收获期',
        },
    },
}


def build_policy_sectors(sector_filer_data=None, sector_resonance=None, final_picks=None):
    """根据板块分析结果生成政策方向数据"""
    from datetime import datetime as _dt
    
    today_str = _dt.now().strftime('%-m/%-d')
    
    # 读取板块分析
    if not sector_filer_data:
        sector_filer_data = load_json(SECTOR_FILTER_FILE)
    
    all_sectors = sector_filer_data.get('all_sectors', [])
    sector_rank = {s['sector']: s for s in all_sectors}
    
    # 初始化收集容器
    dualWheel = {}      # name → stocks set
    supply = {}         # name → stocks set
    demand = {}         # name → stocks set
    earnings = {}       # name → stocks set
    
    # 从 final_picks 获取股票代码→名称映射
    code_name = {}
    if final_picks:
        for c in final_picks:
            code_name[c['code']] = c['name']
    
    # 从板块分析获取每个板块的股票
    qualified_sectors = sector_filer_data.get('qualified_sectors', {})
    for sector_name, sd in qualified_sectors.items():
        mapping = SECTOR_TO_POLICY.get(sector_name)
        if not mapping:
            continue
        
        stocks = sd.get('stocks', [])
        stock_codes = []
        for s in stocks:
            code = s.get('code', '')
            if code.startswith('00') or code.startswith('60'):
                stock_codes.append(code)
        
        # 添加到各方向
        if mapping['dualWheel']:
            name = mapping['dualWheel']
            if name not in dualWheel:
                dualWheel[name] = set()
            dualWheel[name].update(stock_codes)
        
        for name in mapping['supply']:
            if name not in supply:
                supply[name] = set()
            supply[name].update(stock_codes)
        
        for name in mapping['demand']:
            if name not in demand:
                demand[name] = set()
            demand[name].update(stock_codes)
        
        for name in mapping['earnings']:
            if name not in earnings:
                earnings[name] = set()
            earnings[name].update(stock_codes)
    
    # 如果没有从 sector_filter 拿到足够数据，从 final_picks 的 sector 字段补充
    if final_picks:
        # 按 sector 归类
        sector_stocks = {}
        for c in final_picks:
            code = c['code']
            # 用 sector 猜测
            from sector_pre_filter import SECTOR_KEYWORDS
            for sec_name, mapping in SECTOR_TO_POLICY.items():
                if sec_name not in SECTOR_KEYWORDS:
                    continue
                kws = SECTOR_KEYWORDS.get(sec_name, [])
                if any(kw in c['name'] for kw in kws):
                    if sec_name not in sector_stocks:
                        sector_stocks[sec_name] = set()
                    sector_stocks[sec_name].add(code)
                    break
        
        # 补充到各方向
        for sec_name, codes in sector_stocks.items():
            mapping = SECTOR_TO_POLICY.get(sec_name)
            if not mapping:
                continue
            
            if mapping['dualWheel']:
                name = mapping['dualWheel']
                if name not in dualWheel:
                    dualWheel[name] = set()
                dualWheel[name].update(codes)
            
            for name in mapping['supply']:
                if name not in supply:
                    supply[name] = set()
                supply[name].update(codes)
            
            for name in mapping['demand']:
                if name not in demand:
                    demand[name] = set()
                demand[name].update(codes)
    
    # 构建 POLICY_DIRECTIONS
    def format_stocks(codes_set):
        codes = list(codes_set)[:8]  # 最多8只
        return [f"sz{c}" if c.startswith('00') else f"sh{c}" for c in codes]
    
    def build_item(name, stype):
        templates = POLICY_TEMPLATES.get(stype, {})
        tpl = templates.get(name, {})
        return {
            'name': name,
            'policy': tpl.get('policy', f'{today_str}：板块行情活跃，政策持续催化中'),
            'logic': tpl.get('logic', '政策方向确定性高，关注板块内龙头标的'),
            'stocks': format_stocks(stocks_map.get(name, set()))
        }
    
    policy_dirs = {}
    
    # dualWheel
    stocks_map = dualWheel
    if dualWheel:
        policy_dirs['dualWheel'] = [build_item(name, 'dualWheel') for name in dualWheel]
    else:
        # 兜底：至少保留核心方向
        policy_dirs['dualWheel'] = [
            {'name': '半导体设备/材料(供需双驱)', 'policy': '海外管制收紧+国内扩产加速，双轮驱动确定性最高',
             'logic': '国产替代+AI需求双催化，设备/材料订单确定性极强', 'stocks': []},
            {'name': '储能电力(供需双驱)', 'policy': '特别国债降本+用电高峰刺激，储能装机加速',
             'logic': '政策降本+需求放量，户储/大储/电网储能全面受益', 'stocks': []},
        ]
    
    # supply
    stocks_map = supply
    if supply:
        policy_dirs['supply'] = [build_item(name, 'supply') for name in supply]
    else:
        policy_dirs['supply'] = [
            {'name': '半导体设备国产替代', 'policy': '海外管制加码+政策补贴，国产替代加速',
             'logic': '国产设备渗透率提升，订单确定性增强', 'stocks': []},
        ]
    
    # demand
    stocks_map = demand
    if demand:
        policy_dirs['demand'] = [build_item(name, 'demand') for name in demand]
    else:
        policy_dirs['demand'] = [
            {'name': 'AI算力基建', 'policy': '智算中心加速建设，算力需求持续爆发',
             'logic': 'AI应用落地催化需求，光模块/液冷/服务器放量', 'stocks': []},
        ]
    
    # 业绩落地
    stocks_map = earnings
    if earnings:
        earn_list = []
        for name in earnings:
            item = build_item(name, 'earnings')
            templates = POLICY_TEMPLATES.get('earnings', {})
            tpl = templates.get(name, {})
            item['status'] = tpl.get('status', '订单验证期')
            earn_list.append(item)
        earnings_verified = earn_list
    else:
        earnings_verified = [
            {'name': 'AI算力产业链(业绩落地)', 'status': '中报密集验证期',
             'policy': '订单已转化为营收，中报集中验证', 'logic': '从预期到兑现，确定性最强', 'stocks': []},
            {'name': '电力设备/电网(业绩落地)', 'status': '招标放量期',
             'policy': '集采开标，中标量大幅增长', 'logic': '订单已落地，营收可提前确认', 'stocks': []},
        ]
    
    print(f"  供需双驱: {len(policy_dirs.get('dualWheel',[]))} 方向")
    print(f"  供给端:   {len(policy_dirs.get('supply',[]))} 方向")
    print(f"  需求端:   {len(policy_dirs.get('demand',[]))} 方向")
    print(f"  业绩落地: {len(earnings_verified)} 方向")
    
    return policy_dirs, earnings_verified


def inject_policy_to_html(policy_dirs, earnings_verified):
    """把政策方向数据注入到 deploy/index.html"""
    import re as _re3
    with open(INDEX_HTML, 'r', encoding='utf-8') as f:
        html = f.read()
    
    # 替换 POLICY_DIRECTIONS
    policy_json = json.dumps(policy_dirs, ensure_ascii=False)
    m = _re3.search(r'const POLICY_DIRECTIONS\s*=\s*\{.*?\}\s*;', html, _re3.DOTALL)
    if m:
        html = html[:m.start()] + f'const POLICY_DIRECTIONS = {policy_json};' + html[m.end():]
        print(f"  ✅ POLICY_DIRECTIONS 已更新 ({len(policy_json)} bytes)")
    else:
        print("  ⚠️ 未找到 POLICY_DIRECTIONS，跳过")
    
    # 替换 EARNINGS_VERIFIED
    earnings_json = json.dumps(earnings_verified, ensure_ascii=False)
    m2 = _re3.search(r'const EARNINGS_VERIFIED\s*=\s*\[.*?\]\s*;', html, _re3.DOTALL)
    if m2:
        html = html[:m2.start()] + f'const EARNINGS_VERIFIED = {earnings_json};' + html[m2.end():]
        print(f"  ✅ EARNINGS_VERIFIED 已更新 ({len(earnings_json)} bytes)")
    else:
        print("  ⚠️ 未找到 EARNINGS_VERIFIED，跳过")
    
    with open(INDEX_HTML, 'w', encoding='utf-8') as f:
        f.write(html)
    
    print(f"  ✅ 政策板块已注入 deploy/index.html")
    return True

def inject_to_html(new_stocks):
    """把新选票注入到 deploy/index.html 和 deploy_davis/index.html 的 const STOCKS = [...] 中"""
    import re as _re2
    new_stocks_json = json.dumps(new_stocks, ensure_ascii=False, indent=2)
    
    # 注入 deploy/index.html
    with open(INDEX_HTML, 'r', encoding='utf-8') as f:
        html = f.read()
    m = _re2.search(r'(const STOCKS\s*=\s*)(\[.*?\])(;)', html, _re2.DOTALL)
    if not m:
        print("  ❌ deploy/index.html 找不到 STOCKS 数组")
    else:
        new_html = html[:m.start(2)] + new_stocks_json + html[m.end(2):]
        with open(INDEX_HTML, 'w', encoding='utf-8') as f:
            f.write(new_html)
        print(f"  ✅ 池A已注入 {len(new_stocks)} 只票到 deploy/index.html STOCKS ({len(new_html)} bytes)")
    
    # 🔥 同时注入 deploy_davis/index.html
    if os.path.exists(DAVIS_HTML):
        with open(DAVIS_HTML, 'r', encoding='utf-8') as f:
            davis_html = f.read()
        m2 = _re2.search(r'(const STOCKS\s*=\s*)(\[.*?\])(;)', davis_html, _re2.DOTALL)
        if not m2:
            print("  ⚠️ deploy_davis/index.html 找不到 STOCKS 数组，跳过")
        else:
            davis_new = davis_html[:m2.start(2)] + new_stocks_json + davis_html[m2.end(2):]
            with open(DAVIS_HTML, 'w', encoding='utf-8') as f:
                f.write(davis_new)
            print(f"  ✅ 池A已注入 {len(new_stocks)} 只票到 deploy_davis/index.html STOCKS ({len(davis_new)} bytes)")
    else:
        print(f"  ⚠️ deploy_davis/index.html 不存在，跳过")
    
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
    if os.environ.get('SKIP_PUSH'):
        print("  ⏭️ 跳过推送（SKIP_PUSH=1，云端模式由 cloud_pipeline 统一处理）")
        return True
    import subprocess as _sp
    push_script = os.path.join(BASE, 'push_to_github.py')
    if not os.path.exists(push_script):
        print(f"  ⚠️ 推送脚本不存在: {push_script}")
        return False
    try:
        result = _sp.run([sys.executable, push_script], capture_output=True, text=True, timeout=600)
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
