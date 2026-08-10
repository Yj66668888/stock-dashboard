#!/usr/bin/env python3
"""
KDJ 低位启动因子
==================
为启动段选票增加 5分钟 KDJ 和 30分钟 KDJ 双重验证：
- K/D/J 三值同时处于低位 → 超卖
- K 值上升 → 反弹确认
- 满足条件加软加分（不满足不扣分）

数据源：东方财富K线API（5分钟 + 30分钟）
输入：deploy/index.html 的 STOCKS 数组（30只跟踪票）
输出：kdj_factor.json
"""
import urllib.request
import json
import time
import os
import re
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE = os.path.dirname(os.path.abspath(__file__))
INDEX_HTML = os.path.join(BASE, 'deploy', 'index.html')
TOP200_FILE = os.path.join(BASE, 'full_market_top200.json')
OUTPUT_FILE = os.path.join(BASE, 'kdj_factor.json')
MAX_WORKERS = 10  # 从5提升到10（TOP200数量大）
TIMEOUT = 12
PERIOD = 9  # KDJ 标准周期

# ============ 阈值配置 ============
K_MAX = 40      # K 值上限（低位判定）
D_MAX = 40      # D 值上限
J_MAX = 30      # J 值上限
K_BONUS_5M = 5   # 5分钟KD满足 → 加分
K_BONUS_30M = 5  # 30分钟KD满足 → 加分


def fetch_kline_em(code, klt, lmt=60):
    """东方财富K线API（klt: 5=5分钟, 30=30分钟）"""
    market = '1' if code.startswith('6') else '0'
    secid = f"{market}.{code}"
    url = (
        f"https://push2his.eastmoney.com/api/qt/stock/kline/get?"
        f"secid={secid}&ut=2869ffafee25fa5d66c39e8d498df3a8"
        f"&fields1=f1,f2,f3,f4,f5,f6"
        f"&fields2=f51,f52,f53,f54,f55,f56,f57"
        f"&klt={klt}&fqt=1&end=20500101&lmt={lmt}"
    )
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


def calc_kdj(klines, period=PERIOD):
    """
    计算KDJ指标
    返回: [(K, D, J, date), ...] 从第period根开始
    """
    if not klines or len(klines) < period + 1:
        return None

    results = []
    k, d = 50.0, 50.0  # 初始值

    for i in range(period - 1, len(klines)):
        # 取过去period根K线的最高价和最低价
        window = klines[i - period + 1 : i + 1]
        high_n = max(w['high'] for w in window)
        low_n = min(w['low'] for w in window)
        close = window[-1]['close']

        if high_n == low_n:
            rsv = 50.0
        else:
            rsv = (close - low_n) / (high_n - low_n) * 100

        k = 2/3 * k + 1/3 * rsv
        d = 2/3 * d + 1/3 * k
        j = 3 * k - 2 * d

        results.append({
            'k': round(k, 2),
            'd': round(d, 2),
            'j': round(j, 2),
            'date': window[-1]['datetime'][:16],  # 精确到分钟
        })

    return results


def check_kdj_signal(kdj_list):
    """
    检查KDJ是否满足「低位+上升」
    条件：
    1. 最新一根K < K_MAX, D < D_MAX, J < J_MAX
    2. 最新一根K > 前一根K（上升趋势）
    返回: (meets, latest_k, latest_d, latest_j, prev_k, prev_d, prev_j)
    """
    if not kdj_list or len(kdj_list) < 2:
        return (False, None, None, None, None, None, None)

    curr = kdj_list[-1]
    prev = kdj_list[-2]

    ck, cd, cj = curr['k'], curr['d'], curr['j']
    pk, pd, pj = prev['k'], prev['d'], prev['j']

    # 低位
    is_low = (ck < K_MAX and cd < D_MAX and cj < J_MAX)
    # 上升
    is_rising = (ck > pk)

    meets = is_low and is_rising
    return (meets, ck, cd, cj, pk, pd, pj)


def process_stock(code, name):
    """获取单只股票的5分钟和30分钟KDJ数据"""
    result = {'code': code, 'name': name}

    # 5分钟KDJ
    k5 = fetch_kline_em(code, klt=5, lmt=PERIOD + 10)
    if k5 and len(k5) >= PERIOD + 1:
        kdj5 = calc_kdj(k5)
        if kdj5 and len(kdj5) >= 2:
            meets, ck, cd, cj, pk, pd, pj = check_kdj_signal(kdj5)
            result['kdj_5m'] = {
                'meets': meets,
                'k_now': ck, 'd_now': cd, 'j_now': cj,
                'k_prev': pk, 'd_prev': pd, 'j_prev': pj,
                'time': kdj5[-1]['date']
            }
        else:
            result['kdj_5m'] = {'meets': False, 'error': 'KDJ数据不足'}
    else:
        result['kdj_5m'] = {'meets': False, 'error': 'K线获取失败'}

    # 30分钟KDJ
    k30 = fetch_kline_em(code, klt=30, lmt=PERIOD + 10)
    if k30 and len(k30) >= PERIOD + 1:
        kdj30 = calc_kdj(k30)
        if kdj30 and len(kdj30) >= 2:
            meets, ck, cd, cj, pk, pd, pj = check_kdj_signal(kdj30)
            result['kdj_30m'] = {
                'meets': meets,
                'k_now': ck, 'd_now': cd, 'j_now': cj,
                'k_prev': pk, 'd_prev': pd, 'j_prev': pj,
                'time': kdj30[-1]['date']
            }
        else:
            result['kdj_30m'] = {'meets': False, 'error': 'KDJ数据不足'}
    else:
        result['kdj_30m'] = {'meets': False, 'error': 'K线获取失败'}

    # 综合分数
    bonus_5m = K_BONUS_5M if result['kdj_5m'].get('meets') else 0
    bonus_30m = K_BONUS_30M if result['kdj_30m'].get('meets') else 0
    result['kdj_bonus'] = bonus_5m + bonus_30m

    time.sleep(0.12)  # 反爬
    return result


def extract_tracked_codes():
    """从deploy/index.html提取当前STOCKS数组的代码（做T模块+30分选票）"""
    if not os.path.exists(INDEX_HTML):
        return []
    with open(INDEX_HTML, 'r', encoding='utf-8') as f:
        html = f.read()
    m = re.search(r'const STOCKS\s*=\s*(\[.*?\]);', html, re.DOTALL)
    if not m:
        return []
    stocks = json.loads(m.group(1))
    codes = []
    for s in stocks:
        pure = s['code'].replace('sh', '').replace('sz', '')
        codes.append((pure, s['name']))
    return codes


def extract_top200():
    """
    漏斗第二层：获取全市场TOP200候选池
    优先读 full_market_top200.json（predictor生成）
    若不存在，从 daily_predictions.json 的 all_results 实时提取
    返回: [(code, name), ...]
    """
    # 优先读独立文件
    if os.path.exists(TOP200_FILE):
        try:
            with open(TOP200_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            stocks = data.get('stocks', [])
            if stocks:
                print(f"  📂 从 full_market_top200.json 读取 {len(stocks)} 只候选票")
                return [(s['code'], s['name']) for s in stocks]
        except Exception as e:
            print(f"  ⚠️ 读取 full_market_top200.json 失败: {e}")

    # 回退：从 daily_predictions.json 实时提取
    daily_file = os.path.join(BASE, 'daily_predictions.json')
    if not os.path.exists(daily_file):
        print(f"  ⚠️ 未找到 daily_predictions.json，无法提取TOP200")
        return []

    try:
        with open(daily_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        print(f"  ⚠️ 读取 daily_predictions.json 失败: {e}")
        return []

    all_results = data.get('all_results', [])
    if not all_results:
        print(f"  ⚠️ daily_predictions.json 无 all_results")
        return []

    # 按总分+增强bonus排序取前200
    top200 = sorted(
        all_results,
        key=lambda x: x.get('total_score', 0) + x.get('metrics', {}).get('enhanced_bonus', 0),
        reverse=True
    )[:200]

    # 顺便生成独立文件供下次使用
    try:
        top200_output = {
            'scan_date': data.get('scan_date', ''),
            'total': len(top200),
            'source': 'extracted_from_daily_predictions',
            'stocks': [
                {
                    'code': r['code'],
                    'name': r['name'],
                    'total_score': r.get('total_score', 0),
                    'enhanced_bonus': r.get('metrics', {}).get('enhanced_bonus', 0),
                    'combined_score': round(r.get('total_score', 0) + r.get('metrics', {}).get('enhanced_bonus', 0), 1),
                    'rate_30': r.get('metrics', {}).get('rate_30', 0),
                    'drop_20d': r.get('metrics', {}).get('drop_20d', 0),
                }
                for r in top200
            ]
        }
        with open(TOP200_FILE, 'w', encoding='utf-8') as f:
            json.dump(top200_output, f, ensure_ascii=False, indent=2)
        print(f"  💾 已生成 full_market_top200.json ({len(top200)}只)")
    except Exception as e:
        print(f"  ⚠️ 生成 full_market_top200.json 失败: {e}")

    return [(r['code'], r['name']) for r in top200]


def main():
    now = datetime.now()
    print(f"\n{'='*60}")
    print(f"  KDJ 低位启动因子扫描（三层漏斗·第二层）")
    print(f"  扫描时间: {now.strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*60}\n")

    # ========== 漏斗第二层：全市场TOP200候选池 ==========
    print("📌 第一路：全市场TOP200候选池（漏斗第二层精细扫描）")
    top200 = extract_top200()
    print(f"  TOP200候选池: {len(top200)} 只票")

    # ========== 第二路：STOCKS 30只跟踪票（做T模块+30分选票） ==========
    print("\n📌 第二路：STOCKS 30只跟踪票（做T模块）")
    tracked = extract_tracked_codes()
    print(f"  STOCKS数组: {len(tracked)} 只票")
    for code, name in tracked:
        print(f"  {code} {name}")

    # 合并去重（TOP200为主，STOCKS补充）
    seen = set()
    scan_list = []
    for code, name in top200:
        if code not in seen:
            seen.add(code)
            scan_list.append((code, name, 'top200'))
    for code, name in tracked:
        if code not in seen:
            seen.add(code)
            scan_list.append((code, name, 'tracked'))

    print(f"\n📊 合并去重后共扫描: {len(scan_list)} 只票")
    print(f"   其中 TOP200: {len([x for x in scan_list if x[2]=='top200'])} 只")
    print(f"   其中 STOCKS补充: {len([x for x in scan_list if x[2]=='tracked'])} 只")

    if not scan_list:
        print("❌ 无可扫描票，退出")
        return

    # 多线程并发
    print(f"\n🔍 开始获取5分钟+30分钟K线并计算KDJ ({MAX_WORKERS}线程)...")
    results = {}
    success = 0
    errors = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(process_stock, code, name): (code, source)
            for code, name, source in scan_list
        }
        for i, future in enumerate(as_completed(futures)):
            code, source = futures[future]
            try:
                r = future.result()
                results[code] = {
                    'name': r['name'],
                    'source': source,  # 标记来源：top200 / tracked
                    'kdj_5m': r['kdj_5m'],
                    'kdj_30m': r['kdj_30m'],
                    'kdj_bonus': r['kdj_bonus'],
                }
                success += 1
            except Exception as e:
                errors.append(f"{code}: {e}")
            if (i + 1) % 20 == 0:
                print(f"  进度: {i+1}/{len(scan_list)} (成功{success})")

    print(f"\n完成: 成功 {success}, 失败 {len(errors)}")
    if errors:
        for e in errors[:5]:
            print(f"  ⚠️ {e}")

    # 打印满足条件的票
    print(f"\n=== 满足KDJ低位+上升条件的票 ===")
    hit_count = 0
    for code, data in sorted(results.items(), key=lambda x: -x[1]['kdj_bonus']):
        name = data['name']
        k5 = data['kdj_5m']
        k30 = data['kdj_30m']
        bonus = data['kdj_bonus']
        source_tag = "🔝TOP200" if data.get('source') == 'top200' else "📌STOCKS"
        if bonus > 0:
            hit_count += 1
            tags = []
            if k5.get('meets'):
                tags.append(f"5m(K={k5['k_now']:.1f} D={k5['d_now']:.1f} J={k5['j_now']:.1f})")
            if k30.get('meets'):
                tags.append(f"30m(K={k30['k_now']:.1f} D={k30['d_now']:.1f} J={k30['j_now']:.1f})")
            print(f"  ✅ {code} {name:8s} [{source_tag}] 加分+{bonus}  {' | '.join(tags)}")

    if hit_count == 0:
        print("  😔 暂无满足条件的票")

    # 输出
    output = {
        'scan_date': now.strftime('%Y-%m-%d %H:%M:%S'),
        'funnel_layer': '第二层·TOP200+STOCKS合并扫描',
        'total_top200': len(top200),
        'total_tracked': len(tracked),
        'total_scanned': len(scan_list),
        'total_success': success,
        'total_errors': len(errors),
        'config': {
            'k_max': K_MAX,
            'd_max': D_MAX,
            'j_max': J_MAX,
            'bonus_5m': K_BONUS_5M,
            'bonus_30m': K_BONUS_30M,
            'period': PERIOD,
        },
        'stocks': results,
    }

    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n✅ 输出: {OUTPUT_FILE}")
    print(f"   满足条件: {hit_count}/{success} 只\n")


if __name__ == '__main__':
    main()
