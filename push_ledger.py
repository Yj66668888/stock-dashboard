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
    # ⚠️ 但缓存可能是过期的：2026-10-28 起超过 CACHE_MAX_AGE_MIN 标记为 'stale'，
    #    调用方据此决定是否保守处理（重复推送的主要成因之一）
    try:
        with open(LOCAL_CACHE) as f:
            led = _rollover(json.load(f))
            age = _cache_age_min()
            if age is not None and age > CACHE_MAX_AGE_MIN:
                return led, 'stale'
            return led, 'cache'
    except Exception:
        return _empty_ledger(), 'empty'


def _write_cache(ledger):
    """写本地缓存，同时记下写入时刻用于判断缓存新鲜度"""
    try:
        snapshot = {k: v for k, v in ledger.items() if k != '_sha'}
        snapshot['_cached_at'] = bj_now().strftime('%Y-%m-%d %H:%M:%S')
        with open(LOCAL_CACHE, 'w') as f:
            json.dump(snapshot, f, ensure_ascii=False)
    except Exception:
        pass


def _cache_age_min():
    """本地缓存的年龄（分钟）。读不到返回 None"""
    try:
        with open(LOCAL_CACHE) as f:
            c = json.load(f)
        ts = c.get('_cached_at')
        if not ts:
            return None
        dt = datetime.strptime(ts, '%Y-%m-%d %H:%M:%S').replace(
            tzinfo=timezone(timedelta(hours=8)))
        return (bj_now() - dt).total_seconds() / 60.0
    except Exception:
        return None


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
# 内容指纹去重（2026-10-28 加）
# ============================================================
# ★ 为什么需要它（用户反馈「同一个内容连续提示了两次」）★
#
# 原先只有「按档位(slot)去重」：slot 形如 digest:am:2026-10-28、
# index_rebound_v2:2026-10-28。它能挡住同一档在同一天第二次推送，
# 但存在两个漏洞：
#
#   ① **判定与记账之间隔着一次外网 HTTP**：
#      consume_quota() 判定可推 → 调 Server酱发消息（最长 15s）→ 成功后再 append_record()。
#      这中间十几秒里，另一个进程（每 5 分钟一个 automation / 云端每 5 分钟一个 job）
#      读到的账本还是「未推送」状态的旧值 → 它也判定可推 → 同一条内容发出去两次。
#      save_ledger 的 409 重试只保证**账本写入**不互相覆盖，救不回已发出的消息。
#
#   ② **load_ledger 远端 GET 失败时会降级到本地缓存**：
#      本地缓存是「上一次远端真值的快照」，可能已经过期。
#      拿着过期快照做判定等于乐观放行 → 重复推送。（见下方保守降级修复）
#
# 内容指纹是**第二道独立的锁**：它不看档位、不看 push_type，直接比较
# 消息的标题。只要窗口期内出现过相同标题，一律拒绝 —— 这样即使 ①② 同时发生，
# 也撞不出重复。
# ---------------------------------------------------------------------------
# 去重粒度（2026-10-28 定稿，替换原先「名字写着 30 分钟、实际按天」的口径）
#   CONTENT_DEDUP_MIN = 0  → **同一天**粒度（默认，最严）
#   CONTENT_DEDUP_MIN = N  → N 分钟滑动窗口
#   PUSH_DEDUP_MIN 环境变量可实时覆盖：
#       unset → 用上面的模块默认值
#       0     → 关闭内容去重（排障/应急强制推送用）
#       N>0   → N 分钟窗口
# 之所以默认按「天」而不是分钟：本项目所有推送类型在设计上就是「一天一档」
# （见 wechat_notify.push_gate），不存在同一份内容当天需要推两次的场景。
# ---------------------------------------------------------------------------
CONTENT_DEDUP_MIN = 0
CACHE_MAX_AGE_MIN = 15      # 本地缓存超过这个年龄 → 判定为「过期」，保守拒绝


def dedup_window_min():
    """返回 -1=关闭 / 0=同一天 / >0=N分钟窗口"""
    raw = os.environ.get('PUSH_DEDUP_MIN')
    if raw is None:
        return CONTENT_DEDUP_MIN
    try:
        v = int(raw)
    except ValueError:
        return CONTENT_DEDUP_MIN
    return -1 if v == 0 else v


def _within_window(ts, minutes):
    """entry 的 ts 是否落在 minutes 分钟内"""
    try:
        dt = datetime.strptime(ts, '%Y-%m-%d %H:%M:%S').replace(
            tzinfo=timezone(timedelta(hours=8)))
        return (bj_now() - dt).total_seconds() <= minutes * 60.0
    except Exception:
        return True          # 时间读不出来 → 保守当作命中


def content_fp(title):
    """标题指纹：去掉所有空白与形如 14:30 / 10:44 的时间数字后取 md5。

    去掉时间是因为同一份内容在相邻两次运行里只有时间戳不同，
    保留会导致指纹不同而漏判。
    """
    import hashlib
    import re as _re
    t = (title or '')
    t = _re.sub(r'\d{1,2}:\d{2}(:\d{2})?', '', t)   # 去掉时间
    t = _re.sub(r'\s+', '', t)                       # 去掉所有空白
    return hashlib.md5(t.encode('utf-8')).hexdigest()[:12]

def usage_report(ledger=None, verbose=False):
    """当日用量摘要，用于 log 和拒绝原因文案"""
    led = ledger if ledger is not None else load_ledger()[0]
    ents = led.get('entries', [])
    total = len(ents)
    exhausted = led.get('exhausted')
    if exhausted:
        # Server酱 已明确返回「超过当天次数限制」→ 无论本地记了几条都视作满额
        total = SERVERCHAN_DAILY_QUOTA
    digest_used = len([e for e in ents if e.get('type', '').startswith('digest')])
    mkt_used = len([e for e in ents if e.get('type') in MKT_TYPES])
    other_used = total - digest_used - mkt_used
    rep = {
        'date': led.get('date'),
        'total': total, 'quota': SERVERCHAN_DAILY_QUOTA, 'left': SERVERCHAN_DAILY_QUOTA - total,
        'digest_used': digest_used, 'mkt_used': mkt_used, 'other_used': other_used,
        'exhausted': exhausted,
    }
    if verbose:
        rep['entries'] = ents
    return rep


def _gate_check(push_type, ledger, source, slot=None, title='', verbose=True):
    """纯判定：给定一份已加载的账本，判断能不能推。返回 (allowed, reason)

    被 consume_quota（老路径）与 claim（原子占坑路径）共用，
    保证两条路径的规则**完全一致**，不会出现「判定放行了但占坑被拒」的口径漂移。
    """
    if source == 'stale':
        # 远端不可达 + 本地缓存已过期：此刻账本可能漏记了别人刚推过的东西。
        # 保守策略 —— 宁可这一条不推，也不要重复推送白扔 Server酱 额度。
        return False, (f"账本不可靠：远端不可达且本地缓存已过期"
                       f"（>{CACHE_MAX_AGE_MIN}分钟），为避免重复推送本次跳过")

    rep = usage_report(ledger)
    total, left = rep['total'], rep['left']

    # ---- 内容指纹去重（最后一道锁，优先于额度检查）----
    # 默认按「同一天」粒度；PUSH_DEDUP_MIN=N 可切成 N 分钟窗口，=0 关闭。
    win = dedup_window_min()
    if title and win != -1:
        fp = content_fp(title)
        for e in ledger.get('entries', []):
            if e.get('fp') and e.get('fp') == fp and (win == 0 or _within_window(e.get('ts', ''), win)):
                return False, (f"内容重复：已推送过相同标题"
                               f"（{e.get('ts','')}『{(e.get('title') or '')[:30]}』）"
                               f"｜fp={fp}｜如需强制推送请设 PUSH_DEDUP_MIN=0")

    # ---- Server酱 主动上报「超过当天次数限制」→ 当天直接熔断 ----
    ex = ledger.get('exhausted')
    if ex:
        return False, (f"Server酱已回报当日超限（{ex.get('at','')} {ex.get('reason','')}）"
                       f"｜今日账本已熔断，勿重复尝试")

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


def consume_quota(push_type, slot=None, verbose=True, title=''):
    """判断本条推送能否占用额度。返回 (allowed, reason)

    ★ 这是全局硬闸门之一，所有环境（本地 automation / 云端 job）共享同一份账本 ★

    ⚠️ 这是**老路径**：「先判定 → 再发送 → 成功后记账」。
    中间隔着一次最长 15 秒的外网 HTTP，另一个进程此刻读到的账本仍是旧值，
    会跟着一起放行 → 同一条内容发两遍（2026-10-28 用户实测到的重复推送）。
    新代码请一律改用 **claim()**（判定 + 立刻占坑，一次原子 RMW）。

    slot ── 档位唯一标识（如 'digest:am:2026-10-22'）。
      传了 slot 就额外做「同一档位一天只能推一次」的跨环境去重 ——
      这正是 10/22 事故的主因：am 档被本地 automation 与云端 job 各推一遍，
      两者都只推 1 条，总配额闸门看不出异常，但用户收到重复播报且白白浪费额度。

    title ── 消息标题。用于**内容指纹去重**（见 content_fp 说明）；
      即使 slot 不同、push_type 不同，只要粒度内出现过内容相同的标题就拒绝。
    """
    ledger, source = load_ledger()
    return _gate_check(push_type, ledger, source, slot=slot, title=title, verbose=verbose)


# ============================================================
# 原子占坑（claim / release）—— 彻底封死「同一内容连推两次」
# ============================================================
# ★ 为什么 claim 比 consume_quota 强 ★
#
# consume_quota 有 15 秒的空窗：
#     A 判定放行 ──► A 调 Server酱（≤15s）──► A 成功后记账
#                 B 在这 15s 内 GET 到旧账本 ──► B 判定也能放行 ──► B 也发了
# 云端 job 与本地 automation 存在同时触发的档（两者都有 13:10 这一档），
# 这个空窗是真实会被撞上的。
#
# claim 把「判定」和「写账本」合成一步，并利用 GitHub Contents API 的
# **乐观并发（sha）**做 CAS：
#     A 读出账本(sha=X) → 判定通过 → PUT(带 sha=X) → 成功 ✅占坑
#     B 读出账本(sha=X) → 判定通过 → PUT(带 sha=X) → 409 冲突
#       → B 重新 GET（看到 A 刚写的 pending）→ 重新判定 → 被内容指纹拦下 ❌
# 于是 A/B 竞争时**必有一方被拒**，不会再出现两条一模一样的消息。
#
# 万一推送真的失败（网络/接口报错），调用方必须调 release() 把坑还回去，
# 否则会白占额度。release 同样走 CAS。
CLAIM_MAX_ATTEMPTS = 3


def _put_if_match(ledger, expect_sha):
    """PUT 一次，遵循 sha 乐观并发。返回 (conflict: bool, written: bool)

    与 save_ledger 的区别：**不做内部重试**。
    save_ledger 碰到 409 会自己重取 sha 再提交 —— 那样会把别人的 pending
    整份覆盖掉，claim 恰恰不能这么做，必须把冲突交回上层重新判定。
    """
    content = base64.b64encode(
        json.dumps({k: v for k, v in ledger.items() if k != '_sha'},
                   ensure_ascii=False, indent=2).encode('utf-8')).decode('utf-8')
    payload = {
        'message': f'push_ledger: {today_str()} 推送记账',
        'branch': 'main',
        'content': content,
    }
    if expect_sha:
        payload['sha'] = expect_sha
    status, resp = _api('PUT', REMOTE_PATH, payload)
    if status in (200, 201) and resp and resp.get('commit'):
        _write_cache(ledger)
        return False, True
    return (status == 409), False


def claim(push_type, slot=None, title='', verbose=True):
    """判定 + 立即占坑（原子）。返回 (ok, reason)

    ok=True  → 坑已占到，调用方应当**立刻发送**；发送成功无需再调 append_record
               （坑本身就是那条记账）。发送失败请务必调 release() 归还额度。
    ok=False → 被拦（额度/档位/内容重复/账本不可信），不要发送。
    """
    import time as _time
    last_reason = '未知原因'
    for attempt in range(CLAIM_MAX_ATTEMPTS):
        ledger, source = load_ledger()
        ok, reason = _gate_check(push_type, ledger, source, slot=slot,
                                 title=title, verbose=verbose and attempt == 0)
        if not ok:
            return False, reason
        last_reason = reason

        now = bj_now()
        ts = now.strftime('%Y-%m-%d %H:%M:%S')
        new = {k: v for k, v in ledger.items()}
        new['entries'] = [dict(e) for e in ledger.get('entries', [])] + [{
            'ts': ts,
            'type': push_type,
            'slot': slot or '',
            'fp': content_fp(title) if title else '',
            'env': 'cloud' if os.environ.get('GITHUB_ACTIONS') else 'local',
            'title': (title or '')[:60],
            'status': 'pending',          # 发送成功后保持不变；失败由 release 摘掉
        }]
        new['slots'] = dict(ledger.get('slots', {}))
        if slot:
            new['slots'][slot] = ts
        new['date'] = today_str()

        conflict, written = _put_if_match(new, ledger.get('_sha'))
        if written:
            rep = usage_report(new)
            print(f"[ledger] 占坑成功：今日 {rep['total']}/{SERVERCHAN_DAILY_QUOTA} 条"
                  f"（本次={push_type}）", flush=True)
            return True, f'claim ok（余 {rep["left"]} 条）'
        if not conflict:
            return False, '占坑失败：远端账本不可写（为避免重复推送放弃本次）'
        # 409 → 别人刚抢先写了一笔，重来一轮，下一轮大概率被内容指纹/档位拦下
        _time.sleep(0.5 + attempt * 0.5)

    return False, f'占坑失败：并发冲突重试 {CLAIM_MAX_ATTEMPTS} 次仍未成功（{last_reason}）'


def release(title='', slot=None):
    """归还 claim 占到的坑（推送失败时调用），避免把 Server酱 额度白扔掉。

    幂等：找不到对应的 pending 记录也算成功。
    """
    import time as _time
    for attempt in range(CLAIM_MAX_ATTEMPTS):
        ledger, _ = load_ledger()
        entries = ledger.get('entries', [])
        idx = None
        for i in range(len(entries) - 1, -1, -1):
            if entries[i].get('status') != 'pending':
                continue
            if title and entries[i].get('fp') != content_fp(title):
                continue
            idx = i
            break
        if idx is None:
            return True                       # 没有 pending，本来就不需要还
        new = {k: v for k, v in ledger.items()}
        new['entries'] = [e for j, e in enumerate(entries) if j != idx]
        new['slots'] = dict(ledger.get('slots', {}))
        if slot:
            new['slots'].pop(slot, None)
        new['date'] = today_str()
        conflict, written = _put_if_match(new, ledger.get('_sha'))
        if written:
            print('[ledger] 占坑已归还（推送失败，额度未消耗）', flush=True)
            return True
        if not conflict:
            return False
        _time.sleep(0.5 + attempt * 0.5)
    return False


def append_record(push_type, title='', slot=''):
    """推送成功后记账。返回 (ok, report)

    采用 read-modify-write，写冲突自动重试。slot 会被登记到 slots 做档位去重。
    title 会算出内容指纹 fp，供 consume_quota 做「同内容去重」。
    """
    ledger, _ = load_ledger()
    entries = ledger.setdefault('entries', [])
    slots = ledger.setdefault('slots', {})
    now = bj_now()
    entries.append({
        'ts': now.strftime('%Y-%m-%d %H:%M:%S'),
        'type': push_type,
        'slot': slot or '',
        'fp': content_fp(title) if title else '',
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


def mark_exhausted(reason='Server酱返回当日超限'):
    """Server酱明确回报「超过当天发送次数限制」时的反向同步。

    ★ 为什么要这一步 ★
    账本是**本地/云端的自记账**，Server酱 才是真正的计费方。两者可能不一致：
    例如 10/22 上午的重复推送事故里，我们账上只认到 1 条，实际已打满 5 条。
    一旦收到 40001，说明真额度已耗尽 —— 立刻把账本置为熔断，
    后续所有环境（尤其每 5 分钟一个的云端 job）直接本地拒绝，
    不再逐个白撞 HTTP 请求。
    """
    ledger, _ = load_ledger()
    if ledger.get('exhausted'):
        print(f"[ledger] 当日已熔断（{ledger['exhausted'].get('at')}），跳过重复标记", flush=True)
        return True
    ledger['exhausted'] = {
        'at': bj_now().strftime('%Y-%m-%d %H:%M:%S'),
        'reason': (reason or '')[:80],
        'by': 'cloud' if os.environ.get('GITHUB_ACTIONS') else 'local',
    }
    ledger['date'] = today_str()
    ok = save_ledger(ledger)
    print(f"[ledger] ⛔ 当日额度已熔断：{reason}", flush=True)
    if not ok:
        print('[ledger] ⚠️ 远端熔断写入失败，已保底写本地缓存', flush=True)
    return ok


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
        if rep.get('exhausted'):
            print(f"# ⛔ 当日已熔断: {rep['exhausted'].get('at')} "
                  f"{rep['exhausted'].get('reason')}（Server酱真额度已耗尽）")
        for e in rep.get('entries', []):
            print(f"  - {e.get('ts')}  [{e.get('type')}]  slot={e.get('slot','')}  "
                  f"{e.get('env','')}  {e.get('title','')}")
