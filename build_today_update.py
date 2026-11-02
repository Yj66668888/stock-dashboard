#!/usr/bin/env python3
"""今日股票更新打包：票池 40 只盘中明细 + 大盘状态 + 选票结果

数据源：
  1. .cache_pool_daily.json  —— fetch_pool_daily.py 生成（腾讯日线，未复权）
  2. qt.gtimg.cn 实时快照   —— 现价/涨跌幅/成交额/换手/量比
  3. scan_macd.py 实时扫描  —— 位置P / K值 / DIF / volr20 / 信号
  4. index_fund_flow_watch.py —— 大盘资金流（东财）
  列序约定：.cache_pool_daily.json 每行 = [date, open, close, high, low, vol]
"""
import json, os, re, subprocess, urllib.request, time
from collections import OrderedDict

BASE = os.path.dirname(os.path.abspath(__file__))
TODAY = time.strftime('%Y-%m-%d')
NOW = time.strftime('%H:%M:%S')
PY = '/Users/fuckyouasshole/.workbuddy/binaries/python/versions/3.13.12/bin/python3'


def qt(codes):
    """腾讯实时快照，返回 {纯6位代码: {...}}"""
    out = {}
    for i in range(0, len(codes), 80):
        chunk = codes[i:i + 80]
        try:
            req = urllib.request.Request('http://qt.gtimg.cn/q=' + ','.join(chunk),
                                         headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=15) as r:
                data = r.read().decode('gbk', errors='ignore')
            for line in data.split(';'):
                m = re.match(r'v_(\w+)="([^"]*)"', line.strip())
                if not m:
                    continue
                p = m.group(2).split('~')
                if len(p) < 50:
                    continue
                try:
                    out[m.group(1)[2:]] = {
                        'name': p[1], 'close': float(p[3]), 'prev': float(p[4]),
                        'open': float(p[5]), 'chg': float(p[32]),
                        'high': float(p[33]), 'low': float(p[34]),
                        'amount': float(p[37]) if p[37] else None,   # 成交额(万)
                        'turnover': float(p[38]) if p[38] else None,  # 换手%
                        'volRatio': float(p[49]) if p[49] else None,  # 量比
                        'ts': p[30],
                    }
                except (ValueError, IndexError):
                    pass
        except Exception as e:
            print('  qt chunk 失败: %s' % str(e)[:60])
    return out


def prefix(code):
    return ('sh' if code.startswith(('6', '9')) else
            'sz' if code.startswith(('0', '2', '3')) else
            'bj' if code.startswith(('4', '8')) else 'sh') + code


def main():
    print('=== 今日股票更新打包 %s %s ===' % (TODAY, NOW))

    # ---------- 1. 票池日线 ----------
    pool = json.load(open(os.path.join(BASE, '.cache_pool_daily.json')))
    codes = sorted(pool.keys())
    codes = [c[-6:] if len(c) > 6 else c for c in codes]

    # ---------- 2. 实时快照 ----------
    q = qt([prefix(c) for c in codes])
    print('  票池 %d 只，qt 命中 %d' % (len(codes), len(q)))
    if len(q) < len(codes) * 0.9:
        print('  ❌ 覆盖率低于 90%%，中止')
        return 1

    # ---------- 3. 位置P（收盘/过去60日最高收盘）与 DIF ----------
    # 直接复用 scan_macd.py 的输出，避免两套口径打架
    scan_raw = subprocess.run([PY, os.path.join(BASE, 'scan_macd.py')],
                              capture_output=True, text=True, cwd=BASE).stdout
    scan = {}
    for ln in scan_raw.split('\n'):
        m = re.match(r'^\s*(s[hz]\d{6})\s+(\S+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+(-?[\d.]+)'
                     r'\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+(.*)$', ln)
        if m:
            # 列序（2026-11-02 校准，与 scan_macd.py 实际输出对齐）：
            #   1代码 2名称 3现价 4位置P 5K值 6DIF 7volr20 8成交额(万) 9换手% 10信号
            # ⚠️ 旧版把 group(3) 当 P，实际是现价 —— 整列差一位（P=现价、volr 出现负值）
            scan[m.group(1)[2:]] = {
                'name': m.group(2), 'close': float(m.group(3)), 'P': float(m.group(4)),
                'K': float(m.group(5)), 'DIF': float(m.group(6)),
                'volr': float(m.group(7)),
                'amount': float(m.group(8)), 'turn': float(m.group(9)),
                'sig': m.group(10).strip(),
            }

    # ---------- 4. 逐票拼装 ----------
    rows = []
    for c in codes:
        arr = pool.get(c) or pool.get(prefix(c)) or []
        d = q.get(c)
        if not d or not arr:
            continue
        prev_close = arr[-2][2] if len(arr) >= 2 and arr[-2][2] else d['prev']
        # 缓存里今日那根是否已落今天
        cache_today = arr[-1][0] == TODAY
        s = scan.get(c, {})
        rows.append(OrderedDict([
            ('代码', c),
            ('名称', d['name']),
            ('现价', round(d['close'], 2)),
            ('昨收', round(prev_close, 2)),
            ('涨跌幅%', round(d['chg'], 2)),
            ('今开', round(d['open'], 2)),
            ('最高', round(d['high'], 2)),
            ('最低', round(d['low'], 2)),
            ('成交额(万)', round(d['amount'], 0) if d['amount'] else None),
            ('换手%', round(d['turnover'], 2) if d['turnover'] else None),
            ('量比', round(d['volRatio'], 2) if d['volRatio'] else None),
            ('位置P', s.get('P')),
            ('K值', s.get('K')),
            ('DIF', s.get('DIF')),
            ('volr20', s.get('volr')),
            ('入选', '★' if s else ''),
            ('信号', s.get('sig', '')),
            ('缓存今日K', '是' if cache_today else '否'),
        ]))

    rows.sort(key=lambda r: -(r['涨跌幅%'] or 0))
    up = sum(1 for r in rows if (r['涨跌幅%'] or 0) > 0)
    dn = sum(1 for r in rows if (r['涨跌幅%'] or 0) < 0)
    avg = sum(r['涨跌幅%'] for r in rows) / len(rows)
    srt = sorted(r['涨跌幅%'] for r in rows)
    med = srt[len(srt) // 2]
    limit = sum(1 for r in rows if (r['涨跌幅%'] or 0) <= -9.5)

    # ---------- 5. 大盘状态 ----------
    idx = {}
    try:
        raw = subprocess.run(['curl', '-s', '-m', '20', '--noproxy', '*',
                              'https://qt.gtimg.cn/q=sh000001,sz399001,sz399006,sh000688'],
                             capture_output=True, cwd=BASE).stdout.decode('gbk', 'ignore')
        for line in raw.split(';'):
            m = re.match(r'v_(\w+)="([^"]*)"', line.strip())
            if not m:
                continue
            f = m.group(2).split('~')
            if len(f) < 40:
                continue
            idx[f[1]] = {'close': float(f[3]), 'chg': float(f[32]), 'ts': f[30]}
    except Exception as e:
        print('  指数抓取失败: %s' % str(e)[:60])

    mkt = {}
    for ln in scan_raw.split('\n'):
        m = re.search(r'大盘30分KDJ门控：K=([\d.]+)\s+上证=([\d.]+)', ln)
        if m:
            mkt['kdj_K'] = float(m.group(1))
    # 资金流单独跑一次取最新值
    try:
        ff = subprocess.run([PY, os.path.join(BASE, 'index_fund_flow_watch.py')],
                            capture_output=True, text=True, cwd=BASE, timeout=300).stdout
        for ln in ff.split('\n'):
            m = re.search(r'当前=(-?[\d.]+)亿.*?回补=([+-][\d.]+)亿\s+阈值=([\d.]+)亿', ln)
            if m:
                mkt['flow_now'] = float(m.group(1))
                mkt['flow_back'] = float(m.group(2))
                mkt['flow_thr'] = float(m.group(3))
            m = re.search(r'30分K=([\d.]+)\s+D=([\d.]+)\s+J=(-?[\d.]+)', ln)
            if m:
                mkt['K'], mkt['D'], mkt['J'] = float(m.group(1)), float(m.group(2)), float(m.group(3))
            if '资金门:' in ln:
                mkt['flow_gate'] = ln.split('资金门:')[-1].strip()
            if '位置门:' in ln:
                mkt['pos_gate'] = ln.split('位置门:')[-1].strip()
    except Exception as e:
        print('  资金流抓取失败: %s' % str(e)[:60])

    out = {
        'today': TODAY, 'now': NOW, 'rows': rows,
        'stat': {'up': up, 'dn': dn, 'flat': len(rows) - up - dn, 'n': len(rows),
                 'avg': round(avg, 2), 'med': round(med, 2), 'limit': limit},
        'idx': idx, 'mkt': mkt, 'scan_hit': [r for r in rows if r['入选'] == '★'],
    }
    fp = os.path.join(BASE, '_today_update_%s.json' % TODAY.replace('-', ''))
    json.dump(out, open(fp, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    print('  ✅ 落盘 %s' % os.path.basename(fp))
    print('  票池 涨%d/跌%d/平%d 平均%+.2f%% 中位%+.2f%% 跌停%d'
          % (up, dn, len(rows) - up - dn, avg, med, limit))
    print('  大盘 KDJ: %s' % mkt)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
