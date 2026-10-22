#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Server酱 推送账本 —— 跨环境的当日额度唯一真相源
================================================================
★ 为什么需要它（2026-10-22 事故复盘）★

原先每个推送脚本都把「今天推过几次」记在自己的本地文件里：
  - wechat_notify.py   → .wechat_push_state.json
  - index_kdj_watch.py → .index_kdj_state.json
  - index_fund_flow_watch.py → .index_fund_state.json

问题：这些文件全部在 .gitignore 里，而真正的推送大部分发生在
**GitHub Actions 的全新容器**中（Cloudflare Worker 每 5 分钟 dispatch 一次，
2026-10-22 上午实测 43 个 job）。每个容器启动时都是一个干净的文件系统，
上述记账文件全部为「空」，于是：

  · 同一个 am 档被本地 automation 和云端 job 各推一遍
  · 云端 4 次 Dashboard Update 里落在同一个时间窗的两次，互不知情再各推一遍
  · 大盘通道的 MKT_PUSH_BUDGET=3 完全失效（新容器里计数永远从 0 开始）
  → Server酱免费额度 5 条/日，一上午被打光。

修复思路：把「今日已推送」记账搬到**所有环境都能看见的地方** ——
GitHub 仓库里的 `push_ledger.json`（不在 .gitignore，可通过 Contents API 读写）。
任意环境推送前先 GET 真值，推送成功后 PUT 回写 + 带 sha 防并发覆盖。

限额规则（总额 SERVERCHAN_DAILY_QUOTA=5）：
  · 个股播报（digest）   ：每日 2 档（am / pm-close），保底保留 2 条额度
  · 大盘信号（mkt）      ：每日上限 MKT_PUSH_BUDGET=3（30分KDJ回升 + 资金回流 共享）
  · 大盘信号还要给个股留座：只有当 already-used + 1 <= QUOTA - (未用的个股保底数) 才放行，
    避免大盘抢光额度导致下午收尾档推不出

用法：
    from push_ledger import consume_quota, append_record, usage_report
    ok, reason = consume_quota('index_rebound_v2')   # 先占位判断
    ...发送...
    append_record('index_rebound_v2', title)          # 发送成功后记账
"""
import base64
import json
import os
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
REPO = 'Yj66668888/stock-dashboard'
REMOTE_PATH = 'push_ledger.json'            # ← 不加点前缀，确保不被 .gitignore 排除
LOCAL_CACHE = os.path.join(BASE, '.cache_push_ledger.json')

API_ROOT = f'https://api.github.com/repos/{REPO}'
# proxy 会被本机代理变量污染，必须显式绕过（同 wechat_notify / index_kdj_watch 的做法）
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# ---------------- 额度配置 ----------------
SERVERCHAN_DAILY_QUOTA = 5      # Server酱免费版硬上限
DIGEST_RESERVED = 2             # 个股两档（am / pm-close）的保底额度
MKT_PUSH_BUDGET = 3             # 大盘通道每日上限（30分KDJ回升 + 资金回流 共享）
                                #   ↑ 沿用 2026-10-21 用户拍板值（个股2 + 大盘3 = 5 条卡满）。
                                #     此前这个值只写在本地文件里，云端每 5 分钟一个全新容器，
                                #     容器启动计数即为 0 → 实际能推 N×3 条；现在才真正生效。
DIGEST_RESERVE_UNTIL_MIN = 14 * 60 + 45   # 14:45 之后不再为个股留座（已无后续档）

# 被视为「大盘通道」的 push_type（与 wechat_notify._MKT_PUSH_TYPES 保持一致）
MKT_TYPES = ('index_rebound_v2', 'index_rebound', 'index_fundflow')


def bj_now():
    return datetime.now(timezone(timedelta(hours=8)))


def today_str():
    return bj_now().strftime('%Y-%m-%d')


# ============================================================
# Token / HTTP
# ============================================================

def gh_token():
    """优先环境变量（GitHub Actions secret），其次本地 .github_token"""
    t = os.environ.get('GITHUB_TOKEN', '').strip()
    if t:
        return t
    try:
        with open(os.path.join(BASE, '.github_token')) as f:
            return f.read().strip()
    except OSError:
        return None


def _api(method, path, payload=None, timeout=20):
    """GitHub Contents API。返回 (status, json or None)"""
    token = gh_token()
    if not token:
        return 0, None
    data = json.dumps(payload).encode('utf-8') if payload is not None else None
    req = urllib.request.Request(f'{API_ROOT}/contents/{path}', data=data, method=method)
    req.add_header('Authorization', f'token {token}')
    req.add_header('Accept', 'application/vnd.github.v3+json')
    req.add_header('User-Agent', 'push-ledger')
    if data:
        req.add_header('Content-Type', 'application/json')
    try:
        with _OPENER.open(req, timeout=timeout) as r:
            body = r.read().decode('utf-8', errors='ignore')
            return r.status, (json.loads(body) if body else None)
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception:
        return 0, None


# ============================================================
# 账本读写
# ============================================================

def _empty_ledger():
    return {'date': today_str(), 'entries': [], 'slots': {}}


def _rollover(ledger):
    """跨日自动清零：不是今天的数据直接丢弃重建"""
    if not isinstance(ledger, dict) or ledger.get('date') != today_str():
        return _empty_ledger()
    if not isinstance(ledger.get('entries'), list):
        ledger['entries'] = []
    if not isinstance(ledger.get('slots'), dict):
        ledger['slots'] = {}
    return ledger


def load_ledger():
    """GET 远端账本。失败降级到本地缓存；两者都不可用返回当日空账本。

    返回 (ledger, source)  source ∈ 'remote' / 'cache' / 'empty'
    """
    status, resp = _api('GET', REMOTE_PATH)
    if status == 200 and resp and 'content' in resp:
        try:
            raw = base64.b64decode(resp['content']).decode('utf-8')
            led = _rollover(json.loads(raw))
            led['_sha'] = resp.get('sha')
            _write_cache(led)
            return led, 'remote'
        except Exception:
            pass
    # 远端拿不到 → 用本地缓存兜底（缓存是上一次远端真值的快照，比「当天空账本」准）
    try:
        with open(LOCAL_CACHE) as f:
            led = _rollover(json.load(f))
            return led, 'cache'
    except Exception:
        return _empty_ledger(), 'empty'


def _write_cache(ledger):
    try:
        snapshot = {k: v for k, v in ledger.items() if k != '_sha'}
        with open(LOCAL_CACHE, 'w') as f:
            json.dump(snapshot, f, ensure_ascii=False)
    except Exception:
        pass


def save_ledger(ledger, retries=3):
    """PUT 回写远端账本。409(sha冲突) 自动重取再试；成功同步更新本地缓存。

    返回 True/False —— 失败不阻断主流程，只在 log 里告警。
    """
    payload_base = {
        'message': f'push_ledger: {today_str()} 推送记账',
        'branch': 'main',
    }
    for attempt in range(retries):
        sha = ledger.get('_sha')
        if not sha:
            _, cur = _api('GET', REMOTE_PATH)
            if cur and cur.get('sha'):
                sha = cur['sha']
        content = base64.b64encode(
            json.dumps({k: v for k, v in ledger.items() if k != '_sha'},
                       ensure_ascii=False, indent=2).encode('utf-8')).decode()
        payload = dict(payload_base, content=content)
        if sha:
            payload['sha'] = sha
        status, resp = _api('PUT', REMOTE_PATH, payload)
        if status in (200, 201) and resp and resp.get('commit'):
            _write_cache(ledger)
            return True
        if status == 409:      # 并发写了同一个文件 → 重取最新 sha 再提交
            ledger['_sha'] = None
            continue
    _write_cache(ledger)       # 远端写不进去也要保住本地快照
    return False


# ============================================================
# 配额判定
# ============================================================

def usage_report(ledger=None, verbose=False):
    """当日用量摘要，用于 log 和拒绝原因文案"""
    led = ledger if ledger is not None else load_ledger()[0]
    ents = led.get('entries', [])
    total = len(ents)
    digest_used = len([e for e in ents if e.get('type', '').startswith('digest')])
    mkt_used = len([e for e in ents if e.get('type') in MKT_TYPES])
    other_used = total - digest_used - mkt_used
    rep = {
        'date': led.get('date'),
        'total': total, 'quota': SERVERCHAN_DAILY_QUOTA, 'left': SERVERCHAN_DAILY_QUOTA - total,
        'digest_used': digest_used, 'mkt_used': mkt_used, 'other_used': other_used,
    }
    if verbose:
        rep['entries'] = ents
    return rep


def consume_quota(push_type, slot=None, verbose=True):
    """判断本条推送能否占用额度。返回 (allowed, reason)

    ★ 这是唯一的全局硬闸门，所有环境（本地 automation / 云端 job）共享同一份账本 ★

    slot ── 档位唯一标识（如 'digest:am:2026-10-22'）。
      传了 slot 就额外做「同一档位一天只能推一次」的跨环境去重 ——
      这正是 10/22 事故的主因：am 档被本地 automation 与云端 job 各推一遍，
      两者都只推 1 条，总配额闸门看不出异常，但用户收到重复播报且白白浪费额度。
    """
    ledger, source = load_ledger()
    rep = usage_report(ledger)
    total, left = rep['total'], rep['left']

    if verbose:
        print(f'[ledger] 今日用量 {total}/{SERVERCHAN_DAILY_QUOTA}'
              f"（个股{rep['digest_used']} 大盘{rep['mkt_used']} 其他{rep['other_used']}）"
              f"｜账本来源={source}", flush=True)

    if left <= 0:
        return False, f"Server酱今日额度已用尽（{total}/{SERVERCHAN_DAILY_QUOTA}条）"

    # ---- 同一档位跨环境去重（先看这条，它对定位问题最有用）----
    if slot:
        hit = ledger.get('slots', {}).get(slot)
        if hit:
            return False, f"档位 {slot} 今日已推送过（{hit}）"

    if push_type in MKT_TYPES:
        if rep['mkt_used'] >= MKT_PUSH_BUDGET:
            return False, f"大盘通道今日已达上限（{rep['mkt_used']}/{MKT_PUSH_BUDGET}条）"
        # 大盘不能把个股两档的座挤掉：14:45 前要为「还没发出的个股档」留位置
        now = bj_now()
        minutes = now.hour * 60 + now.minute
        if minutes < DIGEST_RESERVE_UNTIL_MIN:
            digest_remaining = DIGEST_RESERVED - rep['digest_used']
            if digest_remaining > 0 and left <= digest_remaining:
                return False, (f"额度余 {left} 条需留给个股 {digest_remaining} 档"
                               f"（已推大盘{rep['mkt_used']}条）")

    return True, f"额度 OK（余 {left} 条）"


def append_record(push_type, title='', slot=''):
    """推送成功后记账。返回 (ok, report)

    采用 read-modify-write，写冲突自动重试。slot 会被登记到 slots 做档位去重。
    """
    ledger, _ = load_ledger()
    entries = ledger.setdefault('entries', [])
    slots = ledger.setdefault('slots', {})
    now = bj_now()
    entries.append({
        'ts': now.strftime('%Y-%m-%d %H:%M:%S'),
        'type': push_type,
        'slot': slot or '',
        'env': 'cloud' if os.environ.get('GITHUB_ACTIONS') else 'local',
        'title': (title or '')[:60],
    })
    if slot:
        slots[slot] = now.strftime('%Y-%m-%d %H:%M:%S')
    ledger['date'] = today_str()
    ok = save_ledger(ledger)
    rep = usage_report(ledger)
    if not ok:
        print('[ledger] ⚠️ 远端账本回写失败，已保底写本地缓存', flush=True)
    print(f"[ledger] 记账：今日 {rep['total']}/{SERVERCHAN_DAILY_QUOTA} 条"
          f"（本次={push_type}）", flush=True)
    return ok, rep


if __name__ == '__main__':
    import sys
    if '--reset' in sys.argv:
        empty = _empty_ledger()
        ok = save_ledger(empty)
        print('账本已清零:', ok)
    else:
        led, src = load_ledger()
        rep = usage_report(led, verbose=True)
        print(f"# Server酱推送账本  日期={rep['date']}  来源={src}")
        print(f"# 已用 {rep['total']}/{rep['quota']} 条"
              f"（个股{rep['digest_used']}｜大盘{rep['mkt_used']}｜其他{rep['other_used']}）"
              f"，剩余 {rep['left']}")
        for e in rep.get('entries', []):
            print(f"  - {e.get('ts')}  [{e.get('type')}]  slot={e.get('slot','')}  "
                  f"{e.get('env','')}  {e.get('title','')}")
