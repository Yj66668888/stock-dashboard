#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
h60_kdj.py —— 60分钟 KDJ 状态判定（2026-11-04，双名单第 4 层过滤）

★ 需求（用户拍板 2026-11-04）
  30 分钟 KDJ 维持原样（只提示不拦截）；
  60 分钟 KDJ 必须是「上升状态」或「刚金叉」或「快要金叉」才进名单，
  下降发散的直接不进。

数据源：腾讯 mkline（m60，320 根 ≈ 80 个交易日，每天 4 根）
  https://ifzq.gtimg.cn/appstock/app/kline/mkline?param=CODE,m60,,320
  列序 [时间, 开, 收, 高, 低, 量]，close = 索引 2（与 m30 同构，已验证）
  ⚠️ 腾讯 mkline 上限 320 根，不支持分段拼接；无新浪 m60 兜底接口
     → 获取失败返回 None（fail-open：不拦截，只标注 '-'，防整列空白）

★ KDJ(9,3,3) 交付口径（与仪表盘一致）
  RSV = (C - LLV9) / (HHV9 - LLV9) * 100
  K = 2/3 * prevK + 1/3 * RSV     （初值 50）
  D = 2/3 * prevD + 1/3 * K       （初值 50）
  J = 3K - 2D

★ 四档定义（只用「截至最新一根」的数据，无未来函数）
  ✦刚金叉  rank=3  近 GOLD_WIN 根内 K 上穿 D
  ↑上行    rank=2  K > D 且 D 上行（D[-1] > D[-2]）
  ⚡将金叉  rank=2  K < D 且 (D−K) 收窄 且 K 上行 且 差值 ≤ NEAR_TH
  ↘高位回落 rank=0  K > D 但 K 下行（多头末端，不算上升状态）
  ↓下行    rank=0  K < D 且差值在扩大（或 K 下行）
  ok = rank >= 2   ←「上行/刚金叉/将金叉」，双名单准入

★ 用法
  from h60_kdj import calc, fetch
  ks = fetch('sh600000')          # [{'date','open','close','high','low','volume'}, ...]
  st  = calc(ks)                  # dict / None
  python3 h60_kdj.py sh600000 sz000001   # 命令行自测

统计归因，不构成投资建议。
"""
import json
import ssl
import time
import urllib.request

# ==================== 参数区（★ 可按偏好改） ====================
K_N = 9                 # KDJ 周期
GOLD_WIN = 3            # ★ 最近几根内金叉算「刚金叉」
NEAR_TH = 3.0           # ★ D−K 差值小于此值且收窄才算「将金叉」
MIN_BARS = 40           # ★ 少于这个根数不判定（≈10 个交易日）
EXCLUDE_FADE = True     # ★ 高位回落（K>D 但 K 下行）是否也排除
# ================================================================

_CTX = ssl.create_default_context()
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
      'Referer': 'https://gu.qq.com/'}


def fetch(code, lmt=320, retries=2):
    """60分钟K线（腾讯 mkline）。失败返回 []（调用方 fail-open）。"""
    url = ('https://ifzq.gtimg.cn/appstock/app/kline/mkline'
           '?param=%s,m60,,%d' % (code, lmt))
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=UA)
            raw = urllib.request.urlopen(req, context=_CTX, timeout=12).read().decode('utf-8', 'ignore')
            data = json.loads(raw).get('data', {}).get(code, {})
            rows = data.get('m60')
            if isinstance(rows, dict):
                rows = rows.get('data') or []
            out = []
            for r in rows or []:
                try:
                    out.append({'date': str(r[0]), 'open': float(r[1]), 'close': float(r[2]),
                                'high': float(r[3]), 'low': float(r[4]), 'volume': float(r[5])})
                except (ValueError, IndexError, TypeError):
                    continue
            if len(out) >= MIN_BARS:
                return out
        except Exception:
            pass
        if attempt < retries:
            time.sleep(0.8 * (attempt + 1))
    return []


def kdj(ks, n=K_N):
    """返回 (K 序列, D 序列)。数据不足返回 ([], [])。"""
    c = [k['close'] for k in ks]
    if len(c) < n + 5:
        return [], []
    K, D, pk, pd = [], [], 50.0, 50.0
    for i in range(len(c)):
        lo = min(c[max(0, i - n + 1): i + 1])
        hi = max(c[max(0, i - n + 1): i + 1])
        rsv = 50.0 if hi == lo else (c[i] - lo) / (hi - lo) * 100.0
        pk = 2.0 / 3.0 * pk + 1.0 / 3.0 * rsv
        pd = 2.0 / 3.0 * pd + 1.0 / 3.0 * pk
        K.append(pk)
        D.append(pd)
    return K, D


def calc(ks):
    """判定 60分KDJ 状态。数据不足返回 None（fail-open）。"""
    if not ks or len(ks) < MIN_BARS:
        return None
    K, D = kdj(ks)
    if not K:
        return None
    i = len(K) - 1
    k0, k1, d0, d1 = K[i], K[i - 1], D[i], D[i - 1]
    gap = k0 - d0
    gap_prev = k1 - d1

    gold_ago = None
    for j in range(i, max(i - GOLD_WIN, 0) - 1, -1):
        if K[j] > D[j] and K[j - 1] <= D[j - 1]:
            gold_ago = i - j
            break

    if gold_ago is not None:
        state, label, rank = 'gold', '✦刚金叉', 3
    elif k0 > d0 and d0 > d1:
        state, label, rank = 'up', '↑上行', 2
    elif k0 < d0 and gap > gap_prev and k0 > k1 and (-gap) <= NEAR_TH:
        state, label, rank = 'near', '⚡将金叉', 2
    elif k0 > d0:
        state, label, rank = 'fade', '↘高位回落', 0
    else:
        state, label, rank = 'down', '↓下行', 0

    ok = rank >= 2 or (state == 'fade' and not EXCLUDE_FADE)
    return {
        'state': state, 'label': label, 'rank': rank, 'ok': ok,
        'K': round(k0, 1), 'D': round(d0, 1), 'J': round(3 * k0 - 2 * d0, 1),
        'gap': round(gap, 2), 'gold_ago': gold_ago, 'n': len(K),
    }


def short(st):
    """单字符短标（表格用）"""
    if not st:
        return '—'
    return {'gold': '✦', 'up': '↑', 'near': '⚡', 'fade': '↘', 'down': '↓'}.get(st['state'], '—')


def txt(st):
    if not st:
        return '无数据'
    base = '%s K%.1f D%.1f' % (st['label'], st['K'], st['D'])
    if st.get('gold_ago') is not None:
        base += '（%d根前金叉）' % st['gold_ago']
    return base


if __name__ == '__main__':
    import sys
    for code in sys.argv[1:]:
        ks = fetch(code)
        st = calc(ks)
        print('%-10s %s' % (code, txt(st) if st else '（获取失败/数据不足，fail-open）'))
        if st and len(ks) >= 3:
            for x in ks[-3:]:
                print('    %s close %.2f' % (x['date'], x['close']))
