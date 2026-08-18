#!/usr/bin/env python3
"""
预计算 KDJ 指标并输出为 JS 代码片段
从新浪财经 API 获取5分钟/30分钟K线数据，计算 KDJ(9,3,3)，
然后直接嵌入 HTML，避免浏览器端 CORS 问题。

数据源: http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData
  - scale=5  → 5分钟K线
  - scale=30 → 30分钟K线
"""
import json
import re
import sys
import urllib.request
import urllib.error
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

TENCENT_MKLINE_URL = "https://ifzq.gtimg.cn/appstock/app/kline/mkline?param={code},{mtf},,{count}"
CURL_HEADERS = [
    '-H', 'Referer: https://gu.qq.com/',
    '-H', 'User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
]


def curl_json(url, timeout=10, retries=5):
    """用 curl 拉取 JSON（--noproxy 绕开本地代理污染），返回 dict 或 None"""
    import subprocess
    cmd = ['curl', '-s', '--noproxy', '*', '--max-time', str(timeout)] + CURL_HEADERS + [url]
    for i in range(retries):
        try:
            out = subprocess.run(cmd, capture_output=True, timeout=timeout + 5).stdout
            if out:
                return json.loads(out.decode('utf-8', errors='replace'))
        except Exception:
            pass
        time.sleep(0.5 + i * 0.5)
    return None


def extract_stock_codes(html_path):
    """从 HTML 中提取 STOCKS 和 LAUNCH_STOCKS 的股票代码"""
    with open(html_path, 'r', encoding='utf-8') as f:
        html = f.read()

    all_codes = set()

    for pattern in [r'const STOCKS\s*=\s*(\[.*?\]);', r'const LAUNCH_STOCKS\s*=\s*(\[.*?\]);']:
        m = re.search(pattern, html, re.DOTALL)
        if m:
            try:
                stocks = json.loads(m.group(1))
                for s in stocks:
                    if 'code' in s:
                        all_codes.add(s['code'])
            except json.JSONDecodeError:
                codes = re.findall(r'"code"\s*:\s*"(\w+)"', m.group(1))
                all_codes.update(codes)

    return list(all_codes)


def fetch_sina_klines(code, scale=5):
    """从腾讯 mkline 获取K线数据（新浪已封IP，2026-08-18切换）

    Args:
        code: 股票代码，如 sh600519, sz000001
        scale: K线周期，5=5分钟, 30=30分钟
    Returns:
        list of dict: [{date, open, high, low, close, volume}, ...]
    """
    mtf = f"m{scale}"
    url = TENCENT_MKLINE_URL.format(code=code, mtf=mtf, count=300)

    try:
        d = curl_json(url)
        bars = d.get('data', {}).get(code, {}).get(mtf)
        if not bars:
            return None

        klines = []
        for b in bars:
            # 腾讯格式: [time, open, close, high, low, volume]
            try:
                k = {
                    'date': b[0],
                    'open': float(b[1]),
                    'high': float(b[3]),
                    'low': float(b[4]),
                    'close': float(b[2]),
                    'volume': float(b[5]) if len(b) > 5 else 0,
                }
                klines.append(k)
            except (ValueError, IndexError, TypeError):
                continue

        return klines if klines else None
    except Exception:
        return None


def calc_kdj(klines, n=9):
    """计算 KDJ(9,3,3)，返回最后两根的 K/D/J

    算法与通达信一致：
      RSV = (Close - LLV(Low, n)) / (HHV(High, n) - LLV(Low, n)) * 100
      K = 2/3 * 前K + 1/3 * RSV
      D = 2/3 * 前D + 1/3 * K
      J = 3*K - 2*D
    初始 K=50, D=50
    """
    if not klines or len(klines) < n:
        return None

    prev_k = 50.0
    prev_d = 50.0

    second_last_k = None
    second_last_d = None

    for i in range(n - 1, len(klines)):
        # 近 n 根的最高价和最低价
        window = klines[i - n + 1:i + 1]
        hh = max(k['high'] for k in window)
        ll = min(k['low'] for k in window)

        if hh > ll:
            rsv = ((klines[i]['close'] - ll) / (hh - ll)) * 100
        else:
            rsv = 50.0

        k_val = (2.0 / 3.0) * prev_k + (1.0 / 3.0) * rsv
        d_val = (2.0 / 3.0) * prev_d + (1.0 / 3.0) * k_val
        j_val = 3.0 * k_val - 2.0 * d_val

        # 记录倒数第二根的值
        if i == len(klines) - 2:
            second_last_k = k_val
            second_last_d = d_val

        prev_k = k_val
        prev_d = d_val

    return {
        'k': round(prev_k, 2),
        'd': round(prev_d, 2),
        'j': round(j_val, 2),
        'prev_k': round(second_last_k, 2) if second_last_k is not None else None,
        'prev_d': round(second_last_d, 2) if second_last_d is not None else None,
    }


def compute_kdj_for_code(code):
    """计算单只股票的 5分钟和30分钟 KDJ"""
    # 5分钟K线
    k5 = fetch_sina_klines(code, scale=5)
    if not k5 or len(k5) < 10:
        return None

    kdj5 = calc_kdj(k5, 9)
    if not kdj5:
        return None

    # 30分钟K线（直接获取，不再从5分钟合成）
    k30 = fetch_sina_klines(code, scale=30)
    if k30 and len(k30) >= 10:
        kdj30 = calc_kdj(k30, 9)
    else:
        kdj30 = None

    return {
        'code': code,
        'kdj5': kdj5,
        'kdj30': kdj30,
        'last5minDate': k5[-1]['date'] if k5 else None,
        'last30minDate': k30[-1]['date'] if k30 else None,
    }


def main():
    html_path = sys.argv[1] if len(sys.argv) > 1 else 'deploy/index.html'

    codes = extract_stock_codes(html_path)
    if not codes:
        print("// 未找到股票代码", file=sys.stderr)
        print("const KDJ_PRECOMPUTED = {};")
        return

    print(f"// 预计算 {len(codes)} 只股票的 KDJ...", file=sys.stderr)

    results = {}
    success = 0
    fail = 0

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(compute_kdj_for_code, code): code for code in codes}
        for future in as_completed(futures):
            code = futures[future]
            try:
                result = future.result(timeout=15)
                if result:
                    results[code] = result
                    success += 1
                else:
                    fail += 1
            except Exception:
                fail += 1

    print(f"// KDJ预计算完成: 成功{success}, 失败{fail}", file=sys.stderr)

    # 输出为 JS 变量
    now = time.strftime('%Y-%m-%d %H:%M:%S')
    js = f"// KDJ 预计算数据，生成时间: {now}\n"
    js += f"// 数据源: 新浪财经 5分钟/30分钟K线 → KDJ(9,3,3)\n"
    js += f"const KDJ_PRECOMPUTED = {json.dumps(results, ensure_ascii=False)};\n"

    print(js)


if __name__ == '__main__':
    main()
