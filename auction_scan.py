#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
竞价异动扫描 — 手动诊断工具（2026-08-23起不再微信推送）
功能已迁移到仪表盘「竞价监测」面板（deploy/index.html，前端腾讯源实时刷新）
本脚本仅保留命令行快照用途，结果存 auction_scan.json

数据源: 东方财富 push2.eastmoney.com (实时) / push2delay (备用)
字段: f2=最新价 f5=成交量 f6=成交额 f8=换手率 f12=代码 f14=名称 f17=今开 f18=昨收
"""
import json, re, subprocess, time, os
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE = os.path.dirname(os.path.abspath(__file__))
HTML = os.path.join(BASE, 'deploy', 'index.html')
RESULT_FILE = os.path.join(BASE, 'auction_scan.json')

_CLEAN_ENV = {k: v for k, v in os.environ.items() if 'proxy' not in k.lower()}


# ============================================================
# 通用工具
# ============================================================

def curl_get_json(url, retries=3, timeout=10):
    """curl --noproxy 绕代理获取JSON（同 enrich_missing_fields.py 方案）"""
    for attempt in range(retries):
        try:
            r = subprocess.run(
                ['curl', '-s', '--noproxy', '*', '--connect-timeout', str(timeout),
                 '-H', 'User-Agent: Mozilla/5.0', url],
                capture_output=True, text=True, timeout=timeout + 5, env=_CLEAN_ENV)
            if r.returncode == 0 and r.stdout.strip():
                return json.loads(r.stdout)
        except Exception:
            pass
        if attempt < retries - 1:
            time.sleep(0.5)
    return None


def pure_code(c):
    return c[-6:]


def to_secid(code):
    """sh600000 -> 1.600000, sz000001 -> 0.000001"""
    p = pure_code(code)
    return ('1.' if p.startswith('6') else '0.') + p


# ============================================================
# 读取仪表盘选票
# ============================================================

def load_dashboard_stocks():
    """从 deploy/index.html 读取当天 STOCKS 数组"""
    if not os.path.exists(HTML):
        print(f'❌ 找不到 {HTML}')
        return []
    with open(HTML, encoding='utf-8') as f:
        html = f.read()
    m = re.search(r'(const STOCKS\s*=\s*)(\[.*?\])(\s*;\s*\n)', html, re.S)
    if not m:
        print('❌ 未找到 STOCKS 数组')
        return []
    stocks = json.loads(m.group(2))
    # 去重
    seen = set()
    result = []
    for s in stocks:
        code = s.get('code', '')
        if code not in seen:
            seen.add(code)
            result.append(s)
    return result


# ============================================================
# 竞价数据批量获取
# ============================================================

def fetch_auction_batch(codes):
    """批量获取竞价数据（东方财富 ulist.np 接口）

    Returns: list of dict
        code, name, price(竞价/最新价), yclose(昨收),
        volume(成交量/手), amount(成交额/元), turnover(换手率%),
        gap_pct(高开百分比)
    """
    secids = ','.join(to_secid(c) for c in codes)
    # f2=最新价 f5=成交量 f6=成交额 f8=换手率 f12=代码 f14=名称 f17=今开 f18=昨收
    fields = 'f2,f5,f6,f8,f12,f14,f17,f18'
    url = (f'https://push2.eastmoney.com/api/qt/ulist.np/get'
           f'?secids={secids}&fields={fields}&fltt=2&invt=2')

    print(f'  请求: push2.eastmoney.com ({len(codes)}只)')
    d = curl_get_json(url)

    # 备用: push2delay
    if not d or not d.get('data') or not d['data'].get('diff'):
        print(f'  push2 失败, 尝试 push2delay...')
        url2 = url.replace('push2.eastmoney.com', 'push2delay.eastmoney.com')
        d = curl_get_json(url2)

    if not d or not d.get('data') or not d.get('data', {}).get('diff'):
        print('❌ 竞价数据获取失败 (两个源均无响应)')
        return []

    results = []
    for item in d['data']['diff']:
        code_num = str(item.get('f12', ''))
        name = item.get('f14', '')
        price = item.get('f2')
        yclose = item.get('f18')
        volume = item.get('f5')
        amount = item.get('f6')
        turnover = item.get('f8')
        open_price = item.get('f17')

        # 跳过空值
        if price is None or price == '-' or yclose is None or yclose == '-':
            continue

        try:
            price = float(price)
            yclose = float(yclose)
        except (TypeError, ValueError):
            continue

        gap_pct = round((price - yclose) / yclose * 100, 2) if yclose > 0 else 0.0

        # 还原 sh/sz 前缀
        prefix = 'sh' if code_num.startswith('6') else 'sz'
        code = f'{prefix}{code_num}'

        results.append({
            'code': code,
            'name': name,
            'price': price,
            'yclose': yclose,
            'open': float(open_price) if open_price and open_price != '-' else None,
            'volume': int(volume) if volume and volume != '-' else 0,
            'amount': float(amount) if amount and amount != '-' else 0.0,
            'turnover': float(turnover) if turnover and turnover != '-' else None,
            'gap_pct': gap_pct,
        })

    return results


# ============================================================
# 竞价信号分析
# ============================================================

# 信号阈值
HIGH_OPEN_STRONG = 5.0    # 大幅高开
HIGH_OPEN_MILD   = 2.0    # 竞价高开
LOW_OPEN_MILD    = -2.0   # 竞价低开
LOW_OPEN_STRONG  = -5.0   # 大幅低开
BIG_AMOUNT_WAN   = 1000   # 竞价金额>1000万


def is_trading_day():
    """判断今天是否为交易日（周六日直接跳过，节假日API无法判断由数据空值兜底）"""
    now = datetime.now()
    # 周末直接跳过
    if now.weekday() >= 5:  # 5=周六, 6=周日
        return False
    return True


def analyze_auction(stocks_data):
    """筛选竞价异动票

    主信号（必须满足至少一个才推送）:
    1. 高开 >= 5%: 大幅高开
    2. 高开 >= 2%: 竞价高开
    3. 低开 <= -5%: 大幅低开
    4. 低开 <= -2%: 竞价低开

    辅助信号（仅在主信号存在时附加，不单独触发）:
    5. 竞价金额 > 1000万: 量能放大
    """
    signals = []
    for s in stocks_data:
        gap = s['gap_pct']
        amount_wan = s['amount'] / 1e4  # 元 → 万元

        # 主信号
        main_sigs = []
        if gap >= HIGH_OPEN_STRONG:
            main_sigs.append(f'大幅高开{gap:.1f}%')
        elif gap >= HIGH_OPEN_MILD:
            main_sigs.append(f'竞价高开{gap:.1f}%')
        elif gap <= LOW_OPEN_STRONG:
            main_sigs.append(f'大幅低开{gap:.1f}%')
        elif gap <= LOW_OPEN_MILD:
            main_sigs.append(f'竞价低开{gap:.1f}%')

        # 没有主信号就不推送（金额信号不单独触发）
        if not main_sigs:
            continue

        # 辅助信号
        all_sigs = list(main_sigs)
        if amount_wan >= BIG_AMOUNT_WAN:
            all_sigs.append(f'竞价金额{amount_wan:.0f}万')

        s['signals'] = all_sigs
        s['amount_wan'] = round(amount_wan, 0)
        signals.append(s)

    # 按高开幅度排序（高开在前，低开在后）
    signals.sort(key=lambda x: x['gap_pct'], reverse=True)
    return signals


# ============================================================
# 主入口
# ============================================================

def main():
    print('=' * 60)
    print(f'  竞价异动扫描 — {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    print('=' * 60)
    print()

    # 0. 交易日检查
    if not is_trading_day():
        print('⛔ 今天非交易日（周末），跳过竞价扫描')
        return

    # 1. 加载仪表盘选票
    stocks = load_dashboard_stocks()
    if not stocks:
        print('❌ 仪表盘无选票，退出')
        return

    codes = [s['code'] for s in stocks]
    print(f'仪表盘选票: {len(codes)}只')
    print(f'股票: {", ".join(s["name"] for s in stocks)}')
    print()

    # 2. 获取竞价数据
    print('正在获取竞价数据...')
    auction_data = fetch_auction_batch(codes)
    print(f'获取成功: {len(auction_data)}/{len(codes)}只')
    print()

    if not auction_data:
        print('❌ 无竞价数据，退出')
        return

    # 3. 打印全部竞价概览（只标高开/低开信号）
    print(f'{"代码":<10} {"名称":<8} {"昨收":>8} {"竞价":>8} {"高开":>7} {"金额(万)":>10} 信号')
    print('-' * 70)
    for s in sorted(auction_data, key=lambda x: x['gap_pct'], reverse=True):
        aw = s['amount'] / 1e4
        sig = ''
        gap = s['gap_pct']
        if gap >= 5:
            sig = '★大幅高开'
        elif gap >= 2:
            sig = '↑竞价高开'
        elif gap <= -5:
            sig = '★大幅低开'
        elif gap <= -2:
            sig = '↓竞价低开'
        print(f'{s["code"]:<10} {s["name"]:<8} {s["yclose"]:>8.2f} {s["price"]:>8.2f} {gap:>+6.1f}% {aw:>10.0f}  {sig}')
    print('-' * 70)
    print()

    # 4. 分析信号
    signals = analyze_auction(auction_data)

    # 5. 保存结果
    result = {
        'scan_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'total_stocks': len(codes),
        'total_fetched': len(auction_data),
        'signal_count': len(signals),
        'all_data': auction_data,
        'signals': signals,
    }
    with open(RESULT_FILE, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f'结果已保存: {RESULT_FILE}')
    print()

    # 6. 结果说明（不再微信推送，实时监控看仪表盘「竞价监测」面板）
    if not signals:
        print('✅ 今日竞价无异常')
        print('ℹ️ 实时竞价监控请看仪表盘「竞价监测」面板')
        return

    print(f'发现 {len(signals)} 只竞价异动票:')
    for s in signals:
        print(f'  {s["name"]:6s} {s["code"]} 高开{s["gap_pct"]:+.1f}% 金额{s["amount_wan"]:.0f}万 信号: {" | ".join(s["signals"])}')
    print()
    print('ℹ️ 已停止微信推送，实时竞价监控请看仪表盘「竞价监测」面板')


if __name__ == '__main__':
    main()
