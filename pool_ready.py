#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""盘中「已经到位」的个股扫描 —— 供大盘资金回流推送内嵌个股名单。

口径锁死为 scan_macd.py v3（本项目唯一被验证过的选票口径）+ 用户的 P<75 铁律：
  1. DIF < 0（水下）
  2. 水下三信号至少命中一个：水下金叉 / DIF水下上翘 / 绿柱缩短≥2根
  3. volr20 ∈ [VOLR_MIN, VOLR_MAX)
  4. 无过滤否决：ST/退、次新、涨停买不进、一字板、成交额<0.5亿、
     换手不在 1~30%、除权污染
  5. ★ P = 收盘 / 过去60日最高收盘 < 75（P≥75 信号几乎全失效，2026-10 验证）

数据来源：
  .cache_pool_daily.json  —— fetch_pool_daily.py 抓的 40 只票池日线（历史）
      ⚠️ 缓存里「今天那一根」可能是盘中抓的残缺 K → 一律丢弃，改用快照重拼
  qt.gtimg.cn 快照        —— 今天那根 K 的 O/H/L/C/量；量能用 est_full_vol 反推全日预估

为什么要反推全日量：盘中 volr20 若用「截至当下的累计量」，会系统性偏低，
一堆票会假性缩量通过。scan_macd 实盘本来就走 est_full_vol，这里必须同口径。
"""
import json
import os
import sys
import time
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

CACHE = os.path.join(BASE, '.cache_pool_daily.json')
POS_MAX = 75.0          # ★ P≥75 信号失效（铁律，见项目记忆「位置压倒一切」）
MAX_SHOW = 6            # 正文最多列几只，多了没人看


def _today():
    return time.strftime('%Y-%m-%d')


def _sess_of(code):
    """6 位代码补市场前缀（qt.gtimg 要求 sh/sz/bj）"""
    return ('sh' if code[0] == '6' else 'bj' if code[0] in '48' else 'sz') + code


def _in_session(now=None):
    """是否处在「今天这根日 K 还没走完」的时段（9:30~15:00）"""
    t = (now or datetime.now()).strftime('%H:%M')
    return ('09:30' <= t < '11:30') or ('13:00' <= t < '15:00')


def _is_stale(hist, today):
    """历史最后一根距今天隔了几个工作日（粗算，节假日会多算，只用于提示）"""
    try:
        d0 = datetime.strptime(hist[-1][0], '%Y-%m-%d').date()
        d1 = datetime.strptime(today, '%Y-%m-%d').date()
    except Exception:
        return 0
    n = 0
    d = d0
    while d < d1:
        d = d.fromordinal(d.toordinal() + 1)
        if d.weekday() < 5:
            n += 1
    return n


def scan(limit=MAX_SHOW, use_cache_min=0):
    """扫描票池，返回「已到位」名单。

    返回 dict(
      ok=True/False, err=..., today=..., data_date=..., intraday=bool,
      stale=int, rows=[顶层候选...], total=int, pool=int, ts=str
    )
    绝不抛异常 —— 调用方是推送链路，个股名单拿不到也不能把大盘消息搞挂。
    """
    out = dict(ok=False, err=None, today=_today(), data_date=None, intraday=False,
               mode='', stale=0, rows=[], total=0, pool=0, ts='')
    try:
        from scan_macd import check, filt_reasons, est_full_vol
        from scan_newlow import qt_snap
    except Exception as e:
        out['err'] = '依赖导入失败: %s' % e
        return out

    if not os.path.exists(CACHE):
        out['err'] = '缺少 .cache_pool_daily.json（先跑 fetch_pool_daily.py）'
        return out

    # 一次性扫描结果缓存（同一进程内重复 build_message 不重复抓网）
    if use_cache_min:
        global _LAST
        if _LAST and (time.time() - _LAST[0]) < use_cache_min * 60:
            return _LAST[1]

    today = out['today']
    try:
        raw = json.load(open(CACHE, encoding='utf-8'))
    except Exception as e:
        out['err'] = '日线缓存读取失败: %s' % e
        return out
    out['pool'] = len(raw)

    try:
        snap = qt_snap([_sess_of(c) for c in raw.keys()])
    except Exception as e:
        out['err'] = '实时快照失败: %s' % e
        return out
    if not snap:
        out['err'] = '实时快照为空（腾讯接口异常？）'
        return out

    rows, bad, stale_max, n_live, n_today = [], 0, 0, 0, 0
    for code, bars in raw.items():
        s = snap.get(code)
        if not s or not s.get('close'):
            bad += 1
            continue
        # 行情数据日期（快照 f[30] 形如 20261028/16:14:03）
        ts = str(s.get('ts') or '')
        data_date = ('%s-%s-%s' % (ts[0:4], ts[4:6], ts[6:8])
                     if len(ts) >= 8 and ts[:8].isdigit() else '')
        if data_date:
            out['data_date'] = out['data_date'] or data_date

        # 历史一律丢掉「今天」（可能是盘中残缺 K）
        hist = [b for b in bars if b[0] != today]
        if len(hist) < 62:
            bad += 1
            continue
        stale_max = max(stale_max, _is_stale(hist, today))
        if not out.get('hist_date'):
            out['hist_date'] = hist[-1][0]

        o, c = s.get('open'), s.get('close')
        h = s.get('high') or max(o or c, c)
        l = s.get('low') or min(o or c, c)
        vol = s.get('vol') or 0
        if not o or not h or not l:
            bad += 1
            continue

        # 今天有没有成交？——快照日期等于今天才算有「今天这根 K」
        # （周末/节假日跑脚本时快照是上一交易日的，拼进来会造出重复 K）
        has_today = (data_date == today)
        bs = [tuple(b[:6]) for b in hist]
        if has_today:
            bs = bs + [(today, o, c, h, l, vol)]
        # live = 今天这根还没走完 → 量能必须反推全日，否则 volr20 系统性偏低会假通过
        live = has_today and _in_session()
        if live:
            n_live += 1
        if has_today:
            n_today += 1

        fv, _how = est_full_vol(bs, s) if live else (None, 'closed')
        try:
            r = check(code, bs, full_vol=fv, snap=s)
        except Exception:
            bad += 1
            continue
        if r is None:
            bad += 1
            continue

        r['name'] = s.get('name', '')
        r['day_chg'] = (c / s['prev'] - 1) * 100 if s.get('prev') else None
        r['amount'] = s.get('amount')
        r['turnover'] = s.get('turnover')
        try:
            r['filt'] = filt_reasons(r, s, True)
        except Exception as e:
            r['filt'] = ['filt异常:%s' % e]
        rows.append(r)

    out['stale'] = stale_max
    out['intraday'] = n_live > 0
    out['mode'] = 'live' if n_live else ('close' if n_today else 'prev')
    ok_rows = [r for r in rows if r.get('pass') and not r.get('filt')
               and (r.get('pos') is not None and r['pos'] < POS_MAX)]
    ok_rows.sort(key=lambda x: (-x['score'], x['pos'] or 999, -(x.get('volr') or 0)))
    out['total'] = len(ok_rows)
    out['rows'] = ok_rows[:limit]
    out['ts'] = time.strftime('%H:%M')
    out['ok'] = True
    if bad and not rows:
        out['err'] = '全部取数失败（%d 只）' % bad

    if use_cache_min:
        _LAST = (time.time(), out)
    return out


_LAST = None


def fmt_md(res):
    """把扫描结果渲染成推送正文的一段 markdown（不含标题序号，由调用方决定放哪）"""
    if not res or not res.get('ok'):
        why = (res or {}).get('err') or '未知原因'
        return [f'### 4、已到位个股\n', f'- ⚠️ 本次未能扫描（{why}）\n']

    rows = res.get('rows') or []
    when = res.get('ts') or ''
    n_all = res.get('total') or 0
    pool_n = res.get('pool') or 0
    mode = res.get('mode') or ('live' if res.get('intraday') else 'close')
    tag = {'live': '盘中实时·量能按全日预估',
           'close': '收盘口径',
           'prev': '上一交易日口径'}.get(mode, '')
    lines = ['### 4、已到位个股（scan_macd v3 口径）\n']

    if not rows:
        lines.append(f'- **票池 {pool_n} 只，当前没有一只过线**'
                     f'（DIF<0 + 水下信号 + volr20<1.0 + 无否决 + P<75）\n')
        lines.append('- 资金回流说明的是「大盘层面的钱在回来」，不等于个股已经回调到位；'
                     '这种时候宁可空仓等，别为了上车而上车\n')
        return lines

    lines.append(f'- 票池 {pool_n} 只 → **{n_all} 只到位**，列出前 {len(rows)} 只'
                 f'（{tag} {when}）\n')
    lines.append('| 代码 | 名称 | 现价 | 涨幅 | P | K | volr20 | 信号 |')
    lines.append('|---|---|---:|---:|---:|---:|---:|---|')
    for r in rows:
        sig = '、'.join(n for n, _ in r['sigs']) or '—'
        chg = r.get('day_chg')
        lines.append('| %s | %s | %.2f | %s | %.0f | %.0f | %.2f | %s |'
                     % (r['code'], (r.get('name') or '')[:6], r['close'],
                        ('%+.2f%%' % chg) if chg is not None else '—',
                        r.get('pos') or 0, r.get('K') or 0, r.get('volr') or 0, sig))
    lines.append('')
    lite = [r for r in rows if (r.get('volr') or 9) < 0.8]
    if lite:
        lines.append('- 缩量最明显的：' + '、'.join(
            '%s(%s) volr %.2f' % (r.get('name') or r['code'], r['code'], r['volr'])
            for r in lite[:3]) + ' —— 回调到位 + 没人抢，是这套口径最想要的样子')
    if res.get('stale', 0) > 1:
        lines.append(f"- ⚠️ 日线历史只到 {res.get('hist_date') or '?'}，"
                     f"已隔 {res['stale']} 个工作日（跑 fetch_pool_daily.py 补），"
                     'MACD / volr 可能有偏差，名单仅供参考')
    lines.append('- 这只是「技术面到位」筛选，不是买入清单：P≥75 已按铁律剔除，'
                 '但仍要你自己看基本面、看盘口，仓位自己定\n')
    return lines


if __name__ == '__main__':
    r = scan(limit=10)
    print(json.dumps({k: v for k, v in r.items() if k != 'rows'},
                     ensure_ascii=False, indent=1))
    print('\n'.join(fmt_md(r)))
    for x in r['rows']:
        print('  %s %s 收%.2f P=%.0f K=%.1f volr=%.2f 得分%d %s'
              % (x['code'], x.get('name'), x['close'], x.get('pos') or 0,
                 x.get('K') or 0, x.get('volr') or 0, x['score'],
                 '、'.join(n for n, _ in x['sigs'])))
