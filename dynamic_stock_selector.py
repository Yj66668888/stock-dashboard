#!/usr/bin/env python3
"""
动态换血选票脚本
对STOCKS数组的30只票算健康度，踢出最差的6只，从全市场选最好的6只补入。
保留原STOCKS基本面信息，新进入的票标注🆕。
"""
import json, os, re

BASE = os.path.dirname(__file__)
INDEX_HTML = os.path.join(BASE, 'deploy', 'index.html')
PRED_FILE = os.path.join(BASE, 'daily_predictions.json')
CF_FILE = os.path.join(BASE, 'capital_flow.json')
SECTOR_FILE = os.path.join(BASE, 'sector_resonance.json')
SECTOR_FILTER_FILE = os.path.join(BASE, 'sector_filter.json')
KDJ_FILE = os.path.join(BASE, 'kdj_factor.json')
OUTPUT = os.path.join(BASE, 'dynamic_stocks.json')

# 每次换血数量
ROTATE_COUNT = 6

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

def calc_health(score, flow_5d, drop_20d, consecutive, latest_chg, avg_vol_up, kdj_bonus=0, rsi=None,
                sector_tier=None, ratio_5d=None, ratio_10d=None):
    """
    综合健康度算法（0-100），返回 -999 表示僵尸票需立即踢出

    八维度平衡评分，偏好"强势板块内+回调到位+资金回流"形态

    1. 基本面评分 18分：高开预测评分兜底
    2. 资金面     18分：5日资金流正负+幅度，回流加分
    3. 位置评估   15分：回调-10%~-20%最优，过深过浅都扣分
    4. 趋势状态   12分：刚启动+温和涨跌最优，追高扣分
    5. 跌速判断   15分：跌势加速→扣分，跌势放缓→加分
    6. 技术信号   10分：KDJ低位反转+缩量止跌+RSI超卖辅助确认
    7. 板块共振    7分：【新增】在强势概念板块内加分
    8. 主力占比    5分：【新增】5日/10日主力资金净占比
    """
    # === 僵尸票检测 ===
    if is_zombie(score, flow_5d):
        return -999.0

    # ========== 1. 基本面评分（0-18）==========
    base = min(score / 70 * 18, 18)

    # ========== 2. 资金面（0-18）==========
    if flow_5d > 30000:
        flow_score = 18
    elif flow_5d > 10000:
        flow_score = 15
    elif flow_5d > 0:
        flow_score = 7 + min(flow_5d / 10000 * 7, 7)
    elif flow_5d > -5000:
        flow_score = 5   # 流出减缓
    elif flow_5d > -20000:
        flow_score = 2   # 轻度流出
    else:
        flow_score = 0   # 大幅流出

    # ========== 3. 位置评估（0-15）==========
    if -20 <= drop_20d <= -10:
        pos_score = 15   # 回调到位（最优区间）
    elif -30 <= drop_20d < -20:
        pos_score = 11   # 稍深但有反弹空间
    elif -10 < drop_20d <= -5:
        pos_score = 10   # 浅回调
    elif -5 < drop_20d <= 0:
        pos_score = 7    # 微调
    elif 0 < drop_20d <= 5:
        pos_score = 4    # 小涨
    elif drop_20d < -30:
        pos_score = 5    # 太深，可能有基本面问题
    else:
        pos_score = 2    # 大涨（追高）

    # ========== 4. 趋势状态（0-12）==========
    if latest_chg > 7:
        trend_score = 0   # 涨停追高
    elif latest_chg > 5:
        trend_score = 2
    elif consecutive >= 6:
        trend_score = 2   # 连涨太多
    elif consecutive >= 4:
        trend_score = 5
    elif consecutive >= 2 and latest_chg < 3:
        trend_score = 10  # 启动确认
    elif latest_chg > 1:
        trend_score = 11  # 温和上涨
    elif latest_chg > -1:
        trend_score = 8   # 横盘
    elif latest_chg > -2:
        trend_score = 5   # 微跌
    elif latest_chg > -3:
        trend_score = 3   # 下跌
    else:
        trend_score = 1   # 中跌以上

    # ========== 5. 跌速判断（0-15）==========
    # 关键：一只票跌了-15%不可怕，可怕的是今天还在加速跌
    momentum_score = 0
    if drop_20d < -5:
        if latest_chg > 3:
            momentum_score = 15  # 强反弹启动！
        elif latest_chg > 1:
            momentum_score = 12  # 止跌回升
        elif latest_chg > 0:
            momentum_score = 9   # 微弱回升，观望
        elif latest_chg > -1:
            momentum_score = 6   # 跌势放缓，可能企稳
        elif latest_chg > -2:
            momentum_score = 3   # 仍在阴跌
        elif latest_chg > -3:
            momentum_score = 1   # 跌速未降
        else:
            momentum_score = 0   # 加速下跌 → 严重扣分
    else:
        if latest_chg > 0:
            momentum_score = 8   # 正常上涨
        else:
            momentum_score = 5   # 正常微调

    # ========== 6. 技术信号（0-10）==========
    tech_score = 0
    if kdj_bonus >= 7:
        tech_score += 5
    elif kdj_bonus >= 5:
        tech_score += 4
    elif kdj_bonus >= 3:
        tech_score += 2

    if avg_vol_up is not None and 0 < avg_vol_up < 0.95:
        tech_score += 3  # 缩量止跌

    if rsi is not None and rsi < 30:
        tech_score += 2

    # ========== 7. 板块共振（0-7）【新增】==========
    # 票在强势概念板块内，享受板块共振加分
    sector_score = 0
    if sector_tier == 1:
        sector_score = 7   # T1: 最强势板块
    elif sector_tier == 2:
        sector_score = 5   # T2: 较强板块
    elif sector_tier == 3:
        sector_score = 3   # T3: 入门板块
    elif sector_tier == 99:
        sector_score = 1   # 兜底板块（微弱共振）

    # ========== 8. 主力资金占比（0-5）【新增】==========
    # 5日/10日主力净流入占成交额比例
    capital_ratio_score = 0
    if ratio_5d is not None:
        if ratio_5d > 5:
            capital_ratio_score += 3
        elif ratio_5d > 2:
            capital_ratio_score += 2
        elif ratio_5d > 0.5:
            capital_ratio_score += 1
        # 负占比不扣分（可能只是主力未介入）
    if ratio_10d is not None:
        if ratio_10d > 5:
            capital_ratio_score += 2
        elif ratio_10d > 2:
            capital_ratio_score += 1
    capital_ratio_score = min(capital_ratio_score, 5)

    health = base + flow_score + pos_score + trend_score + momentum_score + tech_score + sector_score + capital_ratio_score
    return round(max(0, min(health, 100)), 1)

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
    stocks = extract_stocks_from_html()
    pred = load_json(PRED_FILE)
    cf_data = load_json(CF_FILE)
    sector_data = load_json(SECTOR_FILE)
    sector_filter = load_json(SECTOR_FILTER_FILE)

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
    print(f"原STOCKS: {len(stocks)}只, daily_predictions: {len(all_results)}只, 资金流: {len(cf_stocks)}只")
    print(f"板块预筛选: {len(sector_filter.get('qualified_sectors',{}))}个强势板块, 覆盖{sector_qualified_count}只票")
    kdj_stocks = kdj_data.get('stocks', {})
    if kdj_stocks:
        kdj_hits = sum(1 for v in kdj_stocks.values() if v.get('kdj_bonus', 0) > 0)
        print(f"KDJ因子: {len(kdj_stocks)}只已扫描, {kdj_hits}只命中低位上升")

    # 2. 计算STOCKS每只票的综合健康度
    stock_health = []
    zombie_count = 0
    for s in stocks:
        pc = pure_code(s['code'])
        pr = all_results.get(pc, {})
        cf = cf_stocks.get(pc, {})
        metrics = pr.get('metrics', {})
        score = pr.get('total_score', 0)
        flow_5d = cf.get('flow_5d_wan', 0)
        drop_20d = metrics.get('drop_20d', 0)
        consecutive = metrics.get('consecutive', 0)
        latest_chg = cf.get('latest_chg', 0)
        avg_vol_up = metrics.get('avg_vol_up', 1.0)
        rsi = metrics.get('rsi', None)
        kdj_bonus = kdj_stocks.get(pc, {}).get('kdj_bonus', 0)
        # 板块共振数据
        s_tier = code_sector_tier.get(pc)
        s_ratio_5d, s_ratio_10d = code_sector_ratio.get(pc, (None, None))
        health = calc_health(score, flow_5d, drop_20d, consecutive, latest_chg, avg_vol_up, kdj_bonus, rsi,
                             sector_tier=s_tier, ratio_5d=s_ratio_5d, ratio_10d=s_ratio_10d)
        if health == -999:
            zombie_count += 1
        stock_health.append({
            'stock': s,
            'code': pc,
            'score': score,
            'flow_5d': flow_5d,
            'drop_20d': drop_20d,
            'consecutive': consecutive,
            'latest_chg': latest_chg,
            'avg_vol_up': avg_vol_up,
            'kdj_bonus': kdj_bonus,
            'rsi': rsi,
            'health': health,
            'sector_tier': s_tier,
            'ratio_5d': s_ratio_5d,
            'is_zombie': health == -999
        })

    if zombie_count:
        print(f"\n🧟 检测到 {zombie_count} 只僵尸票（数据缺失），将被优先踢出")

    # 按健康度排序（僵尸票 -999 排最前面）
    stock_health.sort(key=lambda x: x['health'])

    print(f"\n=== STOCKS综合健康度排名（低→高）===")
    for sh in stock_health:
        flag = ''
        if sh.get('is_zombie'):
            flag = ' 🧟僵尸票'
        elif -20 <= sh['drop_20d'] <= -10:
            flag = ' ✅回调到位'
        elif sh['drop_20d'] < -20:
            flag = ' 💎超跌'
        if sh['flow_5d'] > 10000:
            flag += ' 💰资金流入'
        elif sh['flow_5d'] < -20000:
            flag += ' ⚠资金流出'
        if sh['consecutive'] >= 4:
            flag += ' ⚠连涨多'
        if sh['latest_chg'] < -2 and sh['drop_20d'] < -5:
            flag += ' 🔻加速跌'
        elif sh['latest_chg'] > 0 and sh['drop_20d'] < -5:
            flag += ' 🟢止跌企稳'
        if sh['kdj_bonus'] >= 5:
            flag += f' 📈KD+{sh["kdj_bonus"]}'
        if sh['avg_vol_up'] is not None and sh['avg_vol_up'] < 0.95:
            flag += ' 📉缩量'
        s_tier = code_sector_tier.get(sh['code'])
        s_ratio_5d, _ = code_sector_ratio.get(sh['code'], (None, None))
        sector_flag = f' 🏭T{s_tier}' if s_tier else ''
        ratio_flag = f' 💹主力{s_ratio_5d:+.1f}%' if s_ratio_5d else ''
        rsi_str = f' RSI{sh.get("rsi","-"):.0f}' if sh.get('rsi') is not None else ''
        print(f"  {sh['health']:5.1f} | {sh['stock']['code']} {sh['stock']['name']:6s} | 评分{sh['score']:4.1f} 资金{sh['flow_5d']:+8.0f}万 20日{sh['drop_20d']:+5.1f}% 当天{sh['latest_chg']:+.1f}% 连{sh['consecutive']}天{rsi_str}{flag}{sector_flag}{ratio_flag}")

    # 3. 踢出健康度最低的ROTATE_COUNT只（白名单票跳过，永不踢出）
    protected_sh = [sh for sh in stock_health if sh['code'] in PROTECTED_CODES]
    kickable_sh = [sh for sh in stock_health if sh['code'] not in PROTECTED_CODES]
    if protected_sh:
        print(f"\n🔒 白名单保护({len(protected_sh)}只): {', '.join(sh['stock']['name'] for sh in protected_sh)}")
    kicked = kickable_sh[:ROTATE_COUNT]
    kept = kickable_sh[ROTATE_COUNT:] + protected_sh
    print(f"\n=== 踢出 {len(kicked)} 只 ===")
    for sh in kicked:
        print(f"  ❌ {sh['stock']['name']} (健康度{sh['health']})")

    # 4. 从全市场选综合健康度最高的ROTATE_COUNT只补入
    # 排除已在kept里的、ST/退市的、已踢出的
    kept_codes = set(sh['code'] for sh in kept)
    kicked_codes = set(sh['code'] for sh in kicked)

    sector_candidates = []
    fallback_candidates = []
    for code, pr in all_results.items():
        if code in kept_codes or code in kicked_codes:
            continue
        # 排除ST/退市（name里带ST/退）
        name = pr.get('name', '')
        if 'ST' in name or '退' in name:
            continue
        # 排除创业板/科创板/北交所（只保留00/60主板）
        if not (code.startswith('00') or code.startswith('60')):
            continue
        score = pr.get('total_score', 0)
        if score < 50:  # 门槛：评分≥50
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

        # 排除数据缺失票（僵尸票）
        if is_zombie(score, flow_5d):
            continue

        # 多维度过滤（不依赖单一维度，但排除明显不适合的）：
        # - 资金大幅流出（<-3亿）且无超跌反弹逻辑 → 排除
        if flow_5d < -30000 and drop_20d > -10:
            continue
        # - 已连续大涨（连涨6天+且当天涨>5%）→ 追高风险极高
        if consecutive >= 6 and latest_chg > 5:
            continue
        # - 20日涨幅>15%（已经涨太多）→ 排除
        if drop_20d > 15:
            continue
        # - 跌势加速：20日跌超5%且今天还在跌超3% → 排除
        if drop_20d < -5 and latest_chg < -3:
            continue

        # 板块共振数据
        c_tier = code_sector_tier.get(code)
        c_ratio_5d, c_ratio_10d = code_sector_ratio.get(code, (None, None))
        health = calc_health(score, flow_5d, drop_20d, consecutive, latest_chg, avg_vol_up, kdj_bonus, rsi,
                             sector_tier=c_tier, ratio_5d=c_ratio_5d, ratio_10d=c_ratio_10d)
        # 二次确认非僵尸
        if health == -999:
            continue
        cand = {
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
            'health': health,
            'sector_tier': c_tier,
            'ratio_5d': c_ratio_5d,
            'ratio_10d': c_ratio_10d,
            'reasons': pr.get('reasons', []),
            'confidence': pr.get('confidence', '')
        }
        # 🎯 双池策略：板块票进精选池，其他进兜底池
        if c_tier and c_tier <= 99:
            sector_candidates.append(cand)
        else:
            fallback_candidates.append(cand)

    # 排序
    sector_candidates.sort(key=lambda x: x['health'], reverse=True)
    fallback_candidates.sort(key=lambda x: x['health'], reverse=True)

    # 补入：板块票优先，不够则全市场兜底
    new_comers = sector_candidates[:ROTATE_COUNT]
    deficit = ROTATE_COUNT - len(new_comers)
    if deficit > 0:
        new_comers += fallback_candidates[:deficit]

    sector_count = len(sector_candidates)
    print(f"\n=== 补入 {len(new_comers)} 只（板块池{sector_count}只 + 兜底池{len(fallback_candidates)}只）===")
    for c in new_comers:
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
        c_tier = code_sector_tier.get(c['code'])
        c_ratio_5d, _ = code_sector_ratio.get(c['code'], (None, None))
        sector_flag = f' 🏭T{c_tier}' if c_tier else ''
        ratio_flag = f' 💹主力{c_ratio_5d:+.1f}%' if c_ratio_5d else ''
        print(f"  ✅ {c['name']:6s} (健康度{c['health']}) 评分{c['score']:.1f} 资金{c['flow_5d']:+.0f}万 20日{c['drop_20d']:+.1f}% 当天{c['latest_chg']:+.1f}% 连{c['consecutive']}天{rsi_str} {pos_flag} {flow_flag} {mom_flag} {kdj_flag} {vol_flag}{sector_flag}{ratio_flag}")

    # 5. 构造新STOCKS数组
    new_stocks = []

    # 保留的票（原样保留基本面）
    for sh in kept:
        s = sh['stock'].copy()
        # 补充健康度信息（用于前端展示）
        s['healthScore'] = sh['health']
        s['dailyScore'] = sh['score']
        s['flow5d'] = sh['flow_5d']
        s['drop20d'] = round(sh['drop_20d'], 1)
        s['consecutive'] = sh['consecutive']
        s['kdjBonus'] = sh['kdj_bonus']
        if sh.get('rsi') is not None:
            s['rsi'] = round(sh['rsi'], 1)
        # 补充板块共振和主力占比数据
        s_tier = code_sector_tier.get(sh['code'])
        s_ratio_5d, s_ratio_10d = code_sector_ratio.get(sh['code'], (None, None))
        if s_tier:
            s['sectorTier'] = s_tier
        if s_ratio_5d is not None:
            s['ratio5d'] = s_ratio_5d
        if s_ratio_10d is not None:
            s['ratio10d'] = s_ratio_10d
        new_stocks.append(s)

    # 新进入的票
    sector_map = {
        # 简单板块映射（根据代码/名称关键词）
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

    for c in new_comers:
        reasons_str = '；'.join(c['reasons'][:2]) if c['reasons'] else '评分驱动'
        # 标注综合特征
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
        # 跌速标签
        if c['drop_20d'] < -5:
            if c['latest_chg'] > 1:
                tags.append('止跌回升')
            elif c['latest_chg'] > 0:
                tags.append('企稳')
            elif c['latest_chg'] < -2:
                tags.append('跌速偏快')
        # 板块共振标签
        if c.get('sector_tier'):
            tags.append(f'T{c["sector_tier"]}板块共振')
        # 主力占比标签
        if c.get('ratio_5d') and c['ratio_5d'] > 2:
            tags.append('主力介入')
        new_stock = {
            'code': to_prefixed_code(c['code']),
            'name': c['name'],
            'sector': guess_sector(c['name']),
            'direction': ','.join(tags[:2]) if tags else '综合选入',
            'pe': 0,
            'profitGrowth': 0,
            'reason': f"🆕动态选入（{','.join(tags) if tags else '综合'}）：{reasons_str}",
            'roe': 0,
            'grossMargin': 0,
            'debtRatio': 0,
            'riskFlags': ['🆕动态选入'] + tags,
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

    # 6. 输出
    output = {
        'update_time': pred.get('update_time', ''),
        'total': len(new_stocks),
        'kicked': [{'code': sh['code'], 'name': sh['stock']['name'], 'health': sh['health']} for sh in kicked],
        'new_comers': [{'code': c['code'], 'name': c['name'], 'health': c['health']} for c in new_comers],
        'stocks': new_stocks
    }

    with open(OUTPUT, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n✓ 动态换血完成：踢出{len(kicked)}只 + 补入{len(new_comers)}只 = {len(new_stocks)}只")
    print(f"  输出: {OUTPUT}")

    # 打印换血摘要
    print(f"\n=== 换血摘要 ===")
    print(f"踢出: {', '.join(sh['stock']['name'] for sh in kicked)}")
    print(f"补入: {', '.join(c['name'] for c in new_comers)}")

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
