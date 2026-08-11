#!/usr/bin/env python3
"""
日线指标预计算脚本 — Python端计算 MACD/均线/趋势/支撑价/30日高开率/板块共振/阶段
注入到 HTML 的 STOCKS / LAUNCH_STOCKS 数组中，避免浏览器端 JSONP 调用失败导致数据缺失。

数据源：新浪财经日线K线API (scale=240) + 东方财富(备用)
计算指标：
  - MACD (DIF/DEA/Histogram → 金叉/死叉/红柱/绿柱)
  - MA(5/10/20/60) + 多头/空头排列
  - 趋势 (5日涨跌幅 + 上升/下降/震荡)
  - 支撑价 (近20日最低价 + 距离%)
  - 30日高开率 (开盘>前收的天数占比)
  - 板块共振 (基于 sectorTier 字段)
  - 阶段 (preBreakoutPhase → preLaunchPhase 映射)
"""

import json, os, re, sys, time, urllib.request, ssl

BASE = os.path.dirname(os.path.abspath(__file__))
HTML_PATH = os.path.join(BASE, 'deploy', 'index.html')

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE

# 新浪财经日线K线API（主数据源，稳定可用）
def get_daily_klines(code, lmt=200):
    """获取日线K线数据，返回解析后的K线dict列表"""
    # 新浪API用 scale=240 表示日线
    url = (f"https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
           f"CN_MarketData.getKLineData?symbol={code}&scale=240&datalen={lmt}")

    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://finance.sina.com.cn/'
        })
        resp = urllib.request.urlopen(req, context=_CTX, timeout=12)
        raw = resp.read().decode('utf-8')
        data = json.loads(raw)
        if not data:
            return []

        # 新浪格式: [{day, open, high, low, close, volume, ma_price5, ...}]
        klines = []
        for d in data:
            klines.append({
                'date': d.get('day', ''),
                'open': float(d.get('open', 0)),
                'close': float(d.get('close', 0)),
                'high': float(d.get('high', 0)),
                'low': float(d.get('low', 0)),
                'volume': float(d.get('volume', 0)),
            })
        return klines
    except Exception as e:
        # 备用：东方财富API
        try:
            if code.startswith('sh'):
                secid = f"1.{code[2:]}"
            elif code.startswith('sz'):
                secid = f"0.{code[2:]}"
            else:
                return []

            url2 = (f"https://push2his.eastmoney.com/api/qt/stock/kline/get?"
                    f"secid={secid}&klt=101&fqt=1&lmt={lmt}"
                    f"&fields1=f1,f2,f3&fields2=f51,f52,f53,f54,f55,f56,f57,f58")
            req2 = urllib.request.Request(url2, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            })
            resp2 = urllib.request.urlopen(req2, context=_CTX, timeout=10)
            data2 = json.loads(resp2.read().decode('utf-8'))
            raw_klines = data2.get('data', {}).get('klines', [])

            klines = []
            for ks in raw_klines:
                parts = ks.split(',')
                klines.append({
                    'date': parts[0],
                    'open': float(parts[1]),
                    'close': float(parts[2]),
                    'high': float(parts[3]),
                    'low': float(parts[4]),
                    'volume': float(parts[5]) if len(parts) > 5 else 0,
                })
            return klines
        except Exception as e2:
            print(f"  [WARN] {code} K线获取失败: {e} / {e2}")
            return []


def calc_macd(klines, short=12, long=26, signal=9):
    """计算MACD指标"""
    if len(klines) < long + signal:
        return None

    closes = [k['close'] for k in klines]

    # EMA
    ema_short = closes[0]
    ema_long = closes[0]
    difs = []

    for i, c in enumerate(closes):
        ema_short = (short - 1) / (short + 1) * ema_short + 2 / (short + 1) * c
        ema_long = (long - 1) / (long + 1) * ema_long + 2 / (long + 1) * c
        difs.append(ema_short - ema_long)

    # DEA (DIF的signal日EMA)
    dea = difs[0]
    deas = []
    for d in difs:
        dea = (signal - 1) / (signal + 1) * dea + 2 / (signal + 1) * d
        deas.append(dea)

    # 当前值
    dif = difs[-1]
    dea_val = deas[-1]
    hist = 2 * (dif - dea_val)

    # 前一日值
    prev_dif = difs[-2] if len(difs) >= 2 else dif
    prev_dea = deas[-2] if len(deas) >= 2 else dea_val

    # 判断金叉/死叉
    cross = ''
    if prev_dif < prev_dea and dif > dea_val:
        cross = '金叉'
    elif prev_dif > prev_dea and dif < dea_val:
        cross = '死叉'
    elif dif > dea_val:
        cross = '金叉'  # 已经在金叉状态
    else:
        cross = '死叉'  # 已经在死叉状态

    # 红柱/绿柱
    bar_type = '红柱' if hist > 0 else '绿柱'

    # 底背离检测：价格新低但DIF不创新低
    bottom_divergence = False
    if len(difs) >= 20:
        recent_low = min(closes[-20:])
        prev_low = min(closes[-40:-20]) if len(closes) >= 40 else recent_low
        recent_dif_low = min(difs[-20:])
        prev_dif_low = min(difs[-40:-20]) if len(difs) >= 40 else recent_dif_low
        if recent_low < prev_low and recent_dif_low > prev_dif_low:
            bottom_divergence = True

    # 生成显示文本
    if cross == '金叉':
        text = f"金叉{'(底背离)' if bottom_divergence else ''}"
    elif cross == '死叉':
        text = f"死叉{'(底背离)' if bottom_divergence else ''}"
    else:
        text = f"{bar_type}"

    return {
        'macd': text,
        'dif': round(dif, 4),
        'dea': round(dea_val, 4),
        'hist': round(hist, 4),
        'cross': cross,
        'bottom_divergence': bottom_divergence,
    }


def calc_ma_full(klines):
    """计算MA(5/10/20/60) + 多头/空头排列 + 30日高开率"""
    if len(klines) < 5:
        return None

    closes = [k['close'] for k in klines]
    opens = [k['open'] for k in klines]

    def ma(n):
        if len(closes) < n:
            return None
        return sum(closes[-n:]) / n

    ma5 = ma(5)
    ma10 = ma(10)
    ma20 = ma(20)
    ma60 = ma(60)

    # 排列判断
    ma_text = ''
    if ma5 and ma10 and ma20:
        if ma5 > ma10 > ma20:
            ma_text = '多头排列'
        elif ma5 < ma10 < ma20:
            ma_text = '空头排列'
        else:
            ma_text = '交叉震荡'

    # 30日高开率
    open_rate_30d = 0
    total = min(30, len(closes) - 1)
    if total > 0:
        count = 0
        for i in range(len(closes) - total, len(closes)):
            if opens[i] > closes[i - 1]:
                count += 1
        open_rate_30d = round(count / total * 100)

    # 全程高开率
    open_rate_total = 0
    total_all = len(closes) - 1
    if total_all > 0:
        count_all = 0
        for i in range(1, len(closes)):
            if opens[i] > closes[i - 1]:
                count_all += 1
        open_rate_total = round(count_all / total_all * 100)

    # 高开率评分
    if open_rate_30d >= 70:
        open_rate_score = 5
    elif open_rate_30d >= 60:
        open_rate_score = 4
    elif open_rate_30d >= 50:
        open_rate_score = 3
    elif open_rate_30d >= 40:
        open_rate_score = 2
    else:
        open_rate_score = 1

    return {
        'ma': ma_text,
        'ma5': round(ma5, 2) if ma5 else None,
        'ma10': round(ma10, 2) if ma10 else None,
        'ma20': round(ma20, 2) if ma20 else None,
        'ma60': round(ma60, 2) if ma60 else None,
        'openRate30d': open_rate_30d,
        'openRateTotal': open_rate_total,
        'openRateScore': open_rate_score,
    }


def calc_trend_daily(klines):
    """计算趋势 + 5日涨跌幅"""
    if len(klines) < 6:
        return None

    closes = [k['close'] for k in klines]
    cur = closes[-1]
    ago5 = closes[-6]
    trend_pct = ((cur - ago5) / ago5 * 100) if ago5 != 0 else 0

    # 趋势判断：基于5日均线方向
    def ma(n):
        if len(closes) < n:
            return 0
        return sum(closes[-n:]) / n

    ma5 = ma(5)
    ma10 = ma(10)
    ma20 = ma(20)

    if ma5 > ma10 > ma20 and trend_pct > 0:
        trend = '上升'
    elif ma5 < ma10 < ma20 and trend_pct < 0:
        trend = '下降'
    elif abs(trend_pct) < 1:
        trend = '震荡'
    elif trend_pct > 0:
        trend = '上升'
    else:
        trend = '下降'

    return {
        'trend': trend,
        'trendPct': round(trend_pct, 1),
    }


def calc_support_daily(klines):
    """计算支撑价（近20日最低价）+ 距离%"""
    if len(klines) < 5:
        return None

    cur_close = klines[-1]['close']
    recent = klines[-min(20, len(klines)):]
    lows = [k['low'] for k in recent]
    support = min(lows)

    # 距离百分比
    support_dist = round((cur_close - support) / support * 100, 1) if support != 0 else 0

    return {
        'supportPrice': round(support, 2),
        'supportDist': support_dist,
    }


def calc_sector_resonance(stock, all_stocks_in_sector=None):
    """计算板块共振（基于 sectorTier + 同板块数量）"""
    tier = stock.get('sectorTier', 0)
    sector = stock.get('sector', '')

    if not sector:
        return None

    # 统计同板块在选股池中的数量
    if all_stocks_in_sector is None:
        all_stocks_in_sector = 0
    same_sector_count = all_stocks_in_sector

    # 基于梯队和同板块数量判断
    if tier == 1:
        return {
            'sectorResonance': '板块启动',
            'sectorResonanceDetail': f'{sector} T1梯队领涨',
        }
    elif tier == 2:
        detail = f'{sector} T2梯队跟进'
        if same_sector_count >= 3:
            detail += f'，池内{same_sector_count}只'
        return {
            'sectorResonance': '部分共振',
            'sectorResonanceDetail': detail,
        }
    elif tier == 3:
        return {
            'sectorResonance': '弱共振',
            'sectorResonanceDetail': f'{sector} T3梯队',
        }
    else:
        # 无梯队信息，基于同板块数量判断
        if same_sector_count >= 4:
            return {
                'sectorResonance': '部分共振',
                'sectorResonanceDetail': f'{sector} 池内{same_sector_count}只聚集',
            }
        elif same_sector_count >= 2:
            return {
                'sectorResonance': '弱共振',
                'sectorResonanceDetail': f'{sector} 池内{same_sector_count}只',
            }
        else:
            return {
                'sectorResonance': '独立走势',
                'sectorResonanceDetail': f'{sector} 个股行情',
            }


def calc_daily_kdj(klines, n=9):
    """计算日线KDJ(9,3,3)，返回当前K/D/J值"""
    if len(klines) < n + 3:
        return None

    highs = [k['high'] for k in klines]
    lows = [k['low'] for k in klines]
    closes = [k['close'] for k in klines]

    rsvs = []
    for i in range(n - 1, len(closes)):
        hh = max(highs[i - n + 1:i + 1])
        ll = min(lows[i - n + 1:i + 1])
        if hh == ll:
            rsv = 50
        else:
            rsv = (closes[i] - ll) / (hh - ll) * 100
        rsvs.append(rsv)

    # KDJ递推
    k = 50
    d = 50
    for rsv in rsvs:
        k = 2 / 3 * k + 1 / 3 * rsv
        d = 2 / 3 * d + 1 / 3 * k
    j = 3 * k - 2 * d

    return {'k': round(k, 1), 'd': round(d, 1), 'j': round(j, 1)}


def map_phase(stock, klines=None):
    """
    智能阶段判定 — 基于实际K线数据(涨幅+KD水位) + preBreakout信号综合判断
    
    阶段体系（前端phaseMap）:
      on_deck         → ⏳启动在即    (预启动信号极强，即将爆发)
      approaching     → 🔍接近启动    (预启动信号较强，接近突破)
      building        → 🚀启动段      (低位筑底/启动初期)
      accelerating    → ⚡加速段      (已经启动且正在加速上涨)
      tail            → 📉回落段      (涨幅过大+KD高位，鱼尾阶段)
      overbought_wait → ⚠️KD高位等回调 (KD极高，追高风险大)
      high_caution    → 🟠KD偏高谨慎   (KD偏高，需警惕)
    """
    pb_score = stock.get('preBreakoutScore', 0)
    drop_20d = stock.get('drop20d', 0)  # 注意：drop20d 是20日涨跌幅，负=跌

    # 如果有K线数据，基于实际市场状态判定
    if klines and len(klines) >= 15:
        closes = [k['close'] for k in klines]
        cur = closes[-1]

        # 5日涨幅
        ago5 = closes[-6] if len(closes) >= 6 else closes[0]
        chg_5d = ((cur - ago5) / ago5 * 100) if ago5 != 0 else 0

        # 日线KDJ
        kdj = calc_daily_kdj(klines)
        kd_k = kdj['k'] if kdj else 50

        # ===== 优先级1: 已经大涨+KD高位 → 回落段(鱼尾) =====
        if chg_5d > 10 and kd_k > 80:
            phase = 'tail'
        # ===== 优先级2: 正在加速上涨 → 加速段 =====
        elif chg_5d > 8 and kd_k > 70:
            phase = 'accelerating'
        elif chg_5d > 15 and kd_k > 60:
            phase = 'accelerating'
        # ===== 优先级3: KD极高 → 高位等回调 =====
        elif kd_k > 85:
            phase = 'overbought_wait'
        # ===== 优先级4: KD偏高 → 偏高谨慎 =====
        elif kd_k >= 75:
            phase = 'high_caution'
        # ===== 优先级5: 预启动信号强 → 启动在即 =====
        elif pb_score >= 50:
            phase = 'on_deck'
        # ===== 优先级6: 预启动信号中等 → 接近启动 =====
        elif pb_score >= 30:
            phase = 'approaching'
        # ===== 优先级7: 低位筑底 → 启动段 =====
        else:
            phase = 'building'

        # preLaunchScore：综合预启动分数 + 实际涨幅
        launch_score = pb_score
        if chg_5d > 0:
            launch_score += min(int(chg_5d), 20)  # 涨幅加分最多20
        launch_score = min(launch_score, 100)

        return {
            'preLaunchPhase': phase,
            'preLaunchScore': launch_score,
            'phase': phase,
        }

    # 无K线数据时，退回到纯preBreakoutPhase映射
    pb_phase = stock.get('preBreakoutPhase', '')
    if not pb_phase:
        # 最后兜底：基于drop20d判断
        if drop_20d > 10:
            return {'preLaunchPhase': 'tail', 'preLaunchScore': pb_score, 'phase': 'tail'}
        elif drop_20d > 5:
            return {'preLaunchPhase': 'accelerating', 'preLaunchScore': pb_score, 'phase': 'accelerating'}
        elif pb_score >= 50:
            return {'preLaunchPhase': 'on_deck', 'preLaunchScore': pb_score, 'phase': 'on_deck'}
        elif pb_score >= 30:
            return {'preLaunchPhase': 'approaching', 'preLaunchScore': pb_score, 'phase': 'approaching'}
        else:
            return {'preLaunchPhase': 'building', 'preLaunchScore': pb_score, 'phase': 'building'}

    # preBreakoutPhase 映射
    phase_map = {
        'imminent': 'on_deck',
        'approaching_breakout': 'approaching',
        'building_base': 'building',
        'weak': 'building',
    }
    launch_phase = phase_map.get(pb_phase, 'building')

    return {
        'preLaunchPhase': launch_phase,
        'preLaunchScore': pb_score,
        'phase': launch_phase,
    }


def enrich_stock(stock, sector_counts=None):
    """为单只股票计算所有日线指标"""
    code = stock.get('code', '')
    if not code:
        return stock

    # 获取日线K线（已解析为dict列表）
    klines = get_daily_klines(code)
    if not klines or len(klines) < 6:
        print(f"  [SKIP] {code} K线数据不足")
        return stock

    # MACD
    macd = calc_macd(klines)
    if macd:
        stock['macd'] = macd['macd']

    # MA + 高开率
    ma_data = calc_ma_full(klines)
    if ma_data:
        stock['ma'] = ma_data['ma']
        stock['ma20'] = ma_data['ma20']
        stock['openRate30d'] = ma_data['openRate30d']
        stock['openRateTotal'] = ma_data['openRateTotal']
        stock['openRateScore'] = ma_data['openRateScore']

    # 趋势
    trend_data = calc_trend_daily(klines)
    if trend_data:
        stock['trend'] = trend_data['trend']
        stock['trendPct'] = trend_data['trendPct']

    # 支撑价
    support_data = calc_support_daily(klines)
    if support_data:
        stock['supportPrice'] = support_data['supportPrice']
        stock['supportDist'] = support_data['supportDist']

    # 板块共振
    sector_name = stock.get('sector', '')
    same_count = sector_counts.get(sector_name, 0) if sector_counts and sector_name else 0
    resonance = calc_sector_resonance(stock, same_count)
    if resonance:
        stock['sectorResonance'] = resonance['sectorResonance']
        stock['sectorResonanceDetail'] = resonance['sectorResonanceDetail']

    # 阶段映射（传入klines进行智能判定）
    phase_data = map_phase(stock, klines)
    if phase_data:
        stock['preLaunchPhase'] = phase_data['preLaunchPhase']
        stock['preLaunchScore'] = phase_data['preLaunchScore']
        stock['phase'] = phase_data['phase']

    return stock


def inject_into_html(html_path):
    """读取HTML，计算指标，注入回去"""
    with open(html_path, 'r', encoding='utf-8') as f:
        html = f.read()

    # 提取 STOCKS 数组
    m1 = re.search(r'const STOCKS\s*=\s*(\[.*?\]);', html, re.DOTALL)
    m2 = re.search(r'const LAUNCH_STOCKS\s*=\s*(\[.*?\]);', html, re.DOTALL)

    if not m1:
        print("[ERROR] 未找到 STOCKS 数组")
        return False

    stocks = json.loads(m1.group(1))
    launch = json.loads(m2.group(1)) if m2 else []

    print(f"STOCKS: {len(stocks)}只, LAUNCH_STOCKS: {len(launch)}只")

    # 统计每个板块在选股池中的数量
    sector_counts = {}
    for s in stocks + launch:
        sec = s.get('sector', '')
        if sec:
            sector_counts[sec] = sector_counts.get(sec, 0) + 1
    print(f"板块分布: {sector_counts}")

    # 为每只股票计算指标
    all_stocks = stocks + launch
    seen_codes = set()
    for i, stock in enumerate(all_stocks):
        code = stock.get('code', '')
        if code in seen_codes:
            continue
        seen_codes.add(code)

        print(f"  [{i+1}/{len(all_stocks)}] {stock.get('name', '?')} ({code})...", end=' ')
        try:
            enrich_stock(stock, sector_counts)
            # 检查结果
            filled = sum(1 for k in ['macd', 'ma', 'trend', 'supportPrice', 'openRate30d', 'sectorResonance', 'preLaunchPhase'] if stock.get(k))
            phase_label = stock.get('preLaunchPhase', '?')
            print(f"✅ {filled}/7 字段已填充 | 阶段={phase_label}")
        except Exception as e:
            print(f"❌ {e}")

        time.sleep(0.15)  # 限速

    # 重新序列化并替换
    stocks_json = json.dumps(stocks, ensure_ascii=False, indent=2)
    launch_json = json.dumps(launch, ensure_ascii=False, indent=2)

    # 替换 STOCKS
    new_html = html[:m1.start(1)] + stocks_json + html[m1.end(1):]

    # 替换 LAUNCH_STOCKS（位置可能因STOCKS替换而偏移）
    m2_new = re.search(r'const LAUNCH_STOCKS\s*=\s*(\[.*?\]);', new_html, re.DOTALL)
    if m2_new:
        new_html = new_html[:m2_new.start(1)] + launch_json + new_html[m2_new.end(1):]

    # 写回
    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(new_html)

    print(f"\n✅ 注入完成: {html_path}")
    return True


if __name__ == '__main__':
    html = sys.argv[1] if len(sys.argv) > 1 else HTML_PATH
    print(f"=== 日线指标预计算 ===")
    print(f"目标: {html}")
    inject_into_html(html)
