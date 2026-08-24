#!/usr/bin/env python3
"""
起涨前预判扫描器 v2
====================
用新浪API获取真实30分钟/5分钟K线，独立计算多周期KDJ，
在价格还没涨起来时捕捉背离、金叉收敛等领先信号。

数据源：新浪 scale=5(5min) + scale=30(30min) + scale=240(日线) + 东方财富资金流
检测信号：
1. 30分钟KDJ底背离（最强领先信号）   0-35分
2. KDJ低位金叉收敛                    0-25分
3. 缩量止跌 + 资金转正               0-28分
4. MACD柱收敛至零轴                   0-15分
5. 量价背离（价跌量缩→卖盘枯竭）       0-15分

输出：preBreakoutScore / preBreakoutPhase / preBreakoutSignals 注入STOCKS数组
"""
import json, re, subprocess, time, sys, os

HTML_PATH = os.path.join(os.path.dirname(__file__), 'deploy', 'index.html')

def curl_get(url, timeout=10):
    try:
        r = subprocess.run(
            ['curl', '-sL', '--max-time', str(timeout), url,
             '-H', 'User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'],
            capture_output=True, text=True, timeout=timeout + 5
        )
        return r.stdout
    except:
        return ''

def to_secid(code):
    num = code.replace('sh', '').replace('sz', '').replace('bj', '')
    if code.startswith('sh'):
        return '1.' + num
    return '0.' + num

# ============ K线获取 ============

def fetch_sina_kline(code, scale, datalen):
    """新浪K线API — scale=5(5min)/30(30min)/240(日线)"""
    url = (f'https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/'
           f'CN_MarketData.getKLineData?symbol={code}&scale={scale}&datalen={datalen}')
    raw = curl_get(url, 8)
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return [[d['day'], float(d['open']), float(d['high']), float(d['low']),
                 float(d['close']), float(d['volume'])] for d in data]
    except:
        return None

def fetch_eastmoney_flow_daily(secid, lmt=12):
    """东方财富日线资金流（5日/10日累计）"""
    url = (f'https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get?'
           f'secid={secid}&klt=101&lmt={lmt}&fltt=2&fields1=f1,f2,f3,f7&'
           f'fields2=f51,f52,f53,f54,f55,f56,f57')
    raw = curl_get(url, 8)
    if not raw:
        return None, None
    try:
        json_start = raw.find('(')
        json_end = raw.rfind(')')
        if json_start >= 0 and json_end > json_start:
            raw = raw[json_start + 1:json_end]
        data = json.loads(raw)
        if not data or 'data' not in data or 'klines' not in data['data']:
            return None, None
        flows = [float(k.split(',')[1]) for k in data['data']['klines']]
        f5 = sum(flows[-5:]) / 10000 if len(flows) >= 5 else None
        f10 = sum(flows[-10:]) / 10000 if len(flows) >= 10 else None
        return f5, f10
    except:
        return None, None

def fetch_eastmoney_flow_today(secids):
    """东方财富批量当日资金流"""
    url = (f'https://push2.eastmoney.com/api/qt/ulist.np/get?secids={",".join(secids)}'
           f'&fields=f12,f14,f62,f184,f66,f69,f72,f75&fltt=2&invt=2')
    raw = curl_get(url, 10)
    if not raw:
        return {}
    try:
        json_start = raw.find('(')
        json_end = raw.rfind(')')
        if json_start >= 0 and json_end > json_start:
            raw = raw[json_start + 1:json_end]
        data = json.loads(raw)
        results = {}
        if not data or 'data' not in data or not data['data'] or 'diff' not in data['data']:
            return {}
        for item in data['data']['diff']:
            num = str(item.get('f12', ''))
            code = ('sh' + num) if (num.startswith('6') or num.startswith('9')) else ('sz' + num)
            results[code] = {
                'mainNetInflow': item.get('f62'),
                'mainFlowPct': item.get('f184'),
            }
        return results
    except:
        return {}

# ============ 技术指标计算 ============

def calc_kdj_series(klines, n=9):
    """计算KDJ完整序列，返回每根K线的K/D/J值列表"""
    if not klines or len(klines) < n:
        return []
    prev_k, prev_d = 50.0, 50.0
    results = []
    for i in range(len(klines)):
        start = max(0, i - n + 1)
        hn = max(float(kl[2]) for kl in klines[start:i + 1])
        ln = min(float(kl[3]) for kl in klines[start:i + 1])
        c = float(klines[i][4])
        rsv = (c - ln) / (hn - ln) * 100 if hn != ln else 50
        k = 2 / 3 * prev_k + 1 / 3 * rsv
        d = 2 / 3 * prev_d + 1 / 3 * k
        j = 3 * k - 2 * d
        results.append({
            'k': round(k, 2), 'd': round(d, 2), 'j': round(j, 2),
            'close': c, 'low': float(klines[i][3]),
            'high': float(klines[i][2]),
            'vol': float(klines[i][5]),
            'idx': i,
        })
        prev_k, prev_d = k, d
    return results

def calc_macd_histogram(closes, fast=12, slow=26, signal=9):
    """计算MACD柱状图历史，返回最近几根的hist值"""
    if len(closes) < slow + signal:
        return []
    ef = sum(closes[:fast]) / fast
    es = sum(closes[:slow]) / slow
    difs = []
    mf, ms = 2 / (fast + 1), 2 / (slow + 1)
    for i in range(len(closes)):
        if i >= fast:
            ef = (closes[i] - ef) * mf + ef
        if i >= slow:
            es = (closes[i] - es) * ms + es
        if i >= slow - 1:
            difs.append(ef - es)
    if len(difs) < signal:
        return []
    dea = sum(difs[:signal]) / signal
    md = 2 / (signal + 1)
    hist_list = []
    for i in range(signal, len(difs)):
        dea = (difs[i] - dea) * md + dea
        hist = 2 * (difs[i] - dea)
        hist_list.append(round(hist, 4))
    return hist_list

# ============ 领先信号检测 ============

def detect_bottom_divergence(kdj_series, lookback=20):
    """
    30分钟KDJ底背离
    逻辑：找最近两个价格低点。价格低点2 < 低点1，但KDJ-K低点2 > KDJ-K低点1。
    """
    if len(kdj_series) < lookback:
        return {'detected': False, 'score': 0, 'detail': ''}

    recent = kdj_series[-lookback:]
    mid = len(recent) // 2

    # 找两段的最低点
    def find_swing_low(series, start, end):
        low_idx = start
        low_price = series[start]['close']
        for i in range(start, min(end, len(series))):
            if series[i]['close'] < low_price:
                low_price = series[i]['close']
                low_idx = i
        return low_idx, low_price, series[low_idx]['k']

    idx1, price1, k1 = find_swing_low(recent, 0, mid)
    idx2, price2, k2 = find_swing_low(recent, mid, len(recent))

    # 底背离：价格创新低但KDJ不创新低
    if price2 < price1 and k2 > k1:
        # 额外条件：当前KDJ-K不能太高
        if kdj_series[-1]['k'] < 65:
            strength = min(100, int((k2 - k1) / max(1, k1) * 100))
            return {
                'detected': True,
                'score': min(35, 12 + strength // 4),
                'detail': f'底背离: 价{price1:.2f}->{price2:.2f}, K{k1:.1f}->{k2:.1f}'
            }

    # 温和版：价格接近持平但KDJ明显回升
    ratio = abs(price2 - price1) / max(0.01, price1)
    if ratio < 0.03 and k2 > k1 + 5:
        return {
            'detected': True,
            'score': 15,
            'detail': f'类背离: 价持平, K回升{k1:.1f}->{k2:.1f}'
        }

    return {'detected': False, 'score': 0, 'detail': ''}


def detect_golden_cross_converging(kdj_series):
    """
    KDJ低位金叉收敛
    - K < D（尚未金叉）且 K/D均低位 → 即将金叉
    - 刚金叉（上一根K<D，当前K>D，差值小）→ 确认信号
    """
    if len(kdj_series) < 3:
        return {'detected': False, 'score': 0, 'detail': ''}

    curr = kdj_series[-1]
    prev = kdj_series[-2]
    prev2 = kdj_series[-3]

    # 情况1：已经金叉了
    if curr['k'] >= curr['d']:
        # 刚金叉（上一根还没金叉）
        if prev['k'] < prev['d'] and curr['k'] - curr['d'] < 5:
            if curr['k'] < 45:
                return {
                    'detected': True,
                    'score': 20,
                    'detail': f'刚金叉: K={curr["k"]:.1f} D={curr["d"]:.1f}'
                }
        return {'detected': False, 'score': 0, 'detail': ''}

    # 情况2：尚未金叉，K正在逼近D
    if curr['k'] < curr['d']:
        # 都在低位
        if curr['k'] < 40 and curr['d'] < 45:
            # K正在上升
            if curr['k'] > prev['k']:
                ratio = curr['k'] / max(0.1, curr['d'])
                if ratio > 0.80:  # 快碰到了
                    distance = curr['d'] - curr['k']
                    score = 25 if distance < 2 else (20 if distance < 5 else 15)
                    return {
                        'detected': True,
                        'score': score,
                        'detail': f'金叉收敛: K={curr["k"]:.1f}->D={curr["d"]:.1f}, 差{distance:.1f}'
                    }

        # K在低位且连续上升2根（动量积累）
        if curr['k'] < 30 and curr['k'] > prev['k'] > prev2['k']:
            return {
                'detected': True,
                'score': 12,
                'detail': f'低位K连升: {prev2["k"]:.1f}->{prev["k"]:.1f}->{curr["k"]:.1f}'
            }

    return {'detected': False, 'score': 0, 'detail': ''}


def detect_volume_shrink_stabilize(daily_klines):
    """
    日线缩量止跌
    - 最近3-4天持续下跌且量缩
    - 最新一天量比前5日均量小（缩量）
    - 价格跌幅收窄（跌速放缓）
    """
    if not daily_klines or len(daily_klines) < 6:
        return {'detected': False, 'score': 0, 'detail': ''}

    vols = [float(k[5]) for k in daily_klines]
    closes = [float(k[4]) for k in daily_klines]

    # 最近5天的量
    recent_vols = vols[-5:]
    avg_vol_5 = sum(vols[-6:-1]) / 5 if len(vols) >= 6 else 1
    latest_vol = vols[-1]
    vol_ratio = latest_vol / avg_vol_5 if avg_vol_5 > 0 else 1.0

    # 最近3天跌幅
    chg_3d = (closes[-1] - closes[-4]) / closes[-4] * 100 if len(closes) >= 4 else 0
    chg_1d = (closes[-1] - closes[-2]) / closes[-2] * 100 if len(closes) >= 2 else 0

    # 量递减
    vol_shrinking = all(recent_vols[i] <= recent_vols[i - 1] * 1.2 for i in range(1, len(recent_vols)))

    score = 0
    details = []

    # 极度缩量
    if vol_ratio < 0.6:
        score += 10
        details.append(f'极度缩量(量比{vol_ratio:.1f})')
    elif vol_ratio < 0.8:
        score += 7
        details.append(f'缩量(量比{vol_ratio:.1f})')
    elif vol_ratio < 0.95:
        score += 3
        details.append(f'量微缩(比{vol_ratio:.1f})')

    # 跌速放缓
    if chg_3d < -3 and abs(chg_1d) < 1:
        score += 8
        details.append(f'3日跌{chg_3d:.1f}%但今日企稳{chg_1d:+.1f}%')
    elif abs(chg_1d) < 0.5:
        score += 5
        details.append(f'今日止跌{chg_1d:+.1f}%')

    # 量递减
    if vol_shrinking:
        score += 5
        details.append('量逐日递减')

    if score > 0:
        return {'detected': True, 'score': min(score, 23), 'detail': ', '.join(details)}
    return {'detected': False, 'score': 0, 'detail': ''}


def detect_capital_reversal(flow_5d, flow_10d, flow_today):
    """
    资金反转信号
    - 10日流出但5日已转正 → 反转
    - 当日资金流入且5日也流入 → 持续进场
    """
    score = 0
    details = []

    if flow_5d is not None and flow_10d is not None:
        if flow_10d < 0 and flow_5d > 0:
            # 资金反转！
            reversal_ratio = abs(flow_5d / flow_10d) if flow_10d != 0 else 0
            if reversal_ratio > 1.0:
                score += 20
                details.append('超级反转(5日覆盖10日流出)')
            elif reversal_ratio > 0.5:
                score += 17
                details.append('强力资金反转')
            elif reversal_ratio > 0.3:
                score += 14
                details.append('明显资金反转')
            else:
                score += 10
                details.append('弱反转')
        elif flow_5d > 0 and flow_10d > 0:
            # 持续流入
            if flow_5d > 5000:
                score += 12
                details.append('持续大额流入')
            else:
                score += 8
                details.append('持续流入')
        elif flow_5d < 0 and flow_5d > flow_10d * 0.5:
            # 流出减速
            score += 5
            details.append('流出减速')

    # 当日资金确认
    if flow_today is not None and flow_today > 0:
        if flow_today > 5000:
            score += 5
            details.append(f'当日主力+{flow_today/10000:.1f}亿')
        else:
            score += 3
            details.append('当日资金流入')

    if score > 0:
        return {'detected': True, 'score': min(score, 25), 'detail': ', '.join(details)}
    return {'detected': False, 'score': 0, 'detail': ''}


def detect_macd_converging(closes):
    """
    MACD柱收敛至零轴
    - 前几根hist为负但在缩小 → 动量拐头
    - hist从负转正 → 刚金叉
    """
    hist_list = calc_macd_histogram(closes)
    if len(hist_list) < 5:
        return {'detected': False, 'score': 0, 'detail': ''}

    recent = hist_list[-5:]

    # 全为负但在缩小
    if all(h < 0 for h in recent) and recent[-1] > recent[-3]:
        # 收敛中
        if abs(recent[-1]) < abs(recent[-3]) * 0.7:
            return {
                'detected': True,
                'score': 12,
                'detail': f'MACD柱收敛: {recent[-3]:.4f}->{recent[-1]:.4f}'
            }
        elif abs(recent[-1]) < abs(recent[-3]):
            return {
                'detected': True,
                'score': 8,
                'detail': f'MACD柱缩小: {recent[-3]:.4f}->{recent[-1]:.4f}'
            }

    # 刚从负转正
    if recent[-1] > 0 and recent[-2] < 0:
        return {
            'detected': True,
            'score': 15,
            'detail': f'MACD柱翻正: {recent[-2]:.4f}->{recent[-1]:.4f}'
        }

    return {'detected': False, 'score': 0, 'detail': ''}


def detect_price_volume_divergence(daily_klines):
    """
    量价背离
    - 价格下跌但成交量创新低 → 卖盘枯竭
    - 价格下跌但下跌日量小于上涨日量 → 空头力竭
    """
    if not daily_klines or len(daily_klines) < 10:
        return {'detected': False, 'score': 0, 'detail': ''}

    closes = [float(k[4]) for k in daily_klines[-10:]]
    vols = [float(k[5]) for k in daily_klines[-10:]]

    # 价格趋势
    price_down = closes[-1] < closes[0]

    # 量创近10日新低
    vol_new_low = vols[-1] == min(vols)

    # 下跌日均量 vs 上涨日均量
    down_vols = [vols[i] for i in range(1, len(closes)) if closes[i] < closes[i - 1]]
    up_vols = [vols[i] for i in range(1, len(closes)) if closes[i] > closes[i - 1]]

    avg_down = sum(down_vols) / len(down_vols) if down_vols else 0
    avg_up = sum(up_vols) / len(up_vols) if up_vols else 0

    score = 0
    details = []

    if price_down and vol_new_low:
        score += 8
        details.append('价跌量创10日新低')

    if avg_up > 0 and avg_down > 0 and avg_down < avg_up * 0.7:
        score += 7
        details.append(f'下跌日量({avg_down:.0f})<上涨日量({avg_up:.0f})的70%')

    if score > 0:
        return {'detected': True, 'score': min(score, 15), 'detail': ', '.join(details)}
    return {'detected': False, 'score': 0, 'detail': ''}


# ============ 综合分析 ============

def analyze_stock(code, name, daily_klines, flow_5d, flow_10d, flow_today):
    """综合分析单只股票的起涨前信号"""
    result = {
        'code': code,
        'name': name,
        'preBreakoutScore': 0,
        'preBreakoutPhase': 'weak',
        'preBreakoutSignals': [],
    }

    # 3. 获取5分钟和30分钟K线
    kl30 = fetch_sina_kline(code, 30, 300)
    if not kl30 or len(kl30) < 15:
        print(f'  {code} 30min K-line insufficient ({len(kl30) if kl30 else 0} bars)')
        return result

    # 计算30分钟KDJ序列
    kdj30 = calc_kdj_series(kl30, 9)
    if not kdj30:
        return result

    # 5分钟KDJ
    kl5 = fetch_sina_kline(code, 5, 300)
    kdj5 = calc_kdj_series(kl5, 9) if kl5 else None

    # 60分钟KDJ（真实独立）
    kl60 = fetch_sina_kline(code, 60, 300)
    kdj60 = calc_kdj_series(kl60, 9) if kl60 else None

    # 3. 日线数据
    closes = [float(k[4]) for k in daily_klines] if daily_klines else []

    # 4. 检测各项信号
    signals = []
    total_score = 0

    # 4.1 KDJ底背离（权重最高）
    div = detect_bottom_divergence(kdj30, 20)
    if div['detected']:
        signals.append({'signal': '底背离', 'score': div['score'], 'detail': div['detail']})
        total_score += div['score']

    # 4.2 KDJ金叉收敛
    gc = detect_golden_cross_converging(kdj30)
    if gc['detected']:
        signals.append({'signal': '金叉收敛', 'score': gc['score'], 'detail': gc['detail']})
        total_score += gc['score']

    # 4.3 缩量止跌
    if daily_klines:
        vol_shrink = detect_volume_shrink_stabilize(daily_klines)
        if vol_shrink['detected']:
            signals.append({'signal': '缩量止跌', 'score': vol_shrink['score'], 'detail': vol_shrink['detail']})
            total_score += vol_shrink['score']

    # 4.4 资金反转
    cap_rev = detect_capital_reversal(flow_5d, flow_10d, flow_today)
    if cap_rev['detected']:
        signals.append({'signal': '资金反转', 'score': cap_rev['score'], 'detail': cap_rev['detail']})
        total_score += cap_rev['score']

    # 4.5 MACD柱收敛
    if closes and len(closes) >= 35:
        macd_conv = detect_macd_converging(closes)
        if macd_conv['detected']:
            signals.append({'signal': 'MACD收敛', 'score': macd_conv['score'], 'detail': macd_conv['detail']})
            total_score += macd_conv['score']

    # 4.6 量价背离
    if daily_klines:
        pv_div = detect_price_volume_divergence(daily_klines)
        if pv_div['detected']:
            signals.append({'signal': '量价背离', 'score': pv_div['score'], 'detail': pv_div['detail']})
            total_score += pv_div['score']

    # 5. 当前KDJ值（用于判断是否在低位）
    curr_kdj30 = kdj30[-1] if kdj30 else None
    curr_kdj5 = kdj5[-1] if kdj5 else None

    # 6. 综合评分与阶段
    total_score = min(total_score, 100)

    # 阶段判定
    if total_score >= 50:
        phase = 'imminent'  # 即将启动
    elif total_score >= 30:
        phase = 'approaching_breakout'  # 接近突破
    elif total_score >= 15:
        phase = 'building_base'  # 筑底中
    else:
        phase = 'weak'

    result['preBreakoutScore'] = total_score
    result['preBreakoutPhase'] = phase
    result['preBreakoutSignals'] = [{'signal': s['signal'], 'score': s['score'], 'detail': s['detail']}
                                     for s in signals[:5]]
    result['kdj30_k'] = curr_kdj30['k'] if curr_kdj30 else None
    result['kdj30_d'] = curr_kdj30['d'] if curr_kdj30 else None
    result['kdj30_j'] = curr_kdj30['j'] if curr_kdj30 else None
    result['kdj5_k'] = curr_kdj5['k'] if curr_kdj5 else None
    result['kdj5_d'] = curr_kdj5['d'] if curr_kdj5 else None
    result['kdj60_k'] = kdj60[-1]['k'] if kdj60 else None
    result['kdj60_d'] = kdj60[-1]['d'] if kdj60 else None
    result['kdj60_j'] = kdj60[-1]['j'] if kdj60 else None

    return result


# ============ 主流程 ============

def main():
    print('=' * 60)
    print('  起涨前预判扫描器 v2')
    print('  数据源: 新浪scale=5/30 + 东方财富资金流')
    print(f'  扫描时间: {time.strftime("%Y-%m-%d %H:%M:%S")}')
    print('=' * 60)

    # 1. 读取HTML获取股票列表
    with open(HTML_PATH, 'r') as f:
        html = f.read()
    m = re.search(r'const\s+STOCKS\s*=\s*(\[.*?\])\s*;', html, re.S)
    if not m:
        print('ERROR: STOCKS array not found in HTML')
        return
    stocks = json.loads(m.group(1))
    print(f'\nSTOCKS: {len(stocks)} stocks')

    all_codes = [s['code'] for s in stocks]
    secids = [to_secid(c) for c in all_codes]

    # 2. 批量获取当日资金流
    print('\n[1/3] Fetching today capital flow (eastmoney batch)...')
    flow_today_data = fetch_eastmoney_flow_today(secids)
    print(f'  Got {len(flow_today_data)} stocks')

    # 3. 逐只分析
    print(f'\n[2/3] Analyzing {len(all_codes)} stocks...\n')
    results = {}

    for i, code in enumerate(all_codes):
        name = next((s['name'] for s in stocks if s['code'] == code), code)
        print(f'  [{i+1}/{len(all_codes)}] {code} {name}...', end='', flush=True)

        # 获取日线K线
        daily_klines = fetch_sina_kline(code, 240, 60)

        # 获取5日/10日资金流
        f5, f10 = fetch_eastmoney_flow_daily(to_secid(code), 12)

        # 当日资金流
        ft = flow_today_data.get(code, {})
        main_net = ft.get('mainNetInflow')
        try:
            flow_today = float(main_net) / 10000 if main_net is not None else None  # 转万元
        except (TypeError, ValueError):
            flow_today = None  # 东财偶发返回 '-' 等非数值

        # 综合分析
        r = analyze_stock(code, name, daily_klines, f5, f10, flow_today)
        results[code] = r

        sig_count = len(r['preBreakoutSignals'])
        score = r['preBreakoutScore']
        phase = r['preBreakoutPhase']
        print(f' score={score} phase={phase} signals={sig_count}')

        # 速率控制
        time.sleep(0.5)

    # 4. 统计
    print('\n' + '=' * 60)
    imminent = [(c, r) for c, r in results.items() if r['preBreakoutPhase'] == 'imminent']
    approaching = [(c, r) for c, r in results.items() if r['preBreakoutPhase'] == 'approaching_breakout']
    building = [(c, r) for c, r in results.items() if r['preBreakoutPhase'] == 'building_base']

    print(f'  即将启动(>=50): {len(imminent)} 只')
    print(f'  接近突破(>=30): {len(approaching)} 只')
    print(f'  筑底中(>=15): {len(building)} 只')
    print(f'  弱信号(<15): {len(all_codes) - len(imminent) - len(approaching) - len(building)} 只')

    # 5. 注入HTML
    print(f'\n[3/3] Injecting into HTML...')

    # 更新STOCKS数组
    for s in stocks:
        code = s['code']
        if code in results:
            r = results[code]
            s['preBreakoutScore'] = r['preBreakoutScore']
            s['preBreakoutPhase'] = r['preBreakoutPhase']
            s['preBreakoutSignals'] = r['preBreakoutSignals']
            # 用真实5min/30min/60min KDJ替换之前的假数据
            if r.get('kdj30_k') is not None:
                s['kd30_k'] = r['kdj30_k']
                s['kd30_d'] = r['kdj30_d']
                s['kd30_cross'] = '上升' if r['kdj30_k'] > r['kdj30_d'] else '下降'
            if r.get('kdj5_k') is not None:
                s['kd5_k'] = r['kdj5_k']
                s['kd5_d'] = r['kdj5_d']
                s['kd5_cross'] = '上升' if r['kdj5_k'] > r['kdj5_d'] else '下降'
            if r.get('kdj60_k') is not None:
                s['kd60_k'] = r['kdj60_k']
                s['kd60_d'] = r['kdj60_d']
                s['kd60_cross'] = '上升' if r['kdj60_k'] > r['kdj60_d'] else '下降'
            # 资金流数据
            if f5_val := results[code].get('flow5d_wan'):
                s['flow5d_wan'] = f5_val
            if f10_val := results[code].get('flow10d_wan'):
                s['flow10d_wan'] = f10_val

    new_stocks_json = json.dumps(stocks, ensure_ascii=False)
    html = re.sub(
        r'const\s+STOCKS\s*=\s*\[.*?\]\s*;',
        f'const STOCKS = {new_stocks_json};',
        html,
        count=1,
        flags=re.S
    )
    print(f'  STOCKS array updated ({len(stocks)} stocks)')

    # 同步更新 KDJ_FALLBACK（浏览器加载时会用 KDJ_FALLBACK 覆盖 STOCKS，必须保持一致）
    m_fb = re.search(r'(?:var|const|let)?\s*KDJ_FALLBACK\s*=\s*(\{.*?\})\s*;', html, re.S)
    if m_fb:
        try:
            fb = json.loads(m_fb.group(1))
            fb_updated = 0
            for s in stocks:
                code = s['code']
                if code in fb:
                    for key in ['kd5_k', 'kd5_d', 'kd5_cross', 'kd30_k', 'kd30_d', 'kd30_cross',
                                'kd60_k', 'kd60_d', 'kd60_cross']:
                        if s.get(key) is not None:
                            fb[code][key] = s[key]
                            fb_updated += 1
            new_fb_json = json.dumps(fb, ensure_ascii=False, separators=(',', ':'))
            html = html[:m_fb.start(1)] + new_fb_json + html[m_fb.end(1):]
            print(f'  KDJ_FALLBACK synced ({fb_updated} fields updated)')
        except Exception as e:
            print(f'  WARNING: KDJ_FALLBACK sync failed: {e}')
    else:
        print(f'  WARNING: KDJ_FALLBACK not found in HTML')

    # 写回HTML
    with open(HTML_PATH, 'w') as f:
        f.write(html)
    print(f'  HTML written to {HTML_PATH}')

    # 6. 打印TOP信号
    all_scored = sorted(results.items(), key=lambda x: -x[1]['preBreakoutScore'])
    print('\n' + '=' * 80)
    print('  TOP Pre-Breakout Signals')
    print('=' * 80)
    for code, r in all_scored[:15]:
        if r['preBreakoutScore'] > 0:
            print(f'\n  {code} {r["name"]}  [Score: {r["preBreakoutScore"]}]  Phase: {r["preBreakoutPhase"]}')
            if r.get('kdj30_k') is not None:
                print(f'    30min KDJ: K={r["kdj30_k"]:.1f} D={r["kdj30_d"]:.1f} J={r["kdj30_j"]:.1f}')
            if r.get('kdj5_k') is not None:
                print(f'    5min KDJ:  K={r["kdj5_k"]:.1f} D={r["kdj5_d"]:.1f}')
            for sig in r['preBreakoutSignals']:
                print(f'    {sig["signal"]}(+{sig["score"]}): {sig["detail"]}')

    print(f'\nDone. {len(results)} stocks analyzed.')


if __name__ == '__main__':
    main()
