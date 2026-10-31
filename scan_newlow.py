#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
创新低确认 + T+1 可用判定（实时扫描）
用法：
  python3 scan_newlow.py                 # 扫票池（deploy/index.html 的 STOCKS+PINNED）
  python3 scan_newlow.py sh600105 sz000688   # 指定代码

输出每只票：
  是否创 5/10/20/60 日「收盘新低」和「盘中价格新低」
  位置 P（收盘/过去60日最高收盘）、KDJ 的 K、MACD 水位
  综合判定（依据 2026-10-27 回测，见 创新低判定与T+1买点_20261027.md）

数据源：腾讯 kline/kline（日线，含今日）+ qt.gtimg.cn（实时快照）
"""
import json, os, re, sys, subprocess, time

BASE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable

def curl(url, gbk=False, timeout=15):
    cmd = ['curl', '-s', '-m', str(timeout), url]
    env = dict(os.environ)
    for k in ('http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY',
              'all_proxy', 'ALL_PROXY'):
        env.pop(k, None)
    out = subprocess.run(cmd, capture_output=True, env=env).stdout
    return out.decode('gbk', errors='ignore') if gbk else out.decode('utf-8', errors='ignore')

def fetch_day(code, n=200):
    """腾讯 kline/kline 日线，返回 [(date,o,c,h,l,vol)]"""
    txt = curl('https://ifzq.gtimg.cn/appstock/app/kline/kline?param=%s,day,,,%d' % (code, n))
    try:
        d = json.loads(txt)
        node = d['data'][code]
        k = node.get('day') or []
    except Exception:
        return []
    out = []
    for b in k:
        try:
            out.append((str(b[0]), float(b[1]), float(b[2]), float(b[3]),
                        float(b[4]), float(b[5] or 0)))
        except Exception:
            continue
    return out

def qt_snap(codes):
    """实时快照；返回 {code: {...}}（腾讯返回的代码是 6 位纯数字）"""
    res = {}
    for i in range(0, len(codes), 40):
        chunk = codes[i:i + 40]
        txt = curl('https://qt.gtimg.cn/q=' + ','.join(chunk), gbk=True)
        for line in txt.split('\n'):
            m = re.search(r'"(.*)"', line, re.S)
            if not m:
                continue
            f = m.group(1).split('~')
            if len(f) < 35:
                continue
            num = f[2]                      # 6 位纯数字

            def gf(i, default=None):
                try:
                    return float(f[i]) if len(f) > i and f[i] not in ('', '-') else default
                except (ValueError, IndexError):
                    return default

            res[num] = dict(name=f[1],
                            close=float(f[3]) if f[3] else None,
                            prev=float(f[4]) if f[4] else None,
                            vol=float(f[6]) if len(f) > 6 and f[6] else None,  # 成交量(手)，供量能比用
                            open=gf(5),          # 今开（除权检测要用，不能留空）
                            high=float(f[33]) if f[33] else None,
                            low=float(f[34]) if f[34] else None,
                            chg=float(f[32]) if f[32] else None,
                            vr=float(f[49]) if len(f) > 49 and f[49] else None,
                            ts=f[30] if len(f) > 30 else '',
                            # ↓ 2026-10-27 新增：标的过滤用（字段位置已用 sh600105/sz000688 实测核对）
                            amount=gf(37),        # 成交额（万元）
                            turnover=gf(38),      # 换手率（%）
                            fvalue=gf(44),        # 流通市值（亿元）
                            up_price=gf(47),      # 涨停价（买不进的判据）
                            dn_price=gf(48))      # 跌停价
    return res

def kdj_last(bars):
    K, D = 50.0, 50.0
    for j in range(max(0, len(bars) - 120), len(bars)):
        hh = max(bars[k][3] for k in range(max(0, j - 8), j + 1))
        ll = min(bars[k][4] for k in range(max(0, j - 8), j + 1))
        rsv = (bars[j][2] - ll) / (hh - ll) * 100 if hh > ll else 50.0
        K = 2.0 / 3 * K + 1.0 / 3 * rsv
        D = 2.0 / 3 * D + 1.0 / 3 * K
    return K, D

def ema(vals, n):
    a = 2.0 / (n + 1); e = vals[0]
    for x in vals[1:]:
        e = a * x + (1 - a) * e
    return e

def analyze(code, bars, snap):
    """bars 为不含今日的历史日线；snap 为实时快照。返回判定 dict"""
    if len(bars) < 65:
        return None
    if snap and snap.get('close'):
        today = (bars[-1][0], 0, snap['close'],
                 snap.get('high') or snap['close'],
                 snap.get('low') or snap['close'], 0)
        full = list(bars) + [today]
        live = True
    else:
        full = list(bars)
        live = False
    c = full[-1][2]; l = full[-1][4]
    r = dict(code=code, live=live, close=c, low=l)
    for n in (5, 10, 20, 60):
        mn_c = min(full[j][2] for j in range(max(0, len(full) - 1 - n), len(full) - 1))
        mn_l = min(full[j][4] for j in range(max(0, len(full) - 1 - n), len(full) - 1))
        r['cnd%d' % n] = 1 if c < mn_c else 0
        r['pnd%d' % n] = 1 if l < mn_l else 0
        r['gap%d' % n] = (c / mn_c - 1) if mn_c > 0 else None   # 距N日低点还有多少
    hi60 = max(full[j][2] for j in range(max(0, len(full) - 1 - 60), len(full) - 1))
    r['P'] = c / hi60 if hi60 > 0 else None
    # 盘中确认可靠度（实测值，见报告）
    r['conf_close'] = None
    K, D = kdj_last(full)
    r['K'] = K; r['J'] = 3 * K - 2 * D
    cs = [b[2] for b in full]
    r['dif'] = ema(cs[-200:], 12) - ema(cs[-200:], 26)
    r['dif_neg'] = 1 if r['dif'] < 0 else 0
    v20 = sum(b[5] for b in full[-21:-1]) / 20.0
    r['vr'] = (full[-1][5] / v20) if v20 > 0 else None
    if snap:
        r['name'] = snap.get('name', '')
        r['chg'] = snap.get('chg')
        r['vr_qt'] = snap.get('vr')
        r['ts'] = snap.get('ts', '')
    return r

def judge(r):
    """依据回测结论给出判定"""
    if not r:
        return '数据不足', ''
    K = r['K']; P = r['P']
    tags = []
    ok = True
    if K < 20:
        tags.append('K<20超卖区(实测无效)'); ok = False
    elif K < 30:
        tags.append('K∈[20,30)弱势区(实测不显著)'); ok = False
    elif K < 40:
        tags.append('K∈[30,40)偏弱(实测不显著)'); ok = False
    elif K < 60:
        tags.append('★K∈[40,60)最优区(实测+0.273%)')
    elif K < 70:
        tags.append('K∈[60,70)次优(实测+0.224%)')
    else:
        tags.append('K≥70高位(追高风险)'); ok = False
    if P is not None:
        if P < 0.3:
            tags.append('P<0.3极端低位(实测-3.1%)'); ok = False
        elif P < 0.7:
            tags.append('P<0.7位置合格')
        else:
            tags.append('P≥0.7偏高')
    nl = [n for n in (5, 10, 20, 60) if r['cnd%d' % n]]
    if nl:
        tags.append('创%s日收盘新低(减分)' % '/'.join(str(x) for x in nl))
        ok = False
    if r['dif_neg']:
        tags.append('MACD水下')
    if r['vr_qt'] and 0.8 <= r['vr_qt'] <= 1.2:
        tags.append('量比平')
    verdict = '★可关注' if ok else '观望'
    return verdict, '；'.join(tags)

def pool_codes():
    """从 deploy/index.html 的 STOCKS/PINNED 常量抠票池。

    文件不存在或解析不出内容时返回 []（调用方负责给出友好提示），
    不抛异常 —— 便于脚本脱离仪表盘单独运行。
    """
    path = os.path.join(BASE, 'deploy', 'index.html')
    try:
        s = open(path, encoding='utf-8', errors='ignore').read()
    except Exception:
        return []
    codes = []
    for var in ('STOCKS', 'PINNED'):
        m = re.search(r'const %s\s*=\s*(\[.*?\]);' % var, s, re.S)
        if not m:
            continue
        try:
            arr = json.loads(m.group(1))
        except Exception:
            continue
        for x in arr:
            c = x.get('code') if isinstance(x, dict) else x
            if c and c not in codes:
                codes.append(c)
    return codes

def main():
    args = [a for a in sys.argv[1:] if not a.startswith('-')]
    codes = args if args else pool_codes()
    if not codes:
        print('没有指定代码，也没读到票池'); return
    print('拉取 %d 只（日线 + 实时快照）…' % len(codes))
    snap = qt_snap(codes)
    rows = []
    for c in codes:
        bars = fetch_day(c, 200)
        if not bars:
            print('  !! %s 日线拉取失败' % c); continue
        # 腾讯日线最后一根可能已含今日，去掉避免重复；用快照补今日
        num = re.sub(r'\D', '', c)
        s = snap.get(num)
        if s and bars[-1][0] == time.strftime('%Y-%m-%d'):
            bars = bars[:-1]
        r = analyze(c, bars, s)
        if r:
            rows.append(r)
        time.sleep(0.12)
    rows.sort(key=lambda x: (0 if 40 <= x['K'] < 70 else 1, -x['K']))
    hdr = ('%-10s %-8s %7s %7s %6s %6s %6s %6s %6s %6s %6s %6s  %s' %
           ('代码', '名称', '现价', '涨跌%', '位置P', 'K值', '创5', '创10', '创20', '创60',
            '盘中低', '量比', '判定'))
    print('\n' + hdr)
    print('-' * 130)
    for r in rows:
        v, t = judge(r)
        print('%-10s %-8s %7.2f %+7.2f %6.2f %6.1f %6s %6s %6s %6s %6s %6s  %s' % (
            r['code'], r.get('name', '')[:8], r['close'], r.get('chg') or 0,
            r['P'] or 0, r['K'],
            '✓' if r['cnd5'] else '-', '✓' if r['cnd10'] else '-',
            '✓' if r['cnd20'] else '-', '✓' if r['cnd60'] else '-',
            '✓' if r['pnd20'] else '-',
            ('%.2f' % r['vr_qt']) if r.get('vr_qt') else '-',
            v))
        print('           └ %s' % t)
    print('\n说明：创N=收盘创N日新低；盘中低=今日最低价创20日新低（盘中一旦跌破即100%成立，不会被撤销）')
    print('      判定依据 2026-10-27 回测（899只×111天 T+1 口径）：')
    print('        K∈[40,60) 超额 +0.273%  p<0.0001  剔Top3日 +0.235%  4/4月度为正  Bonferroni通过 ✅')
    print('        创20日新低 +0.079% (p=0.81 无效) ；未创新低 +0.171% (p=0.0007 显著) → 新低是减分项')
    print('        K<20 超卖抄底 −0.007% (无效) ；P<0.3 极端低位 −3.09% (n=43，样本小但显著)')

if __name__ == '__main__':
    main()
