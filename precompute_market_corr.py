#!/usr/bin/env python3
"""
预计算大盘跟随度（Beta + 相关性）并输出 JS 代码片段
数据源：新浪财经日K线 API
  - 大盘基准：上证指数 (sh000001)
  - 个股：仪表盘中所有股票
计算：
  - Beta = Cov(stock_ret, mkt_ret) / Var(mkt_ret)
  - Correlation = Cov(stock_ret, mkt_ret) / (Std_stock * Std_mkt)
  - 5日同步率：最近5天股票与大盘同向涨跌的天数/5
分类：
  - 强跟随: R>0.7 & β>1.0
  - 跟随: R>0.5 & β>0.6
  - 弱关联: R>0.3
  - 独立: R<=0.3
  - 逆势: R<0
"""
import urllib.request, json, os, sys, re, time
from datetime import datetime

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
    'Referer': 'https://finance.sina.com.cn/'
}

def get_sina_kline(symbol, datalen=30):
    """新浪日K线 — 支持指数和个股"""
    url = f"http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol={symbol}&scale=240&ma=no&datalen={datalen}"
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=10) as r:
                return json.loads(r.read().decode())
        except Exception:
            if attempt < 2:
                time.sleep(0.5)
    return None

def calc_daily_returns(klines):
    """计算日收益率序列"""
    closes = [float(k['close']) for k in klines]
    returns = []
    for i in range(1, len(closes)):
        if closes[i-1] > 0:
            returns.append((closes[i] - closes[i-1]) / closes[i-1])
        else:
            returns.append(0.0)
    return returns

def calc_beta_corr(stock_returns, mkt_returns):
    """计算 Beta 和相关系数"""
    n = min(len(stock_returns), len(mkt_returns))
    if n < 5:
        return None, None
    
    # 对齐尾部
    sr = stock_returns[-n:]
    mr = mkt_returns[-n:]
    
    mean_s = sum(sr) / n
    mean_m = sum(mr) / n
    
    cov = sum((sr[i] - mean_s) * (mr[i] - mean_m) for i in range(n)) / n
    var_m = sum((mr[i] - mean_m) ** 2 for i in range(n)) / n
    var_s = sum((sr[i] - mean_s) ** 2 for i in range(n)) / n
    
    if var_m < 1e-12 or var_s < 1e-12:
        return None, None
    
    beta = cov / var_m
    corr = cov / (var_m ** 0.5 * var_s ** 0.5)
    
    return beta, corr

def calc_5d_alignment(stock_klines, mkt_klines):
    """计算最近5天同向涨跌的同步率"""
    n = min(len(stock_klines), len(mkt_klines), 6)  # 需要6根才能算5天涨跌
    if n < 2:
        return 0, 0
    
    aligned = 0
    total = 0
    for i in range(max(1, n - 5), n):
        s_prev = float(stock_klines[i-1]['close'])
        s_curr = float(stock_klines[i]['close'])
        m_prev = float(mkt_klines[i-1]['close'])
        m_curr = float(mkt_klines[i]['close'])
        
        s_dir = 1 if s_curr > s_prev else (-1 if s_curr < s_prev else 0)
        m_dir = 1 if m_curr > m_prev else (-1 if m_curr < m_prev else 0)
        
        if s_dir != 0 and m_dir != 0:
            total += 1
            if s_dir == m_dir:
                aligned += 1
    
    return aligned, total

def classify(beta, corr):
    """分类大盘跟随度"""
    if corr is None or beta is None:
        return 'unknown', 'var(--text-dim)'
    
    if corr < 0:
        return '逆势', 'var(--green)'
    elif corr > 0.7 and beta > 1.0:
        return '强跟随', 'var(--red)'
    elif corr > 0.5 and beta > 0.6:
        return '跟随', '#e8a735'
    elif corr > 0.3:
        return '弱关联', 'var(--text-dim)'
    else:
        return '独立', 'var(--blue)'

def extract_stock_codes(html_path):
    """从 HTML 中提取股票代码"""
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
                        raw = s['code']
                        if len(raw) > 6:
                            raw = raw.replace('sh', '').replace('sz', '')
                        all_codes.add(raw)
            except json.JSONDecodeError:
                pass
    return list(all_codes)

def main():
    html_path = sys.argv[1] if len(sys.argv) > 1 else 'deploy/index.html'
    
    codes = extract_stock_codes(html_path)
    print(f"// 提取 {len(codes)} 只股票代码", file=sys.stderr)
    
    # 1. 获取上证指数日K线
    print(f"// 拉取上证指数(sh000001)日K线...", file=sys.stderr)
    mkt_klines = get_sina_kline('sh000001', 30)
    if not mkt_klines or len(mkt_klines) < 10:
        print("const MARKET_CORR_CACHE = {};", )
        print(f"// ❌ 上证指数数据获取失败", file=sys.stderr)
        return
    
    mkt_returns = calc_daily_returns(mkt_klines)
    latest_mkt_date = mkt_klines[-1]['day'][:10] if mkt_klines else ''
    mkt_5d_chg = 0
    if len(mkt_klines) >= 6:
        mkt_5d_chg = (float(mkt_klines[-1]['close']) - float(mkt_klines[-5]['close'])) / float(mkt_klines[-5]['close']) * 100
    
    print(f"// 上证指数: {len(mkt_klines)}根K线, 最新={latest_mkt_date}, 5日涨跌={mkt_5d_chg:+.2f}%", file=sys.stderr)
    
    # 2. 逐个计算个股
    results = {}
    success = fail = 0
    
    for code in codes:
        sina_code = ('sh' if code.startswith('6') else 'sz') + code
        stock_klines = get_sina_kline(sina_code, 30)
        
        if not stock_klines or len(stock_klines) < 10:
            fail += 1
            continue
        
        stock_returns = calc_daily_returns(stock_klines)
        beta, corr = calc_beta_corr(stock_returns, mkt_returns)
        
        if beta is None:
            fail += 1
            continue
        
        aligned, total = calc_5d_alignment(stock_klines, mkt_klines)
        label, color = classify(beta, corr)
        
        # 个股5日涨跌
        stock_5d_chg = 0
        if len(stock_klines) >= 6:
            stock_5d_chg = (float(stock_klines[-1]['close']) - float(stock_klines[-5]['close'])) / float(stock_klines[-5]['close']) * 100
        
        results[code] = {
            'beta': round(beta, 2),
            'corr': round(corr, 3),
            'label': label,
            'color': color,
            'align_5d': f"{aligned}/{total}" if total > 0 else '--',
            'stock_5d_chg': round(stock_5d_chg, 2),
            'mkt_5d_chg': round(mkt_5d_chg, 2),
            'date': latest_mkt_date
        }
        success += 1
        time.sleep(0.15)
    
    print(f"// 大盘跟随度计算完成: 成功{success}, 失败{fail}, 数据日期={latest_mkt_date}", file=sys.stderr)
    
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    mkt_info = f"上证5日{mkt_5d_chg:+.2f}%"
    js = f"// 大盘跟随度预计算，生成时间: {now}\n"
    js += f"// 基准: 上证指数(sh000001), 数据日: {latest_mkt_date}, {mkt_info}\n"
    js += f"// 字段: beta=贝塔, corr=相关系数, label=分类, align_5d=5日同步率\n"
    js += f"const MARKET_CORR_CACHE = {json.dumps(results, ensure_ascii=False)};\n"
    
    print(js)


if __name__ == '__main__':
    main()
