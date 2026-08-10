"""
增强因子计算模块 — 全市场零成本日K线因子
用gap_up_predictor已拉的400天日K线数据计算6个新因子：
1. MACD底背离
2. RSI超卖
3. 布林带下轨
4. BIAS乖离率
5. 地量信号
6. 日KDJ低位上升

设计：软加分，不改变原有100分体系，作为bonus字段输出
"""

# ===== 因子1: MACD底背离 =====
def calc_macd_divergence(closes, lookback=20):
    """
    MACD底背离：价格创新低但MACD柱不创新低
    返回: (是否背离, 强度0-10)
    """
    if len(closes) < 35:
        return False, 0

    # 计算MACD(12,26,9)
    ema12 = _calc_ema(closes, 12)
    ema26 = _calc_ema(closes, 26)
    dif = [e12 - e26 for e12, e26 in zip(ema12, ema26)]
    dea = _calc_ema(dif, 9)
    macd_hist = [(d - e) * 2 for d, e in zip(dif, dea)]

    # 找近lookback天的两个低点
    recent_closes = closes[-lookback:]
    recent_macd = macd_hist[-lookback:]

    if len(recent_closes) < 10:
        return False, 0

    # 找最低点
    min_idx = recent_closes.index(min(recent_closes))
    if min_idx < 5 or min_idx > len(recent_closes) - 5:
        return False, 0

    # 前半段最低点
    first_half = recent_closes[:min_idx]
    if not first_half:
        return False, 0
    first_min_idx = first_half.index(min(first_half))

    # 底背离判断：价格新低但MACD柱不创新低
    price_new_low = recent_closes[min_idx] < recent_closes[first_min_idx]
    macd_higher = recent_macd[min_idx] > recent_macd[first_min_idx]

    if price_new_low and macd_higher:
        # 强度根据MACD柱差值
        diff = recent_macd[min_idx] - recent_macd[first_min_idx]
        strength = min(10, abs(diff) * 100)
        return True, round(strength, 1)

    return False, 0


def _calc_ema(data, period):
    """计算EMA"""
    if len(data) < period:
        return [0] * len(data)
    ema = [sum(data[:period]) / period]
    multiplier = 2 / (period + 1)
    for i in range(period, len(data)):
        ema.append(data[i] * multiplier + ema[-1] * (1 - multiplier))
    # 前面补齐
    return [ema[0]] * (period - 1) + ema


# ===== 因子2: RSI超卖 =====
def calc_rsi_oversold(closes, period=14):
    """
    RSI超卖：RSI<30视为超卖
    返回: (rsi值, 强度0-10)
    """
    if len(closes) < period + 1:
        return 50, 0

    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(0, diff))
        losses.append(max(0, -diff))

    # 取最近period天
    recent_gains = gains[-period:]
    recent_losses = losses[-period:]
    avg_gain = sum(recent_gains) / period
    avg_loss = sum(recent_losses) / period

    if avg_loss == 0:
        rsi = 100
    else:
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))

    # 强度：RSI越低超卖越严重
    if rsi < 20:
        strength = 10
    elif rsi < 25:
        strength = 8
    elif rsi < 30:
        strength = 6
    elif rsi < 35:
        strength = 3
    else:
        strength = 0

    return round(rsi, 1), strength


# ===== 因子3: 布林带下轨 =====
def calc_bollinger_lower(closes, period=20, std_mult=2):
    """
    布林带下轨：收盘价跌破下轨视为超卖
    返回: (偏离度%, 强度0-10)
    """
    if len(closes) < period:
        return 0, 0

    recent = closes[-period:]
    ma = sum(recent) / period
    variance = sum((x - ma) ** 2 for x in recent) / period
    std = variance ** 0.5
    lower = ma - std_mult * std

    latest = closes[-1]
    if ma == 0:
        return 0, 0

    # 偏离度：负值表示在下轨下方
    deviation = (latest - lower) / ma * 100

    if latest < lower:
        # 跌破下轨，超卖
        strength = min(10, abs(deviation) * 2)
        return round(deviation, 1), round(strength, 1)
    elif latest < ma - std:
        # 接近下轨
        strength = 5
        return round(deviation, 1), strength
    else:
        return round(deviation, 1), 0


# ===== 因子4: BIAS乖离率 =====
def calc_bias(closes, period=20):
    """
    BIAS乖离率：BIAS=(收盘价-MA)/MA*100
    BIAS<-12视为超卖
    返回: (bias值, 强度0-10)
    """
    if len(closes) < period:
        return 0, 0

    ma = sum(closes[-period:]) / period
    if ma == 0:
        return 0, 0

    bias = (closes[-1] - ma) / ma * 100

    if bias < -15:
        strength = 10
    elif bias < -12:
        strength = 8
    elif bias < -8:
        strength = 5
    elif bias < -5:
        strength = 3
    else:
        strength = 0

    return round(bias, 1), strength


# ===== 因子5: 地量信号 =====
def calc_volume_dry(volumes, lookback=60, recent_days=5):
    """
    地量信号：近5天有成交量创60日新低
    返回: (是否地量, 强度0-10)
    """
    if len(volumes) < lookback:
        return False, 0

    recent_vol = volumes[-recent_days:]
    baseline = volumes[-lookback:] if len(volumes) >= lookback else volumes
    min_vol_60 = min(baseline)

    # 近5天有地量
    dry_days = sum(1 for v in recent_vol if v <= min_vol_60 * 1.1)
    if dry_days >= 2:
        return True, min(10, dry_days * 3)
    elif dry_days >= 1:
        return True, 5
    else:
        return False, 0


# ===== 因子6: 日KDJ低位上升 =====
def calc_daily_kdj(highs, lows, closes, period=9):
    """
    日KDJ(9,3,3)：K<40且D<40且J<30且K上升
    返回: (k, d, j, 是否命中, 强度0-10)
    """
    if len(closes) < period + 3:
        return 50, 50, 50, False, 0

    rsv_list = []
    for i in range(period - 1, len(closes)):
        window_high = max(highs[i - period + 1:i + 1])
        window_low = min(lows[i - period + 1:i + 1])
        if window_high == window_low:
            rsv = 50
        else:
            rsv = (closes[i] - window_low) / (window_high - window_low) * 100
        rsv_list.append(rsv)

    if len(rsv_list) < 3:
        return 50, 50, 50, False, 0

    # 计算K、D、J
    k_values = [50.0]
    d_values = [50.0]
    for rsv in rsv_list:
        k = 2 / 3 * k_values[-1] + 1 / 3 * rsv
        d = 2 / 3 * d_values[-1] + 1 / 3 * k
        k_values.append(k)
        d_values.append(d)

    k_values = k_values[1:]
    d_values = d_values[1:]
    j_values = [3 * k - 2 * d for k, d in zip(k_values, d_values)]

    k = k_values[-1]
    d = d_values[-1]
    j = j_values[-1]
    k_prev = k_values[-2] if len(k_values) >= 2 else k

    # 低位上升判断
    is_low = k < 40 and d < 40 and j < 30
    is_rising = k > k_prev

    if is_low and is_rising:
        strength = 10
    elif is_low:
        strength = 6
    elif k < 50 and is_rising:
        strength = 3
    else:
        strength = 0

    return round(k, 1), round(d, 1), round(j, 1), (is_low and is_rising), strength


# ===== 因子7: 量比突变 =====
def calc_volume_spike(volumes, lookback=20, recent=5):
    """
    量比突变：近5天量比从<0.5突然变>2，主力介入信号
    返回: (是否突变, 强度0-10)
    """
    if len(volumes) < lookback + recent:
        return False, 0

    avg_vol = sum(volumes[-lookback - recent:-recent]) / lookback
    if avg_vol == 0:
        return False, 0

    recent_vols = volumes[-recent:]
    vol_ratios = [v / avg_vol for v in recent_vols]

    min_ratio = min(vol_ratios)
    max_ratio = max(vol_ratios)

    # 量比突变：最小<0.5且最大>2
    if min_ratio < 0.5 and max_ratio > 2.0:
        return True, 10
    elif min_ratio < 0.7 and max_ratio > 1.8:
        return True, 7
    elif max_ratio > 2.5:
        # 单日放量
        return True, 5
    else:
        return False, 0


# ===== 因子8: 缩量至极致 =====
def calc_volume_shrink(volumes, period=30, lookback=5):
    """
    缩量至极致：5日均量创30日新低
    返回: (是否极致缩量, 强度0-10)
    """
    if len(volumes) < period + lookback:
        return False, 0

    ma5_vol = sum(volumes[-lookback:]) / lookback
    ma30_vol = sum(volumes[-period:]) / period

    if ma30_vol == 0:
        return False, 0

    ratio = ma5_vol / ma30_vol

    if ratio < 0.5:
        return True, 10  # 5日均量不到30日均量一半
    elif ratio < 0.6:
        return True, 7
    elif ratio < 0.7:
        return True, 5
    elif ratio < 0.8:
        return True, 3
    else:
        return False, 0


# ===== 综合计算入口 =====
def calc_enhanced_factors(closes, highs, lows, volumes):
    """
    计算全部8个增强因子
    返回: dict with bonus_total, factors dict
    """
    # 1. MACD底背离
    macd_div, macd_str = calc_macd_divergence(closes)

    # 2. RSI超卖
    rsi, rsi_str = calc_rsi_oversold(closes)

    # 3. 布林带下轨
    boll_dev, boll_str = calc_bollinger_lower(closes)

    # 4. BIAS乖离率
    bias, bias_str = calc_bias(closes)

    # 5. 地量信号
    is_dry, vol_dry_str = calc_volume_dry(volumes)

    # 6. 日KDJ
    k, d, j, kdj_hit, kdj_str = calc_daily_kdj(highs, lows, closes)

    # 7. 量比突变
    vol_spike, spike_str = calc_volume_spike(volumes)

    # 8. 缩量至极致
    is_shrink, shrink_str = calc_volume_shrink(volumes)

    bonus_total = (macd_str + rsi_str + boll_str + bias_str +
                   vol_dry_str + kdj_str + spike_str + shrink_str)

    return {
        'bonus_total': round(bonus_total, 1),
        'factors': {
            'macd_divergence': {'hit': macd_div, 'strength': macd_str},
            'rsi': {'value': rsi, 'strength': rsi_str},
            'bollinger': {'deviation': boll_dev, 'strength': boll_str},
            'bias': {'value': bias, 'strength': bias_str},
            'volume_dry': {'hit': is_dry, 'strength': vol_dry_str},
            'daily_kdj': {'k': k, 'd': d, 'j': j, 'hit': kdj_hit, 'strength': kdj_str},
            'volume_spike': {'hit': vol_spike, 'strength': spike_str},
            'volume_shrink': {'hit': is_shrink, 'strength': shrink_str},
        }
    }


if __name__ == '__main__':
    # 测试
    import json
    import urllib.request

    # 拉一只票测试
    url = 'https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol=sh600885&scale=240&ma=no&datalen=100'
    req = urllib.request.Request(url, headers={'Referer': 'https://finance.sina.com.cn'})
    resp = urllib.request.urlopen(req, timeout=10)
    text = resp.read().decode('utf-8')
    data = json.loads(text)

    closes = [float(d['close']) for d in data]
    highs = [float(d['high']) for d in data]
    lows = [float(d['low']) for d in data]
    volumes = [float(d['volume']) for d in data]

    result = calc_enhanced_factors(closes, highs, lows, volumes)
    print(json.dumps(result, ensure_ascii=False, indent=2))
