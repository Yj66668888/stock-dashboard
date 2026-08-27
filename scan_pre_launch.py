#!/usr/bin/env python3
"""
低位启动前扫描器
================
从 daily_predictions top200 候选票中，拉30分钟K线计算KDJ(9,3,3)，
筛选出"30分钟KDJ仍在低位但即将金叉/底背离"的票 — 即还没涨起来的启动前候选。

筛选条件：
1. 30分钟KDJ的K值 < 40（低位）
2. K正在上升（K > prev_K）或即将金叉（K逼近D）
3. 排除已大幅上涨的票（当日涨幅 > 5% 排除）
4. 优先级：底背离 > 即将金叉 > 低位回升
5. 5分钟KDJ低位过滤（2026-08-27新增）：5分K>=65 剔除（已拉起的不要），
   5分K<30且上拐加分——保证推送时5分钟也在低位，不追高
"""
import json, subprocess, time, os, sys
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed


def drop_incomplete_kl(klines):
    """丢弃盘中未走完的当根K线（2026-08-27新增）。
    新浪day字段为bar终点标注：bar时间 > 当前时间 → 该bar未走完，K值临时不可靠，
    曾导致"推送时看着要拐头，之后一路向下"的半根K线幻觉"""
    if not klines:
        return klines
    try:
        bar_time = datetime.strptime(klines[-1][0], '%Y-%m-%d %H:%M:%S')
    except (ValueError, TypeError):
        return klines
    if bar_time > datetime.now():
        return klines[:-1]
    return klines

# 微信推送
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from wechat_notify import send_wechat
    WECHAT_ENABLED = True
except ImportError:
    WECHAT_ENABLED = False

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

def add_prefix(code):
    """纯数字代码加sh/sz前缀"""
    if code.startswith(('6', '9')):
        return 'sh' + code
    else:
        return 'sz' + code

def fetch_sina_kline(code, scale, datalen):
    """新浪K线API"""
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

def calc_kdj(klines, n=9):
    """KDJ(9,3,3) — 返回最近几根的K/D/J值"""
    if not klines or len(klines) < n:
        return []
    prev_k, prev_d = 50.0, 50.0
    results = []
    for i in range(len(klines)):
        start = max(0, i - n + 1)
        hn = max(float(kl[2]) for kl in klines[start:i+1])
        ln = min(float(kl[3]) for kl in klines[start:i+1])
        c = float(klines[i][4])
        rsv = (c - ln) / (hn - ln) * 100 if hn != ln else 50
        k = 2/3 * prev_k + 1/3 * rsv
        d = 2/3 * prev_d + 1/3 * k
        j = 3 * k - 2 * d
        results.append({
            'k': round(k, 2), 'd': round(d, 2), 'j': round(j, 2),
            'close': c, 'low': float(klines[i][3]), 'high': float(klines[i][2]),
            'vol': float(klines[i][5]),
        })
        prev_k, prev_d = k, d
    return results

def fetch_realtime(code):
    """新浪实时行情 — 获取当前价和涨幅（GBK编码）"""
    prefix = 'sh' if code.startswith('sh') else 'sz'
    num = code[2:]
    url = f'https://hq.sinajs.cn/list={prefix}{num}'
    try:
        r = subprocess.run(
            ['curl', '-sL', '--max-time', '5', url,
             '-H', 'Referer: https://finance.sina.com.cn'],
            capture_output=True, timeout=8
        )
        raw = r.stdout.decode('gbk', errors='ignore')
    except:
        return None, None
    if not raw or '"' not in raw:
        return None, None
    try:
        parts = raw.split('"')[1].split(',')
        price = float(parts[3])
        prev_close = float(parts[2])
        chg = (price - prev_close) / prev_close * 100 if prev_close else 0
        return round(chg, 2), price
    except:
        return None, None

def detect_bottom_divergence(kdj_series, lookback=20):
    """30分钟KDJ底背离"""
    if len(kdj_series) < lookback:
        return False, ''
    recent = kdj_series[-lookback:]
    mid = len(recent) // 2
    
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
    
    if price2 < price1 and k2 > k1:
        if kdj_series[-1]['k'] < 65:
            return True, f'底背离: 价{price1:.2f}->{price2:.2f}, K{k1:.1f}->{k2:.1f}'
    
    ratio = abs(price2 - price1) / max(0.01, price1)
    if ratio < 0.03 and k2 > k1 + 5:
        return True, f'类背离: 价持平, K回升{k1:.1f}->{k2:.1f}'
    
    return False, ''

def scan_one(stock_info):
    """扫描单只股票"""
    code, name, score = stock_info
    full_code = add_prefix(code)
    
    # 排除创业板/科创板/北交所
    num = code
    if num.startswith('30') or num.startswith('68') or num.startswith('8') or num.startswith('4'):
        return None
    if num.startswith('00') or num.startswith('60'):
        pass  # 主板OK
    else:
        return None
    
    # 1. 获取30分钟K线（丢弃未走完的当根）
    kl30 = drop_incomplete_kl(fetch_sina_kline(full_code, 30, 100))
    if not kl30 or len(kl30) < 12:
        return None
    
    # 2. 计算KDJ
    kdj = calc_kdj(kl30, 9)
    if len(kdj) < 5:
        return None
    
    curr = kdj[-1]
    prev = kdj[-2]
    k, d, j = curr['k'], curr['d'], curr['j']
    prev_k = prev['k']
    
    # 3. 筛选条件
    # K必须低位（< 40）
    if k > 40:
        return None
    
    # K正在上升或即将金叉（2026-08-27加固：单根上拐→连续2根上拐，
    # 避免下跌途中一根反抽就触发推送；真金叉交叉本身是强确认，保持单根）
    prev2 = kdj[-3]
    prev_prev_k = prev2['k']
    two_bar_rise = k > prev_k and prev_k >= prev_prev_k
    k_rising = two_bar_rise
    k_approaching_d = k < d and (d - k) < 5 and two_bar_rise
    just_golden = prev_k < prev['d'] and k >= d and k < 35  # 刚金叉且低位
    
    if not (k_rising or k_approaching_d or just_golden):
        return None

    # ---- 3b. 5分钟KDJ低位检查（2026-08-27新增：推送时5分钟不在高位） ----
    kl5 = drop_incomplete_kl(fetch_sina_kline(full_code, 5, 100))
    kdj5_series = calc_kdj(kl5, 9) if kl5 and len(kl5) >= 9 else []
    k5_info = None
    if len(kdj5_series) >= 3:
        c5 = kdj5_series[-1]
        p5 = kdj5_series[-2]
        k5_info = {'k': c5['k'], 'd': c5['d'], 'j': c5['j'], 'prev_k': p5['k']}
        if c5['k'] >= 65:   # 5分钟已拉起 → 剔除，避免推送即高点
            return None
    else:
        return None  # 5分钟K线拉不到 → 保守剔除
    
    # 4. 获取实时涨幅
    chg, price = fetch_realtime(full_code)
    if chg is not None and chg > 5:  # 排除已经大涨的
        return None
    
    # 5. 底背离检测
    div, div_detail = detect_bottom_divergence(kdj, 20)
    
    # 6. 缩量检测（最近5根vs前面5根）
    vols = [kl[5] for kl in kl30[-10:]]
    recent_vol = sum(vols[-5:]) / 5 if len(vols) >= 5 else 0
    prev_vol = sum(vols[:5]) / 5 if len(vols) >= 5 else 0
    vol_shrink = recent_vol < prev_vol * 0.7 if prev_vol > 0 else False
    
    # 7. 计算综合得分
    signals = []
    total_score = 0
    
    if div:
        signals.append(f'底背离({div_detail})')
        total_score += 30
    
    if just_golden:
        signals.append(f'30分KDJ刚金叉(K={k:.1f}/D={d:.1f})')
        total_score += 25
    elif k_approaching_d:
        signals.append(f'30分KDJ即将金叉(K={k:.1f}逼近D={d:.1f})')
        total_score += 20
    elif k_rising:
        signals.append(f'30分KDJ低位回升(K={k:.1f},前值={prev_k:.1f})')
        total_score += 12
    
    if vol_shrink:
        signals.append('缩量止跌(近5根量<前5根70%)')
        total_score += 10
    
    if k < 20:
        signals.append(f'超低位(K={k:.1f})')
        total_score += 8
    
    if j < 0:
        signals.append(f'J值负值(J={j:.1f},超卖)')
        total_score += 5
    
    # 5分钟KDJ加分（2026-08-27新增）
    if k5_info:
        k5, k5_prev, j5 = k5_info['k'], k5_info['prev_k'], k5_info['j']
        if k5 < 30 and k5 > k5_prev:
            signals.append(f'5分低位上拐(K5={k5:.1f})')
            total_score += 10
        elif k5 < 30:
            signals.append(f'5分超卖待拐(K5={k5:.1f})')
            total_score += 6
        elif k5 < 45:
            signals.append(f'5分偏低(K5={k5:.1f})')
            total_score += 3
        else:
            signals.append(f'5分中位(K5={k5:.1f})')
    
    # 日线预测分加成
    total_score = total_score + min(10, score / 10)
    
    return {
        'code': full_code,
        'name': name,
        'chg_pct': chg or 0,
        'price': price or 0,
        'kd30_k': k,
        'kd30_d': d,
        'kd30_j': j,
        'kd30_prev_k': prev_k,
        'trend30': ('up' if k > d else
                    ('prep_up' if (k - d) > (prev_k - prev['d']) or k > prev_k else 'down')),
        'kd5_k': k5_info['k'] if k5_info else None,
        'kd5_d': k5_info['d'] if k5_info else None,
        'kd5_j': k5_info['j'] if k5_info else None,
        'k_rising': k_rising,
        'just_golden': just_golden,
        'approaching_cross': k_approaching_d,
        'bottom_divergence': div,
        'vol_shrink': vol_shrink,
        'signals': signals,
        'pre_launch_score': round(total_score, 1),
        'daily_score': score,
    }

def main():
    print('='*60)
    print('  低位启动前扫描器 — 找30分钟KDJ还在低位的启动前候选')
    print('='*60)
    print()
    
    # 从仪表盘 deploy/index.html 读取当天选出的 STOCKS（30只）
    import re
    html_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'deploy', 'index.html')
    with open(html_path, encoding='utf-8') as f:
        html = f.read()
    m = re.search(r'(const STOCKS\s*=\s*)(\[.*?\])(\s*;\s*\n)', html, re.S)
    if not m:
        print('❌ 未找到 STOCKS 数组，退出'); return
    dashboard_stocks = json.loads(m.group(2))
    
    # 去重
    seen = set()
    stock_list = []
    for s in dashboard_stocks:
        code = s.get('code', '')
        if code not in seen:
            seen.add(code)
            stock_list.append((code, s.get('name', ''), s.get('healthScore', 0)))
    
    print(f'候选池: {len(stock_list)}只 (仪表盘当日选票)')
    print(f'扫描中... (3线程并发拉30分钟K线)')
    print()
    
    results = []
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(scan_one, s): s for s in stock_list}
        done = 0
        for future in as_completed(futures):
            done += 1
            if done % 10 == 0:
                print(f'  进度: {done}/{len(stock_list)}')
            try:
                r = future.result()
                if r:
                    results.append(r)
            except:
                pass
    
    # 按得分排序
    results.sort(key=lambda x: x['pre_launch_score'], reverse=True)
    
    print(f'\n扫描完成: {len(stock_list)}只中筛选出 {len(results)}只低位启动前候选\n')
    print('='*80)
    print(f'{"排名":>4} {"代码":<10} {"名称":<8} {"涨幅":>6} {"30分K":>6} {"30分D":>6} {"30分J":>6} {"5分K":>6} {"得分":>6}  信号')
    print('-'*80)
    
    for i, r in enumerate(results[:30]):
        signals_str = ' | '.join(r['signals']) if r['signals'] else ''
        print(f'{i+1:>4}  {r["code"]:<10} {r["name"]:<8} {r["chg_pct"]:>+5.1f}% {r["kd30_k"]:>6.1f} {r["kd30_d"]:>6.1f} {r["kd30_j"]:>6.1f} {r["kd5_k"] if r.get("kd5_k") is not None else "--":>6} {r["pre_launch_score"]:>6.1f}  {signals_str}')
    
    print('='*80)
    
    # 输出JSON
    output = {
        'scan_time': time.strftime('%Y-%m-%d %H:%M:%S'),
        'total_scanned': len(stock_list),
        'total_qualified': len(results),
        'top30': results[:30],
    }
    
    with open('pre_launch_candidates.json', 'w') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    
    print(f'\n结果已保存: pre_launch_candidates.json')
    print(f'TOP 30 已输出上方表格')
    
    # 微信推送: 如果发现优质低位启动前候选, 推送通知
    if WECHAT_ENABLED and results:
        top5 = results[:5]
        if top5:
            names_str = '/'.join(r['name'] for r in top5)
            title = f"仪表盘选票低位启动{len(results)}只: {names_str}"
            lines = [
                f"## 低位启动前扫描（仪表盘选票）\n",
                f"> 扫描时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n",
                f"> 扫描范围: 仪表盘{len(stock_list)}只选票 | 筛出: {len(results)}只\n\n",
                f"---\n\n",
            ]
            for i, r in enumerate(top5, 1):
                signals_str = ' / '.join(r['signals']) if r['signals'] else '无'
                trend_txt = {'up': '↑ 上升(K>D)', 'prep_up': '↗ 待升(收敛/上拐)',
                             'down': '↓ 下跌(左侧观察,勿急)'}[r.get('trend30', 'down')]
                lines.append(f"### {i}. {r['name']} {r['code']}")
                lines.append(f"- 得分: **{r['pre_launch_score']:.1f}**")
                lines.append(f"- 涨幅: {r['chg_pct']:+.1f}%")
                lines.append(f"- 30分K趋势: {trend_txt}")
                lines.append(f"- 30分KDJ: K={r['kd30_k']:.1f} D={r['kd30_d']:.1f} J={r['kd30_j']:.1f}")
                k5 = r.get('kd5_k')
                if k5 is not None:
                    lines.append(f"- 5分KDJ: K={k5:.1f} D={r['kd5_d']:.1f} J={r['kd5_j']:.1f}（低位=低吸窗口，高位勿追）")
                lines.append(f"- 信号: {signals_str}\n")
            try:
                send_wechat(title, '\n'.join(lines), push_type='prelaunch')
            except Exception as e:
                print(f"[WeChat] 推送异常(不影响扫描): {e}")
    
    return results[:30]

if __name__ == '__main__':
    main()
