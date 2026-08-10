"""
基本面因子扫描模块 — 对TOP200做基本面扫描
因子：
9. PE历史分位（PE越低估值越便宜，加分）
10. ROE质量筛选（ROE>15%高质量，加分）
11. 业绩预告（近3个月预增公告，加分）
12. 概念热度标签（热门概念板块成员，加分）

数据源：东方财富行情接口
注意：建议在交易日9:30-15:00运行，API更稳定
"""
import urllib.request
import json
import time
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TOP200_FILE = os.path.join(BASE_DIR, 'full_market_top200.json')
OUTPUT_FILE = os.path.join(BASE_DIR, 'fundamental_factors.json')

MAX_WORKERS = 10
RETRY_TIMES = 2
TIMEOUT = 8


def fetch_basic_data(code):
    """
    获取单只票的基本面数据（PE/PB/ROE/流通市值）
    code: 6位代码（不带sh/sz前缀）
    """
    market = '1' if code.startswith('6') else '0'
    secid = f'{market}.{code}'
    url = f'https://push2.eastmoney.com/api/qt/stock/get?secid={secid}&fields=f57,f58,f162,f167,f173,f117,f37'

    for attempt in range(RETRY_TIMES):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            resp = urllib.request.urlopen(req, timeout=TIMEOUT)
            data = json.loads(resp.read().decode('utf-8'))
            d = data.get('data', {})
            if d:
                return {
                    'code': code,
                    'name': d.get('f58', ''),
                    'pe': d.get('f162', 0) / 100 if d.get('f162') else 0,  # PE动态
                    'pb': d.get('f167', 0) / 100 if d.get('f167') else 0,  # PB
                    'roe': d.get('f173', 0) / 100 if d.get('f173') else 0,  # ROE%
                    'circ_mv': d.get('f117', 0),  # 流通市值
                    'turnover': d.get('f37', 0) / 100 if d.get('f37') else 0,  # 换手率%
                }
            time.sleep(0.3)
        except Exception:
            time.sleep(0.5)
    return None


def calc_fundamental_bonus(data):
    """
    计算基本面因子加分
    9. PE历史分位：PE<20加5分，PE<30加3分（低估值优势）
    10. ROE质量：ROE>15%加5分，ROE>10%加3分（高质量公司）
    11. 业绩预告：TODO（需更稳定的API）
    12. 概念热度：TODO（需概念映射表）
    """
    bonus = 0
    factors = {}

    pe = data.get('pe', 0)
    pb = data.get('pb', 0)
    roe = data.get('roe', 0)
    turnover = data.get('turnover', 0)

    # 9. PE估值加分（PE越低越便宜，但PE<0亏损不给分）
    if 0 < pe < 15:
        pe_str = 5
    elif 15 <= pe < 25:
        pe_str = 3
    elif 25 <= pe < 40:
        pe_str = 1
    else:
        pe_str = 0  # 亏损或高估值
    bonus += pe_str
    factors['pe'] = {'value': pe, 'strength': pe_str}

    # 10. ROE质量加分
    if roe >= 20:
        roe_str = 5
    elif roe >= 15:
        roe_str = 4
    elif roe >= 10:
        roe_str = 3
    elif roe >= 5:
        roe_str = 1
    else:
        roe_str = 0
    bonus += roe_str
    factors['roe'] = {'value': roe, 'strength': roe_str}

    # 11. PB估值加分（PB<1.5低估）
    if 0 < pb < 1.0:
        pb_str = 3
    elif 1.0 <= pb < 1.5:
        pb_str = 2
    elif 1.5 <= pb < 2.5:
        pb_str = 1
    else:
        pb_str = 0
    bonus += pb_str
    factors['pb'] = {'value': pb, 'strength': pb_str}

    # 12. 换手率活跃度（1%-5%为健康，太高太低都不好）
    if 1 <= turnover <= 5:
        turn_str = 2
    elif 0.5 <= turnover < 1 or 5 < turnover <= 8:
        turn_str = 1
    else:
        turn_str = 0
    bonus += turn_str
    factors['turnover'] = {'value': turnover, 'strength': turn_str}

    # 业绩预告和概念热度标记为TODO
    factors['forecast'] = {'value': '需公告API', 'strength': 0}
    factors['concept'] = {'value': '需概念映射', 'strength': 0}

    return round(bonus, 1), factors


def scan_stock(code):
    """扫描单只票基本面"""
    data = fetch_basic_data(code)
    if not data:
        return None
    bonus, factors = calc_fundamental_bonus(data)
    return {
        'code': code,
        'name': data['name'],
        'pe': data['pe'],
        'pb': data['pb'],
        'roe': data['roe'],
        'turnover': data['turnover'],
        'circ_mv': data['circ_mv'],
        'fund_bonus': bonus,
        'factors': factors,
    }


def main():
    print(f"\n{'='*60}")
    print(f"  基本面因子扫描（TOP200）")
    print(f"{'='*60}\n")

    # 加载TOP200
    if not os.path.exists(TOP200_FILE):
        print(f"❌ 找不到 {TOP200_FILE}，请先运行 gap_up_predictor.py")
        return

    with open(TOP200_FILE) as f:
        top200 = json.load(f)

    codes = [s['code'] for s in top200.get('stocks', [])]
    print(f"📊 扫描 {len(codes)} 只票的基本面数据...")

    results = []
    completed = 0
    failed = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(scan_stock, c): c for c in codes}
        for future in as_completed(futures):
            completed += 1
            if completed % 50 == 0:
                print(f"  进度: {completed}/{len(codes)}")
            try:
                r = future.result()
                if r:
                    results.append(r)
                else:
                    failed += 1
            except:
                failed += 1

    print(f"\n  ✅ 成功: {len(results)} 只, 失败: {failed} 只")

    # 统计
    high_roe = sum(1 for r in results if r['roe'] >= 15)
    low_pe = sum(1 for r in results if 0 < r['pe'] < 25)
    low_pb = sum(1 for r in results if 0 < r['pb'] < 1.5)
    bonus_ge5 = sum(1 for r in results if r['fund_bonus'] >= 5)

    print(f"\n{'='*60}")
    print(f"  📊 基本面因子统计")
    print(f"{'='*60}")
    print(f"  ROE≥15%: {high_roe}只 ({high_roe*100//max(len(results),1)}%)")
    print(f"  PE<25:   {low_pe}只 ({low_pe*100//max(len(results),1)}%)")
    print(f"  PB<1.5:  {low_pb}只 ({low_pb*100//max(len(results),1)}%)")
    print(f"  bonus≥5: {bonus_ge5}只")

    # 保存
    output = {
        'scan_date': time.strftime('%Y-%m-%d %H:%M'),
        'total': len(results),
        'stocks': {r['code']: r for r in results},
    }
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n📁 结果已保存: {OUTPUT_FILE}")


if __name__ == '__main__':
    main()
