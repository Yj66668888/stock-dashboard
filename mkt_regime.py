# -*- coding: utf-8 -*-
"""
大盘状态判定（2026-10-31 建立）
================================================================================
把当日上证走势归入三档，用于切换「涨日名单 / 跌日名单」。

    涨日  up    : 上证涨跌幅 >= +0.5%
    跌日  down  : 上证涨跌幅 <= -0.5%
    平淡  flat  : 中间

★ 阈值 0.5% 不是随手定的 —— 与横截面归因 analyze_contrarian.py 的场景划分
  （DOWN_TH = -0.005 / UP_TH = +0.005）完全一致，保证名单与回测口径同源。

★ 平淡日怎么办
--------------------------------------------------------------------------------
归因实测：平淡日 116 天（占 243 天的 47.7%）里，所有因子分位红盘率都挤在
44.5%~48.6% 之间，最好的组也只有 48.6%（+2.2pp）。
→ 平淡日【没有名单】，页面直接提示"今日无结构差异，别折腾因子"。
  这不是偷懒，是数据结论 —— 硬给名单只会让你以为有信号。

★ 数据源
--------------------------------------------------------------------------------
腾讯 qt.gtimg.cn/q=sh000001。
⚠️ 指数的字段与个股不同，**没有现成的涨跌幅字段**，必须自己算：
    v_sh000001="1~上证指数~000001~3599.02~3651.43~3605.65~497973940~..."
    索引 3 = 当前点数 / 索引 4 = 昨收 / 索引 5 = 今开 / 索引 6 = 成交量(手)
    → 涨跌幅 = (close / prev_close - 1) × 100
   （个股格式里索引 5 才是涨跌幅，照抄会算出 3605% 这种离谱值，已踩过）
curl 必须 env -u 四 proxy + --noproxy '*'（否则本机代理会污染）。
================================================================================
"""
import os
import subprocess
import time

UP_TH = 0.5      # ★ 与归因 UP_TH=0.005 对齐
DOWN_TH = -0.5   # ★ 与归因 DOWN_TH=-0.005 对齐

CACHE = None
CACHE_TS = 0
CACHE_TTL = 60   # 秒


def _curl(url, timeout=10):
    env = dict(os.environ)
    for k in ('http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY',
              'all_proxy', 'ALL_PROXY'):
        env.pop(k, None)
    try:
        out = subprocess.run(
            ['curl', '-s', '--noproxy', '*', '--max-time', str(timeout), url],
            capture_output=True, env=env, timeout=timeout + 5)
        return out.stdout.decode('gbk', errors='ignore')
    except Exception:
        return ''


def fetch_sh(use_cache=True):
    """返回 dict(code, name, close, chg_pct, ok, note)；失败时 ok=False"""
    global CACHE, CACHE_TS
    now = time.time()
    if use_cache and CACHE and now - CACHE_TS < CACHE_TTL:
        return CACHE
    txt = _curl('https://qt.gtimg.cn/q=sh000001')
    r = {'ok': False, 'note': '取数失败'}
    if 'v_sh000001=' in txt:
        try:
            body = txt.split('v_sh000001=')[1].split(';')[0].strip('"')
            f = body.split('~')
            close, prev = float(f[3]), float(f[4])
            # ⚠️ 指数没有涨跌幅字段：必须 (close/prev_close-1)×100
            pct = (close / prev - 1) * 100 if prev > 0 else 0.0
            r = {'ok': True, 'code': 'sh000001', 'name': f[1],
                 'close': close, 'prev': prev, 'chg_pct': pct, 'note': ''}
        except Exception as e:
            r = {'ok': False, 'note': '解析失败: %s' % e}
    CACHE, CACHE_TS = r, now
    return r


def regime(use_cache=True):
    """返回 dict(state, label, chg_pct, ts, ok, note)
    state ∈ up / down / flat / unknown"""
    s = fetch_sh(use_cache=use_cache)
    ts = time.strftime('%Y-%m-%d %H:%M')
    if not s['ok']:
        return {'state': 'unknown', 'label': '未知', 'chg_pct': None,
                'ts': ts, 'ok': False, 'note': s['note']}
    p = s['chg_pct']
    if p >= UP_TH:
        st, lb = 'up', '大盘涨'
    elif p <= DOWN_TH:
        st, lb = 'down', '大盘跌'
    else:
        st, lb = 'flat', '大盘平淡'
    return {'state': st, 'label': lb, 'chg_pct': round(p, 2),
            'close': s['close'], 'ts': ts, 'ok': True, 'note': ''}


def advice(st):
    """每个状态的展示文案（只提示，不拦截——买不买用户自己定）"""
    if st == 'up':
        return ('进攻名单：高弹性、高波动、高成交额、创新低的活跃股 + 缩量回调'
                '（涨日实测：bias60 低值组红盘率 75.3%，全市场基准 67.06%）')
    if st == 'down':
        return ('防御名单：低弹性、低波动、低成交额、未创新低的冷门股'
                '（跌日实测：beta60 低值组红盘率 32.5%，全市场基准 25.77%）')
    if st == 'flat':
        return ('今日平淡：归因实测这 116 天里所有因子分位红盘率都挤在 '
                '44.5%~48.6%，没有结构差异 → 不给名单，别硬找信号')
    return '大盘取数失败，名单仅供参考'


if __name__ == '__main__':
    r = regime(use_cache=False)
    print('上证 %s  涨跌幅 %s%%  → %s' % (r['close'], r['chg_pct'], r['label']))
    print(advice(r['state']))
