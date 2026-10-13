#!/usr/bin/env python3
"""盘中实时数据刷新脚本（2026-10-12 加）

目的: 把 STOCKS 中的盘中字段（close/dayChg/turnover/volRatio/flowDate）
      全部更新到当前盘中最准实时值，让浏览器打开仪表盘时第一眼就是 10/12 盘中最新数据。
      收盘后才能算的指标（KDJ/MACD/资金流等）保持上一交易日值不变。

数据源: qt.gtimg.cn（稳定，腾讯主站，对实时行情最全最快）
"""
import re, json, urllib.request, time, sys, os

BASE = os.path.dirname(os.path.abspath(__file__))
DEPLOY = os.path.join(BASE, "deploy", "index.html")
DOCS = os.path.join(BASE, "docs", "index.html")


def fetch_qt_batch(codes, max_per_req=80):
    """qt.gtimg.cn 批量拉实时行情，GBK 解码。返回 {code: dict}"""
    out = {}
    for i in range(0, len(codes), max_per_req):
        chunk = codes[i:i + max_per_req]
        url = 'http://qt.gtimg.cn/q=' + ','.join(chunk)
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=10) as r:
                raw = r.read()
            data = raw.decode('gbk', errors='ignore')
            for line in data.split(';'):
                m = re.match(r'v_(\w+)="([^"]*)"', line.strip())
                if not m:
                    continue
                code = m.group(1)
                p = m.group(2).split('~')
                if len(p) < 50:
                    continue
                try:
                    out[code] = {
                        'close': float(p[3]) if p[3] else None,
                        'prev_close': float(p[4]) if p[4] else None,
                        'chg': float(p[31]) if p[31] else None,
                        'chg_pct': float(p[32]) if p[32] else None,
                        'turnover': float(p[38]) if p[38] else None,
                        'volRatio': float(p[49]) if p[49] else None,
                        'vol_shou': float(p[6]) if p[6] else None,  # 成交手数
                        'timestamp': p[30],
                    }
                except (ValueError, IndexError):
                    pass
        except Exception as e:
            print(f"  [WARN] qt 拉取 chunk {i}~{i+len(chunk)} 失败: {e}")
    return out


def update_stocks_with_live(html_text, qt_data, today_str):
    """把 STOCKS 中每只票的盘中字段更新到 qt 实时值"""
    m = re.search(r'const\s+STOCKS\s*=\s*(\[.*?\]);', html_text, re.DOTALL)
    if not m:
        print("  ❌ 未找到 STOCKS 数组")
        return html_text, 0, 0
    raw = m.group(1)
    stocks = json.loads(raw)
    ok, miss = 0, 0
    for s in stocks:
        code = s.get('code')
        d = qt_data.get(code)
        if not d or not d.get('close'):
            miss += 1
            continue
        s['close'] = round(d['close'], 2)
        if d.get('chg_pct') is not None:
            s['dayChg'] = round(d['chg_pct'], 2)
        if d.get('turnover') is not None:
            s['turnover'] = round(d['turnover'], 2)
        if d.get('volRatio') is not None:
            s['volRatio'] = round(d['volRatio'], 2)
        s['flowDate'] = today_str
        # 加一个字段标记这是盘中实时
        s['liveTimestamp'] = d.get('timestamp', '')
        ok += 1
    new_raw = json.dumps(stocks, ensure_ascii=False, indent=2)
    new_html = html_text[:m.start(1)] + new_raw + html_text[m.end(1):]
    return new_html, ok, miss


def main():
    today = time.strftime("%Y-%m-%d")
    # 拿 deploy/index.html 的 STOCKS
    with open(DEPLOY, 'r', encoding='utf-8') as f:
        html = f.read()
    m = re.search(r'const\s+STOCKS\s*=\s*(\[.*?\]);', html, re.DOTALL)
    if not m:
        print("❌ deploy/index.html 没有 STOCKS 数组")
        return False
    stocks = json.loads(m.group(1))
    codes = [s['code'] for s in stocks]
    print(f"📊 待刷新 {len(codes)} 只票 → {today} 盘中实时数据")
    qt_data = fetch_qt_batch(codes)
    print(f"   qt 命中 {len(qt_data)}/{len(codes)}")
    new_html, ok, miss = update_stocks_with_live(html, qt_data, today)
    print(f"   更新成功 {ok}, 缺失 {miss}")
    # 写回 deploy 和 docs
    with open(DEPLOY, 'w', encoding='utf-8') as f:
        f.write(new_html)
    with open(DOCS, 'w', encoding='utf-8') as f:
        f.write(new_html)
    print(f"✅ 已写回 deploy/index.html + docs/index.html（flowDate={today}）")
    return True


if __name__ == '__main__':
    sys.exit(0 if main() else 1)
