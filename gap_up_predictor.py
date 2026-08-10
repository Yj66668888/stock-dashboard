#!/usr/bin/env python3
"""
30分选票 — 多因子高开预测引擎
================================
每日扫描全A股，多维度打分，预测次日高开概率及原因。

因子架构（总分100）：
  1. 历史高开率 (30分) — 30天/全年高开率 + 趋势方向
  2. 量价结构   (25分) — 缩量高开倾向 / 低开承接 / 量比稳定性
  3. 技术形态   (20分) — 超跌反弹 / 连续高开惯性 / 价格位置
  4. 基本面信号 (15分) — 业绩变化 / 概念热度 / 板块联动
  5. 风险扣分   (10分) — 庄股嫌疑 / 异常波动 / ST风险
"""
import urllib.request
import json

# 增强因子模块（日K线零成本因子）
from gap_up_enhanced_factors import calc_enhanced_factors
import time
import os
import sys
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict

# ===================== 配置 =====================
SCAN_DAYS = 30          # 分析天数
FULL_YEAR_DAYS = 250    # 全年参考
MIN_KLINES = 20         # 最少K线数
MAX_WORKERS = 8         # 并发数
MAX_STOCK_PAGES = 55    # 股票页数
OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "daily_predictions.json")
TRACKED_FILE = os.path.join(OUTPUT_DIR, "gap_up_config.json")
TOP_N = 20

# 只保留沪深主板（00xxx深市 / 60xxx沪市），自动排除创业板(30)/科创板(68)/北交所(43/83/87/88/92)/老三板(4/8)
MAIN_BOARD_PREFIXES = ('00', '60')
EXCLUDE_KEYWORDS = ['ST', '*ST', '退市']

# ===================== 数据获取 =====================
def fetch_stock_list():
    """从新浪获取沪深主板A股列表（分市场查询，带重试）"""
    import subprocess
    stocks = []
    # 分别查询沪市(sh_a)和深市(sz_a)，避免北交所页面干扰
    for node, label in [('sh_a', '沪市'), ('sz_a', '深市')]:
        for page in range(1, 30):
            url = f"https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData?page={page}&num=100&sort=symbol&asc=1&node={node}&symbol=&_s_r_a=auto"
            got_data = False
            for attempt in range(3):
                try:
                    result = subprocess.run(
                        ['curl', '-s', '--max-time', '15', '-H', 'User-Agent: Mozilla/5.0', url],
                        capture_output=True, text=True, timeout=20
                    )
                    raw = result.stdout.strip()
                    if not raw or len(raw) < 10 or raw.startswith('<!') or raw.startswith('<html'):
                        # HTML响应或空，重试
                        if attempt < 2:
                            time.sleep(0.5)
                            continue
                        break
                    data = json.loads(raw)
                    if not data:
                        break
                    got_data = True
                    for item in data:
                        code = item.get('code', '')
                        name = item.get('name', '')
                        symbol = item.get('symbol', '')
                        if not code or not name:
                            continue
                        if not code.startswith(MAIN_BOARD_PREFIXES):
                            continue
                        if any(kw in name for kw in EXCLUDE_KEYWORDS):
                            continue
                        stocks.append({
                            'code': code,
                            'name': name,
                            'symbol': symbol,
                        })
                    break  # 成功，跳出重试
                except Exception:
                    if attempt < 2:
                        time.sleep(0.5)
                        continue
                    break
            if not got_data:
                break  # 该市场无更多数据
    # 去重
    seen = set()
    unique = []
    for s in stocks:
        if s['code'] not in seen:
            seen.add(s['code'])
            unique.append(s)
    return unique


def fetch_kline(symbol, days=400):
    """获取日K线（带重试）"""
    url = f"https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol={symbol}&scale=240&ma=no&datalen={days}"
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Referer': 'https://finance.sina.com.cn/',
                'Accept': 'application/json, text/javascript, */*; q=0.01',
                'Accept-Language': 'zh-CN,zh;q=0.9',
            })
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read().decode('utf-8', errors='ignore')
                if not raw or len(raw) < 10:
                    if attempt < 2:
                        time.sleep(0.3)
                        continue
                    return None
                return json.loads(raw)
        except Exception:
            if attempt < 2:
                time.sleep(0.3)
    return None


# ===================== 因子计算 =====================
def compute_factors(kline_data):
    """计算所有评分因子"""
    if not kline_data or len(kline_data) < MIN_KLINES:
        return None

    pairs = []
    volumes = []
    highs, lows, closes = [], [], []

    for i in range(len(kline_data) - 1):
        try:
            today = kline_data[i]
            tomorrow = kline_data[i + 1]
            close_t = float(today['close'])
            open_n = float(tomorrow['open'])
            vol_t = float(today['volume'])
            vol_n = float(tomorrow['volume'])
            high_t = float(today['high'])
            low_t = float(today['low'])

            if close_t > 0 and open_n > 0 and vol_t > 0:
                gap_pct = (open_n - close_t) / close_t
                vol_ratio = vol_n / vol_t if vol_t > 0 else 1

                pairs.append({
                    'date': tomorrow['day'],
                    'gap_pct': gap_pct,
                    'vol_ratio': vol_ratio,
                    'is_gap_up': gap_pct > 0,
                    'prev_close': close_t,
                    'prev_vol': vol_t,
                })
                volumes.append(vol_t)
                closes.append(close_t)
                highs.append(high_t)
                lows.append(low_t)
        except (KeyError, ValueError, ZeroDivisionError):
            continue

    if len(pairs) < MIN_KLINES:
        return None

    total = len(pairs)
    gap_ups = [p for p in pairs if p['is_gap_up']]
    gap_downs = [p for p in pairs if p['gap_pct'] < 0]
    gap_up_count = len(gap_ups)

    # ===== 因子1: 历史高开率 (30分) =====
    # 1a. 30天高开率 (12分)
    recent_30 = pairs[-30:] if total >= 30 else pairs
    rate_30 = sum(1 for p in recent_30 if p['is_gap_up']) / len(recent_30)
    score_1a = min(12, rate_30 * 20)  # 60%以上满分

    # 1b. 全年高开率 (8分)
    rate_full = gap_up_count / total
    score_1b = min(8, rate_full * 16)  # 50%以上满分

    # 1c. 趋势方向 (10分) — 近15天 vs 前15天
    if total >= 30:
        recent_15 = pairs[-15:]
        older_15 = pairs[-30:-15]
        rate_recent = sum(1 for p in recent_15 if p['is_gap_up']) / 15
        rate_older = sum(1 for p in older_15 if p['is_gap_up']) / 15
        trend = rate_recent - rate_older
        if trend > 0.15:
            score_1c = 10  # 明显加速
        elif trend > 0.05:
            score_1c = 7   # 温和加速
        elif trend > -0.05:
            score_1c = 5   # 持平
        elif trend > -0.15:
            score_1c = 2   # 减速
        else:
            score_1c = 0   # 明显减速
    else:
        score_1c = 5

    factor1 = score_1a + score_1b + score_1c

    # ===== 因子2: 量价结构 (25分) =====
    # 2a. 缩量高开倾向 (10分) — 高开时量比<1说明抛压轻
    gap_up_vols = [p['vol_ratio'] for p in gap_ups[-30:]] if gap_ups else []
    gap_down_vols = [p['vol_ratio'] for p in gap_downs[-30:]] if gap_downs else []

    avg_up_vol = sum(gap_up_vols) / len(gap_up_vols) if gap_up_vols else 1
    avg_down_vol = sum(gap_down_vols) / len(gap_down_vols) if gap_down_vols else 1

    # 缩量高开 + 低开放量 = 抛压枯竭信号（京能置业型）
    if avg_up_vol < 0.95 and avg_down_vol > 1.1:
        score_2a = 10
    elif avg_up_vol < 1.0 and avg_down_vol > 1.0:
        score_2a = 8
    elif avg_up_vol < 1.1:
        score_2a = 5
    else:
        score_2a = 2

    # 2b. 量比稳定性 (8分) — 波动小说明筹码稳定
    all_vols = gap_up_vols + gap_down_vols
    if len(all_vols) >= 10:
        vol_std = (sum((v - 1) ** 2 for v in all_vols) / len(all_vols)) ** 0.5
        if vol_std < 0.3:
            score_2b = 8
        elif vol_std < 0.5:
            score_2b = 5
        elif vol_std < 0.8:
            score_2b = 3
        else:
            score_2b = 1
    else:
        score_2b = 4

    # 2c. 近期缩量天数占比 (7分) — 近期缩量意味着卖盘减少
    recent_vols = [p['vol_ratio'] for p in pairs[-15:]]
    shrink_days = sum(1 for v in recent_vols if v < 0.9)
    shrink_ratio = shrink_days / len(recent_vols)
    score_2c = min(7, shrink_ratio * 14)

    factor2 = score_2a + score_2b + score_2c

    # ===== 因子3: 技术形态 (20分) =====
    # 3a. 超跌反弹潜力 (8分) — 近20天跌幅越大，高开反弹概率越高
    if len(closes) >= 20:
        price_20d_ago = closes[-21] if len(closes) > 20 else closes[0]
        if price_20d_ago > 0:
            drop_20d = (closes[-1] - price_20d_ago) / price_20d_ago
            if drop_20d < -0.20:
                score_3a = 8  # 超跌>20%
            elif drop_20d < -0.10:
                score_3a = 6
            elif drop_20d < -0.05:
                score_3a = 4
            elif drop_20d < 0:
                score_3a = 2
            else:
                score_3a = 1  # 上涨中的票，高开概率其实不低
        else:
            score_3a = 3
    else:
        score_3a = 3

    # 3b. 连续高开惯性 (7分)
    consecutive = 0
    for p in reversed(pairs):
        if p['is_gap_up']:
            consecutive += 1
        else:
            break
    if consecutive >= 5:
        score_3b = 7
    elif consecutive >= 3:
        score_3b = 5
    elif consecutive >= 1:
        score_3b = 3
    else:
        score_3b = 0

    # 3c. 价格位置 (5分) — 低价股更容易高开（散户多，波动大）
    latest_close = closes[-1] if closes else 0
    if 3 <= latest_close <= 15:
        score_3c = 5
    elif 15 < latest_close <= 30:
        score_3c = 4
    elif latest_close < 3:
        score_3c = 3  # 太便宜可能有问题
    else:
        score_3c = 2

    factor3 = score_3a + score_3b + score_3c

    # ===== 因子4: 基本面信号 (15分) =====
    # 这里用K线倒推：大幅增长+高换手=可能有业绩利好
    score_4 = 5  # 基础分，实际消息需要外部数据
    # 如果近5天有明显放量上涨，可能有利好催化
    if len(pairs) >= 5:
        last_5 = pairs[-5:]
        up_and_volume = sum(1 for p in last_5 if p['is_gap_up'] and p['vol_ratio'] > 1.2)
        if up_and_volume >= 3:
            score_4 += 10  # 放量高开密集，可能有催化剂
        elif up_and_volume >= 2:
            score_4 += 6
        elif up_and_volume >= 1:
            score_4 += 3

    factor4 = score_4

    # ===== 因子5: 风险扣分 (最多扣10分) =====
    penalty = 0
    reasons_risk = []

    # ST/退市风险
    # (已在筛选阶段排除)

    # 庄股嫌疑：高价+亏损+缩量高开
    if latest_close > 50 and rate_30 > 0.6 and avg_up_vol < 0.85:
        penalty += 5
        reasons_risk.append("⚠疑似庄股(高价+高开率+缩量)")

    # 异常巨量：单日量比>5
    if any(p['vol_ratio'] > 5 for p in pairs[-10:]):
        penalty += 3
        reasons_risk.append("近期异常巨量")

    # 连续下跌+高开（可能是诱多）
    if drop_20d < -0.15 and rate_30 > 0.6:
        penalty += 2
        reasons_risk.append("超跌高开需警惕诱多")

    factor5 = min(10, penalty)

    # ===== 增强6因子（软加分，日K线零成本）=====
    enhanced = calc_enhanced_factors(closes, highs, lows, volumes)
    bonus_total = enhanced['bonus_total']
    ef = enhanced['factors']

    # ===== 生成原因 =====
    reasons = []

    # 增强因子理由
    if ef['macd_divergence']['hit']:
        reasons.append(f"MACD底背离（强度{ef['macd_divergence']['strength']}）")
    if ef['rsi']['strength'] >= 6:
        reasons.append(f"RSI超卖({ef['rsi']['value']})")
    if ef['bollinger']['strength'] >= 5:
        reasons.append(f"布林带下轨偏离{ef['bollinger']['deviation']}%")
    if ef['bias']['strength'] >= 5:
        reasons.append(f"BIAS乖离{ef['bias']['value']}%")
    if ef['volume_dry']['hit']:
        reasons.append("地量信号（抛压枯竭）")
    if ef['daily_kdj']['hit']:
        reasons.append(f"日KDJ低位上升(K={ef['daily_kdj']['k']} D={ef['daily_kdj']['d']} J={ef['daily_kdj']['j']})")
    if ef['volume_spike']['hit']:
        reasons.append(f"量比突变（强度{ef['volume_spike']['strength']}）")
    if ef['volume_shrink']['hit']:
        reasons.append("缩量至极致（筹码沉淀）")

    # ===== 总分 =====
    total_score = factor1 + factor2 + factor3 + factor4 - factor5
    total_score = max(0, min(100, total_score))
    # bonus单独输出，不加到total_score（软加分在健康度里体现）

    if rate_30 >= 0.6:
        reasons.append(f"近30天高开率{rate_30*100:.0f}%")
    if score_1c >= 7:
        reasons.append("高开趋势加速中")
    if trend > 0.1 and total >= 30:
        reasons.append(f"近期高开率从{rate_older*100:.0f}%升至{rate_recent*100:.0f}%")

    if score_2a >= 8:
        reasons.append("缩量高开+低开放量→抛压枯竭")
    elif score_2a >= 5:
        reasons.append("温和量能，筹码稳定")

    if score_2c >= 5:
        reasons.append("近期持续缩量，卖盘减少")

    if score_3a >= 6:
        reasons.append(f"近20天跌{abs(drop_20d)*100:.0f}%，超跌反弹概率大")
    if consecutive >= 3:
        reasons.append(f"连续{consecutive}天高开，惯性延续")

    if 3 <= latest_close <= 15:
        reasons.append(f"低价股({latest_close:.2f}元)，流动性好")
    if score_4 >= 10:
        reasons.append("近期放量高开密集，可能有催化事件")

    # 综合
    if total_score >= 75:
        confidence = "⭐⭐⭐⭐⭐ 强烈看高开"
    elif total_score >= 65:
        confidence = "⭐⭐⭐⭐ 较大概率高开"
    elif total_score >= 55:
        confidence = "⭐⭐⭐ 可能高开"
    elif total_score >= 45:
        confidence = "⭐⭐ 有一定概率"
    else:
        confidence = "⭐ 概率较低"

    return {
        'total_score': round(total_score, 1),
        'confidence': confidence,
        'reasons': reasons,
        'risk_reasons': reasons_risk,
        'scores': {
            '历史高开率': round(factor1, 1),
            '量价结构': round(factor2, 1),
            '技术形态': round(factor3, 1),
            '基本面信号': round(factor4, 1),
            '风险扣分': -factor5,
        },
        'metrics': {
            'rate_30': round(rate_30 * 100, 1),
            'rate_full': round(rate_full * 100, 1),
            'trend': round(trend * 100, 1) if total >= 30 else 0,
            'avg_vol_up': round(avg_up_vol, 2),
            'avg_vol_down': round(avg_down_vol, 2),
            'consecutive': consecutive,
            'drop_20d': round(drop_20d * 100, 1) if len(closes) >= 20 else 0,
            'latest_close': round(latest_close, 2),
            'latest_date': pairs[-1]['date'] if pairs else '',
            # 增强6因子
            'enhanced_bonus': bonus_total,
            'macd_div': ef['macd_divergence']['strength'],
            'rsi': ef['rsi']['value'],
            'rsi_str': ef['rsi']['strength'],
            'boll_dev': ef['bollinger']['deviation'],
            'boll_str': ef['bollinger']['strength'],
            'bias': ef['bias']['value'],
            'bias_str': ef['bias']['strength'],
            'vol_dry': 1 if ef['volume_dry']['hit'] else 0,
            'vol_dry_str': ef['volume_dry']['strength'],
            'vol_spike': 1 if ef['volume_spike']['hit'] else 0,
            'vol_spike_str': ef['volume_spike']['strength'],
            'vol_shrink': 1 if ef['volume_shrink']['hit'] else 0,
            'vol_shrink_str': ef['volume_shrink']['strength'],
            'kdj_k': ef['daily_kdj']['k'],
            'kdj_d': ef['daily_kdj']['d'],
            'kdj_j': ef['daily_kdj']['j'],
            'kdj_hit': 1 if ef['daily_kdj']['hit'] else 0,
            'kdj_str': ef['daily_kdj']['strength'],
        }
    }


def scan_stock(stock):
    """扫描单只股票"""
    kline = fetch_kline(stock['symbol'])
    if not kline or len(kline) < MIN_KLINES:
        return None
    
    factors = compute_factors(kline)
    if not factors:
        return None
    
    return {
        'code': stock['code'],
        'name': stock['name'],
        **factors
    }


def is_tracked(code, tracked_stocks):
    """检查是否为跟踪标的"""
    for s in tracked_stocks:
        if s['code'] == code:
            return s
    return None


def main():
    scan_date = datetime.now().strftime('%Y-%m-%d %H:%M')
    print(f"\n{'='*60}")
    print(f"  30分选票 — 多因子高开预测引擎")
    print(f"  扫描时间: {scan_date}")
    print(f"{'='*60}\n")

    # 加载跟踪标的
    tracked_stocks = []
    try:
        with open(TRACKED_FILE, 'r') as f:
            config = json.load(f)
            tracked_stocks = config.get('tracked_stocks', [])
    except:
        pass

    # 获取股票列表
    print("📡 获取A股列表（仅00/60主板）...")
    stocks = fetch_stock_list()
    print(f"  ✅ 共 {len(stocks)} 只主板票\n")

    # 并发扫描
    print(f"🔍 开始多因子扫描 ({MAX_WORKERS}线程)...")
    results = []
    completed = 0
    
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(scan_stock, s): s for s in stocks}
        for future in as_completed(futures):
            completed += 1
            if completed % 500 == 0:
                print(f"  进度: {completed}/{len(stocks)} ({completed*100//len(stocks)}%)")
            try:
                r = future.result()
                if r:
                    results.append(r)
            except:
                pass

    print(f"  ✅ 扫描完成！有效结果: {len(results)} 只\n")

    # 去重并按分数排序
    seen = set()
    unique_results = []
    for r in results:
        if r['code'] not in seen:
            seen.add(r['code'])
            unique_results.append(r)
    
    unique_results.sort(key=lambda x: x['total_score'], reverse=True)

    # 标记跟踪标的
    for r in unique_results:
        tracked = is_tracked(r['code'], tracked_stocks)
        r['is_tracked'] = bool(tracked)
        r['tracked_tags'] = tracked.get('tags', []) if tracked else []

    # ========== 输出 TOP N ==========
    top = unique_results[:TOP_N]
    
    print(f"{'='*80}")
    print(f"  📈 次日高开概率 TOP {TOP_N}  (扫描时间: {scan_date})")
    print(f"{'='*80}")
    
    for i, r in enumerate(top):
        score = r['total_score']
        name = r['name']
        code = r['code']
        m = r['metrics']
        
        # 跟踪标记
        tracked_mark = ' 👁 跟踪' if r['is_tracked'] else ''
        
        # 分数颜色标记
        if score >= 75:
            star = '🔥'
        elif score >= 65:
            star = '🟡'
        elif score >= 55:
            star = '🟢'
        else:
            star = '⚪'
        
        print(f"\n{star} #{i+1} {code} {name} — 综合评分 {score:.0f}/100 {tracked_mark}")
        print(f"   置信度: {r['confidence']}")
        print(f"   明细: 高开率{m['rate_30']}% | 全年{m['rate_full']}% | 趋势{m['trend']:+.1f}%")
        print(f"   量价: 高开量比{m['avg_vol_up']} | 低开量比{m['avg_vol_down']} | 连续{m['consecutive']}天")
        print(f"   技术: {'超跌' if m['drop_20d'] < -5 else '正常'} {m['drop_20d']:+.1f}% | 收盘{m['latest_close']}")
        
        if r['reasons']:
            print(f"   📋 高开原因:")
            for reason in r['reasons']:
                print(f"      • {reason}")
        
        if r['risk_reasons']:
            print(f"   ⚠️ 风险提示:")
            for risk in r['risk_reasons']:
                print(f"      • {risk}")
        
        print(f"   评分: 历史高开率{r['scores']['历史高开率']} | 量价结构{r['scores']['量价结构']} | 技术形态{r['scores']['技术形态']} | 基本面{r['scores']['基本面信号']} | 风险{r['scores']['风险扣分']}")

    # ========== 分数分布 ==========
    print(f"\n{'='*80}")
    print(f"  📊 全市场评分分布 ({len(unique_results)} 只)")
    print(f"{'='*80}")
    
    ranges = [(75, 100, '🔥 ≥75分'), (65, 75, '🟡 65-74分'), (55, 65, '🟢 55-64分'), (45, 55, '⚪ 45-54分'), (0, 45, '⬜ <45分')]
    for lo, hi, label in ranges:
        count = sum(1 for r in unique_results if lo <= r['total_score'] < hi)
        bar = '█' * (count // 20)
        print(f"  {label}: {count:>5}只 {bar}")

    # ========== 跟踪标的状态 ==========
    if tracked_stocks:
        print(f"\n{'='*80}")
        print(f"  👁 跟踪标的今日状态")
        print(f"{'='*80}")
        for ts in tracked_stocks:
            found = None
            for r in unique_results:
                if r['code'] == ts['code']:
                    found = r
                    break
            if found:
                score = found['total_score']
                status = '🟢 推荐持有' if score >= 55 else ('🟡 观察中' if score >= 45 else '🔴 建议减仓')
                print(f"  {ts['code']} {ts['name']}: 评分 {score:.0f}/100 — {status}")
                print(f"    原因: {ts['reason'][:80]}...")
            else:
                print(f"  {ts['code']} {ts['name']}: ⚠️ 扫描中无数据")

    # ========== 按30天高开率排序（用户核心关注：经常第二天高开）==========
    by_rate = sorted(unique_results, key=lambda x: x['metrics']['rate_30'], reverse=True)
    top_gap_up = by_rate[:50]  # 30天高开率TOP50

    print(f"\n{'='*80}")
    print(f"  📊 30天经常高开 TOP 30  (核心筛选：经常第二天高开)")
    print(f"{'='*80}")
    print(f"  {'代码':<8} {'名称':<10} {'30天高开率':>10} {'全年':>6} {'趋势':>7} {'连续':>4} {'收盘':>8} {'评分':>6}")
    print(f"  {'-'*72}")
    for i, r in enumerate(top_gap_up[:30]):
        m = r['metrics']
        star = '🔥' if m['rate_30'] >= 65 else ('🟡' if m['rate_30'] >= 55 else '  ')
        print(f"  {star} {r['code']:<8} {r['name']:<10} {m['rate_30']:>8.1f}% {m['rate_full']:>5.1f}% {m['trend']:>+6.1f}% {m['consecutive']:>4}天 {m['latest_close']:>8} {r['total_score']:>6.1f}")

    # ========== 保存结果 ==========
    # 全市场TOP200（总分+增强bonus排序，用于漏斗第二层精细扫描）
    top200 = sorted(unique_results, key=lambda x: x['total_score'] + x['metrics'].get('enhanced_bonus', 0), reverse=True)[:200]

    output = {
        'scan_date': scan_date,
        'total_scanned': len(stocks),
        'valid_results': len(unique_results),
        'top_predictions': top,
        'top_gap_up_rate': top_gap_up,  # 按30天高开率排序TOP50
        'top200': top200,  # 全市场TOP200（含增强因子bonus）
        'all_results': unique_results,  # 保存全部结果
        'tracked_status': [
            {
                'code': ts['code'],
                'name': ts['name'],
                'score': next((r['total_score'] for r in unique_results if r['code'] == ts['code']), None),
            }
            for ts in tracked_stocks
        ]
    }

    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    # 单独输出TOP200到独立文件（供漏斗第二层使用）
    top200_output = {
        'scan_date': scan_date,
        'total': len(top200),
        'stocks': [
            {
                'code': r['code'],
                'name': r['name'],
                'total_score': r['total_score'],
                'enhanced_bonus': r['metrics'].get('enhanced_bonus', 0),
                'combined_score': round(r['total_score'] + r['metrics'].get('enhanced_bonus', 0), 1),
                'rate_30': r['metrics'].get('rate_30', 0),
                'drop_20d': r['metrics'].get('drop_20d', 0),
                'macd_div': r['metrics'].get('macd_div', 0),
                'rsi': r['metrics'].get('rsi', 50),
                'boll_str': r['metrics'].get('boll_str', 0),
                'bias': r['metrics'].get('bias', 0),
                'vol_dry': r['metrics'].get('vol_dry', 0),
                'kdj_k': r['metrics'].get('kdj_k', 50),
                'kdj_d': r['metrics'].get('kdj_d', 50),
                'kdj_j': r['metrics'].get('kdj_j', 50),
                'kdj_hit': r['metrics'].get('kdj_hit', 0),
                'latest_close': r['metrics'].get('latest_close', 0),
            }
            for r in top200
        ]
    }
    with open('full_market_top200.json', 'w', encoding='utf-8') as f:
        json.dump(top200_output, f, ensure_ascii=False, indent=2)

    # 打印增强因子统计
    enhanced_hits = sum(1 for r in unique_results if r['metrics'].get('enhanced_bonus', 0) >= 10)
    macd_hits = sum(1 for r in unique_results if r['metrics'].get('macd_div', 0) > 0)
    rsi_hits = sum(1 for r in unique_results if r['metrics'].get('rsi_str', 0) >= 6)
    boll_hits = sum(1 for r in unique_results if r['metrics'].get('boll_str', 0) >= 5)
    bias_hits = sum(1 for r in unique_results if r['metrics'].get('bias_str', 0) >= 5)
    dry_hits = sum(1 for r in unique_results if r['metrics'].get('vol_dry', 0) == 1)
    kdj_hits = sum(1 for r in unique_results if r['metrics'].get('kdj_hit', 0) == 1)
    print(f"\n{'='*80}")
    print(f"  🧪 增强6因子全市场命中统计 ({len(unique_results)}只)")
    print(f"{'='*80}")
    print(f"  MACD底背离: {macd_hits}只 ({macd_hits*100//max(len(unique_results),1)}%)")
    print(f"  RSI超卖:    {rsi_hits}只 ({rsi_hits*100//max(len(unique_results),1)}%)")
    print(f"  布林带下轨: {boll_hits}只 ({boll_hits*100//max(len(unique_results),1)}%)")
    print(f"  BIAS超卖:   {bias_hits}只 ({bias_hits*100//max(len(unique_results),1)}%)")
    print(f"  地量信号:   {dry_hits}只 ({dry_hits*100//max(len(unique_results),1)}%)")
    print(f"  日KDJ低位:  {kdj_hits}只 ({kdj_hits*100//max(len(unique_results),1)}%)")
    print(f"  bonus≥10分: {enhanced_hits}只")
    print(f"  TOP200已输出: full_market_top200.json")
    
    # ========== 自动更新选票池 config.json ==========
    update_stock_pool(unique_results, tracked_stocks, scan_date)
    
    print(f"\n📁 结果已保存: {OUTPUT_FILE}")
    print(f"{'='*60}\n")


def update_stock_pool(all_results, existing_tracked, scan_date):
    """把每日扫描选出的 TOP 票自动写入 config.json 的选票池"""
    # 读取当前 config
    try:
        with open(TRACKED_FILE, 'r', encoding='utf-8') as f:
            config = json.load(f)
    except:
        config = {'tracked_stocks': []}

    if 'tracked_stocks' not in config:
        config['tracked_stocks'] = []

    # 标记原有标的来源
    for s in config['tracked_stocks']:
        if 'source' not in s:
            s['source'] = 'manual'

    # 筛选 TOP 20 中主板票
    top_main = [r for r in all_results[:20] if r['code'].startswith(('00', '60'))]

    # 收集已有 code
    existing_codes = {s['code'] for s in config['tracked_stocks']}

    # 添加新选出的票
    added = 0
    for r in top_main:
        if r['code'] in existing_codes:
            # 已存在则更新评分
            for s in config['tracked_stocks']:
                if s['code'] == r['code'] and s.get('source') == 'auto_scan':
                    s['score'] = r['total_score']
                    s['confidence'] = r['confidence']
                    s['reason'] = '；'.join(r['reasons'])
                    s['risk_reasons'] = r.get('risk_reasons', [])
                    s['metrics'] = r['metrics']
                    s['last_scan'] = scan_date
            continue
        m = r['metrics']
        config['tracked_stocks'].append({
            'code': r['code'],
            'name': r['name'],
            'added': datetime.now().strftime('%Y-%m-%d'),
            'source': 'auto_scan',
            'score': r['total_score'],
            'confidence': r['confidence'],
            'reason': '；'.join(r['reasons']),
            'risk_reasons': r.get('risk_reasons', []),
            'metrics': m,
            'scores_detail': r['scores'],
            'last_scan': scan_date,
            'tags': ['每日扫描', '次日高开预测'],
        })
        existing_codes.add(r['code'])
        added += 1

    # 清理：删除超过 7 天没扫描命中的 auto_scan 票
    cutoff = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
    before = len(config['tracked_stocks'])
    config['tracked_stocks'] = [
        s for s in config['tracked_stocks']
        if s.get('source') != 'auto_scan'
        or s.get('last_scan', '9999') >= cutoff
        or s.get('added', '9999') >= cutoff
    ]
    removed = before - len(config['tracked_stocks'])

    # 记录扫描历史
    if 'scan_history' not in config:
        config['scan_history'] = []
    config['scan_history'].insert(0, {
        'date': scan_date,
        'top_count': len(top_main),
    })
    config['scan_history'] = config['scan_history'][:30]  # 只保留近30次

    config['last_scan'] = scan_date

    with open(TRACKED_FILE, 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

    manual_count = sum(1 for s in config['tracked_stocks'] if s.get('source') == 'manual')
    auto_count = sum(1 for s in config['tracked_stocks'] if s.get('source') == 'auto_scan')

    print(f"\n{'='*80}")
    print(f"  📋 30分选票池已更新")
    print(f"{'='*80}")
    print(f"  ✅ 新增选出: {added} 只")
    print(f"  🗑  清理过期: {removed} 只")
    print(f"  👁 手动跟踪: {manual_count} 只")
    print(f"  🤖 自动选出: {auto_count} 只")
    print(f"  📦 选票池总数: {len(config['tracked_stocks'])} 只")
    print(f"  📁 配置文件: {TRACKED_FILE}")
    print(f"{'='*80}")


if __name__ == '__main__':
    main()
