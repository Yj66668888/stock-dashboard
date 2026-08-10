#!/usr/bin/env python3
"""
高开日30分KD特征分析
====================
对高开率TOP50股票，分析过去60天每次"次日高开"时，
前一天30分钟KD所处的区间（高位>80 / 中位20-80 / 低位<20）。

输出每只票的KD特征标签，如：
  "高开前KD多在低位(10-25)，超卖反弹型"
  "高开前KD多在中位(35-55)，趋势延续型"

同时获取当天最新的30分KD和5分KD值。
"""
import urllib.request
import json
import time
import os
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# ===================== 配置 =====================
INPUT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "daily_predictions.json")
OUTPUT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kdj_pattern_analysis.json")
MAX_WORKERS = 6
TIMEOUT = 15

# ===================== K线获取 =====================
def fetch_30min_kline(code, lmt=500):
    """获取30分钟K线（东方财富API）
    返回 [{date, open, close, high, low, volume}, ...]
    """
    market = '1' if code.startswith('6') else '0'
    secid = f"{market}.{code}"
    url = (f"https://push2his.eastmoney.com/api/qt/stock/kline/get?"
           f"secid={secid}&ut=2869ffafee25fa5d66c39e8d498df3a8"
           f"&fields1=f1,f2,f3,f4,f5,f6"
           f"&fields2=f51,f52,f53,f54,f55,f56,f57"
           f"&klt=30&fqt=1&end=20500101&lmt={lmt}")
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)',
                'Referer': 'https://quote.eastmoney.com'
            })
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                raw = resp.read().decode('utf-8')
                if not raw or len(raw) < 20:
                    if attempt < 2:
                        time.sleep(0.5)
                        continue
                    return None
                data = json.loads(raw)
                if not data or not data.get('data') or not data['data'].get('klines'):
                    return None
                klines = []
                for k in data['data']['klines']:
                    f = k.split(',')
                    klines.append({
                        'datetime': f[0],
                        'date': f[0][:10],
                        'time': f[0][11:16] if len(f[0]) > 11 else '',
                        'open': float(f[1]),
                        'close': float(f[2]),
                        'high': float(f[3]),
                        'low': float(f[4]),
                        'volume': int(float(f[5])),
                        'amount': float(f[6]) if len(f) > 6 else 0
                    })
                return klines
        except Exception:
            if attempt < 2:
                time.sleep(0.5)
    return None


def fetch_5min_kline(code, lmt=100):
    """获取5分钟K线"""
    market = '1' if code.startswith('6') else '0'
    secid = f"{market}.{code}"
    url = (f"https://push2his.eastmoney.com/api/qt/stock/kline/get?"
           f"secid={secid}&ut=2869ffafee25fa5d66c39e8d498df3a8"
           f"&fields1=f1,f2,f3,f4,f5,f6"
           f"&fields2=f51,f52,f53,f54,f55,f56,f57"
           f"&klt=5&fqt=1&end=20500101&lmt={lmt}")
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)',
                'Referer': 'https://quote.eastmoney.com'
            })
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                raw = resp.read().decode('utf-8')
                if not raw or len(raw) < 20:
                    if attempt < 2:
                        time.sleep(0.5)
                        continue
                    return None
                data = json.loads(raw)
                if not data or not data.get('data') or not data['data'].get('klines'):
                    return None
                klines = []
                for k in data['data']['klines']:
                    f = k.split(',')
                    klines.append({
                        'datetime': f[0],
                        'date': f[0][:10],
                        'time': f[0][11:16] if len(f[0]) > 11 else '',
                        'open': float(f[1]),
                        'close': float(f[2]),
                        'high': float(f[3]),
                        'low': float(f[4]),
                        'volume': int(float(f[5])),
                    })
                return klines
        except Exception:
            if attempt < 2:
                time.sleep(0.5)
    return None


def fetch_daily_kline_sina(symbol, datalen=80):
    """获取日K线（新浪API，用于判断高开日）"""
    url = (f"https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
           f"CN_MarketData.getKLineData?symbol={symbol}&scale=240&ma=no&datalen={datalen}")
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0',
                'Referer': 'https://finance.sina.com.cn'
            })
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                raw = resp.read().decode('gbk', errors='ignore')
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


# ===================== KDJ计算 =====================
def calc_kdj(klines, n=9):
    """计算KDJ(9,3,3)
    输入: klines = [{high, low, close, ...}, ...]
    输出: [{k, d, j, date}, ...]
    """
    if not klines or len(klines) < n:
        return None

    result = []
    prev_k = 50.0
    prev_d = 50.0

    for i in range(n - 1, len(klines)):
        highest_h = max(kl['high'] for kl in klines[i - n + 1:i + 1])
        lowest_l = min(kl['low'] for kl in klines[i - n + 1:i + 1])
        close = klines[i]['close']

        if highest_h == lowest_l:
            rsv = 50.0
        else:
            rsv = (close - lowest_l) / (highest_h - lowest_l) * 100

        k = 2.0 / 3 * prev_k + 1.0 / 3 * rsv
        d = 2.0 / 3 * prev_d + 1.0 / 3 * k
        j = 3 * k - 2 * d

        result.append({
            'k': round(k, 2),
            'd': round(d, 2),
            'j': round(j, 2),
            'date': klines[i].get('date', ''),
            'time': klines[i].get('time', ''),
            'datetime': klines[i].get('datetime', '')
        })
        prev_k = k
        prev_d = d

    return result


# ===================== 核心分析逻辑 =====================
def analyze_stock(stock):
    """分析单只股票的高开日KD特征"""
    code = stock['code']
    name = stock.get('name', '')
    symbol = 'sh' + code if code.startswith('6') else 'sz' + code

    result = {
        'code': code,
        'name': name,
        'kdj_30_pattern': None,
        'kdj_30_today': None,
        'kdj_5_today': None,
        'error': None
    }

    # 1. 获取日K线，找出高开日
    daily = fetch_daily_kline_sina(symbol, datalen=80)
    if not daily or len(daily) < 30:
        result['error'] = '日K线不足'
        return result

    # 找出高开日（次日开盘 > 当日收盘）
    # daily[i] 的次日是 daily[i+1]
    gap_up_days = []  # 存储高开发生日(即daily[i+1]的day)和前一天日期(daily[i]的day)
    for i in range(len(daily) - 1):
        try:
            close_t = float(daily[i]['close'])
            open_n = float(daily[i + 1]['open'])
            if close_t > 0 and open_n > close_t:
                # 高开日是 daily[i+1]，前一天是 daily[i]
                gap_up_days.append({
                    'gap_up_date': daily[i + 1]['day'],   # 高开发生的那天
                    'prev_date': daily[i]['day'],          # 前一天（KD特征分析对象）
                    'gap_pct': round((open_n - close_t) / close_t * 100, 2)
                })
        except (KeyError, ValueError, ZeroDivisionError):
            continue

    # 只看最近30天和60天的高开日
    recent_30_gap_ups = gap_up_days[-30:] if len(gap_up_days) >= 30 else gap_up_days
    recent_60_gap_ups = gap_up_days[-60:] if len(gap_up_days) >= 60 else gap_up_days

    # 2. 获取30分钟K线（足够覆盖60天，60*8=480根）
    klines_30 = fetch_30min_kline(code, lmt=600)
    if not klines_30 or len(klines_30) < 50:
        result['error'] = '30分K线不足'
        return result

    # 3. 计算完整30分KDJ序列
    kdj_30_all = calc_kdj(klines_30, 9)
    if not kdj_30_all or len(kdj_30_all) < 20:
        result['error'] = 'KDJ计算失败'
        return result

    # 4. 获取当天最新的30分KDJ
    last_kdj_30 = kdj_30_all[-1]
    result['kdj_30_today'] = {
        'k': last_kdj_30['k'],
        'd': last_kdj_30['d'],
        'j': last_kdj_30['j'],
        'time': last_kdj_30.get('time', ''),
        'date': last_kdj_30.get('date', ''),
        'zone': get_kd_zone(last_kdj_30['k'], last_kdj_30['d'])
    }

    # 5. 获取5分K线和当天5分KDJ
    klines_5 = fetch_5min_kline(code, lmt=100)
    if klines_5 and len(klines_5) >= 12:
        kdj_5_all = calc_kdj(klines_5, 9)
        if kdj_5_all and len(kdj_5_all) > 0:
            last_kdj_5 = kdj_5_all[-1]
            result['kdj_5_today'] = {
                'k': last_kdj_5['k'],
                'd': last_kdj_5['d'],
                'j': last_kdj_5['j'],
                'time': last_kdj_5.get('time', ''),
                'zone': get_kd_zone(last_kdj_5['k'], last_kdj_5['d'])
            }

    # 6. 分析高开日的30分KD特征
    # 按日期分组30分KDJ，取每天最后一根30分K线的K/D值
    daily_kdj_map = {}  # {date: {k, d, j}}
    for kdj_item in kdj_30_all:
        d = kdj_item['date']
        # 只保留每天最后一条（收盘时的KD）
        daily_kdj_map[d] = {
            'k': kdj_item['k'],
            'd': kdj_item['d'],
            'j': kdj_item['j']
        }

    # 分析30天窗口的高开日KD特征
    pattern_30 = analyze_kd_pattern(recent_30_gap_ups, daily_kdj_map, '30天')
    pattern_60 = analyze_kd_pattern(recent_60_gap_ups, daily_kdj_map, '60天')

    result['kdj_30_pattern'] = {
        'pattern_30': pattern_30,
        'pattern_60': pattern_60,
        'label': pattern_30['label'] if pattern_30 else '数据不足',
        'total_gap_ups_30': len(recent_30_gap_ups),
        'total_gap_ups_60': len(recent_60_gap_ups),
    }

    return result


def get_kd_zone(k, d):
    """判断KD区间"""
    avg = (k + d) / 2
    if avg >= 80:
        return '高位(超买)'
    elif avg >= 50:
        return '中高位'
    elif avg >= 20:
        return '中低位'
    else:
        return '低位(超卖)'


def analyze_kd_pattern(gap_ups, daily_kdj_map, label_prefix):
    """分析高开日的KD分布特征"""
    if not gap_ups:
        return None

    kd_values = []
    for gu in gap_ups:
        prev_date = gu['prev_date']
        if prev_date in daily_kdj_map:
            kd_values.append({
                'k': daily_kdj_map[prev_date]['k'],
                'd': daily_kdj_map[prev_date]['d'],
                'date': prev_date,
                'gap_pct': gu['gap_pct']
            })

    if len(kd_values) < 3:
        return None

    ks = [v['k'] for v in kd_values]
    ds = [v['d'] for v in kd_values]

    avg_k = round(sum(ks) / len(ks), 1)
    avg_d = round(sum(ds) / len(ds), 1)
    min_k = round(min(ks), 1)
    max_k = round(max(ks), 1)

    # 统计区间分布
    high_count = sum(1 for v in kd_values if (v['k'] + v['d']) / 2 >= 80)
    mid_count = sum(1 for v in kd_values if 20 <= (v['k'] + v['d']) / 2 < 80)
    low_count = sum(1 for v in kd_values if (v['k'] + v['d']) / 2 < 20)
    total = len(kd_values)

    high_pct = round(high_count / total * 100, 1)
    mid_pct = round(mid_count / total * 100, 1)
    low_pct = round(low_count / total * 100, 1)

    # 生成特征标签
    if low_pct >= 50:
        zone_label = f"低位({min_k:.0f}-{max_k:.0f})超卖反弹型"
    elif high_pct >= 50:
        zone_label = f"高位({min_k:.0f}-{max_k:.0f})强势延续型"
    elif avg_k < 35:
        zone_label = f"中低位({min_k:.0f}-{max_k:.0f})偏弱反弹型"
    elif avg_k > 65:
        zone_label = f"中高位({min_k:.0f}-{max_k:.0f})偏强延续型"
    else:
        zone_label = f"中位({min_k:.0f}-{max_k:.0f})震荡型"

    return {
        'label': zone_label,
        'avg_k': avg_k,
        'avg_d': avg_d,
        'min_k': min_k,
        'max_k': max_k,
        'high_pct': high_pct,
        'mid_pct': mid_pct,
        'low_pct': low_pct,
        'sample_count': total,
        'details': kd_values
    }


# ===================== 主流程 =====================
def main():
    print("=" * 60)
    print("  高开日30分KD特征分析引擎")
    print("=" * 60)

    # 读取已有扫描结果
    if not os.path.exists(INPUT_FILE):
        print(f"❌ 找不到输入文件: {INPUT_FILE}")
        return

    with open(INPUT_FILE, 'r') as f:
        data = json.load(f)

    top50 = data.get('top_gap_up_rate', [])
    if not top50:
        print("❌ daily_predictions.json 中没有 top_gap_up_rate 数据")
        return

    print(f"\n📊 待分析股票: {len(top50)} 只（高开率TOP50）")
    print(f"⏰ 开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    results = []
    completed = 0
    errors = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_map = {}
        for stock in top50:
            # 从top_gap_up_rate取code和name
            s = {
                'code': stock.get('code', ''),
                'name': stock.get('name', '')
            }
            if not s['code']:
                continue
            future = executor.submit(analyze_stock, s)
            future_map[future] = s

        for future in as_completed(future_map):
            s = future_map[future]
            try:
                result = future.result()
                results.append(result)
                completed += 1

                if result.get('error'):
                    errors += 1
                    print(f"  [{completed:2d}/{len(top50)}] ❌ {s['code']} {s['name']} - {result['error']}")
                else:
                    p = result.get('kdj_30_pattern', {})
                    label = p.get('label', '?') if p else '?'
                    today_k = result.get('kdj_30_today', {}).get('k', '?')
                    today_d = result.get('kdj_30_today', {}).get('d', '?')
                    today_zone = result.get('kdj_30_today', {}).get('zone', '?')
                    print(f"  [{completed:2d}/{len(top50)}] ✅ {s['code']} {s['name']:8s} | "
                          f"高开前KD: {label:20s} | 今30分K={today_k} D={today_d}({today_zone})")
            except Exception as e:
                completed += 1
                errors += 1
                print(f"  [{completed:2d}/{len(top50)}] ❌ {s['code']} {s['name']} - 异常: {e}")

    # 排序：按code排序
    results.sort(key=lambda x: x['code'])

    # 保存结果
    output = {
        'scan_date': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'total_analyzed': len(results),
        'total_success': len(results) - errors,
        'total_errors': errors,
        'results': results
    }

    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n{'=' * 60}")
    print(f"✅ 分析完成！")
    print(f"  成功: {len(results) - errors} 只")
    print(f"  失败: {errors} 只")
    print(f"  结果已保存: {OUTPUT_FILE}")
    print(f"⏰ 完成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # 打印TOP10汇总
    print(f"\n{'=' * 60}")
    print("📊 高开率TOP10 — KD特征汇总:")
    print(f"{'代码':<8} {'名称':<10} {'高开前KD特征':<30} {'今30分K/D':<15} {'今5分K/D':<15}")
    print("-" * 80)
    for i, r in enumerate(results[:10]):
        if r.get('error'):
            continue
        p = r.get('kdj_30_pattern', {})
        label = p.get('label', '--') if p else '--'
        k30 = r.get('kdj_30_today', {})
        k30_str = f"{k30.get('k','?')}/{k30.get('d','?')}" if k30 else '--'
        k5 = r.get('kdj_5_today', {})
        k5_str = f"{k5.get('k','?')}/{k5.get('d','?')}" if k5 else '--'
        print(f"{r['code']:<8} {r['name']:<10} {label:<30} {k30_str:<15} {k5_str:<15}")


if __name__ == '__main__':
    main()
