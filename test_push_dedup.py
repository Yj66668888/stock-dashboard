#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""push_ledger / wechat_notify 去重与原子占坑的回归测试

用法：
    python test_push_dedup.py

★ 为什么要有这个文件 ★
2026-10-28 用户反馈「同一个内容连续提示了两次」。根因是推送链路存在
「判定 → 发送(≤15s) → 记账」的空窗，本地 automation 与云端 job 在同一分钟
双双触发时会一起通过判定。修完之后必须有一个能反复跑的回归测试，
否则以后再改推送链路没人知道会不会把这些锁又弄丢。

测试用**假的 GitHub Contents API**（内存字典 + sha 乐观并发）模拟远端账本，
完全不发真实网络请求，也不碰真实的 push_ledger.json。
sha 语义与真实 API 一致：PUT 时携带的 sha 必须等于当前文件的 sha，
否则返回 409 —— 这是 claim 能做 CAS 的前提。
"""
import base64
import importlib
import json
import os
import sys
import tempfile

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import push_ledger as P          # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=''):
    if cond:
        PASS.append(name)
        print(f'  ✅ {name}')
    else:
        FAIL.append(f'{name} {extra}')
        print(f'  ❌ {name}  {extra}')


# ============================================================
# 假远端账本
# ============================================================
class FakeRemote:
    """模拟 GitHub Contents API 的 GET/PUT，含 sha 乐观并发"""

    def __init__(self, init=None):
        self.content = json.dumps(init or P._empty_ledger(), ensure_ascii=False)
        self.sha = 'sha-%d' % (abs(hash(self.content)) % 10 ** 8)
        self.get_n = self.put_n = self.conflicts = 0

    # 供 push_ledger._api 调用，签名保持一致
    def api(self, method, path, payload=None, timeout=20):
        if method == 'GET':
            self.get_n += 1
            return 200, {
                'content': base64.b64encode(self.content.encode('utf-8')).decode('utf-8'),
                'sha': self.sha,
            }
        if method == 'PUT':
            self.put_n += 1
            got = (payload or {}).get('sha')
            if got is not None and got != self.sha:
                self.conflicts += 1
                return 409, None                    # 乐观并发冲突
            self.content = base64.b64decode(payload['content']).decode('utf-8')
            self.sha = 'sha-%d' % (abs(hash(self.content)) % 10 ** 8)
            return 200, {'commit': {'sha': self.sha}}
        return 0, None

    def snapshot(self):
        return json.loads(self.content)


def fresh_env(remote=None):
    """重置模块状态，挂上假远端 + 临时本地缓存"""
    tmpdir = tempfile.mkdtemp(prefix='push_ledger_test_')
    P.LOCAL_CACHE = os.path.join(tmpdir, '.cache_push_ledger.json')
    for k in ('PUSH_DEDUP_MIN', 'FORCE_PUSH', 'GITHUB_ACTIONS'):
        os.environ.pop(k, None)
    P._api = remote.api
    return remote


TITLE_A = '更新播报｜现在不是买点｜候选8只: 北矿科技/海源复材/林海股份'
TITLE_B = '大盘提示｜30分K到位｜上证 +0.72%'


def t_fp():
    print('\n[1] content_fp 指纹语义')
    check('同内容 → 同指纹', P.content_fp(TITLE_A) == P.content_fp(TITLE_A))
    check('不同内容 → 不同指纹', P.content_fp(TITLE_A) != P.content_fp(TITLE_B))
    check('时间前缀不影响指纹',
          P.content_fp('10:44 ' + TITLE_A) == P.content_fp('14:50 ' + TITLE_A))
    check('空白差异不影响指纹',
          P.content_fp(TITLE_A) == P.content_fp('  ' + TITLE_A + ' '))
    check('空标题不炸', P.content_fp('') == P.content_fp(None))


def t_consume_quota():
    print('\n[2] consume_quota 内容指纹去重（旧路径）')
    remote = fresh_env(FakeRemote())
    ok, why = P.consume_quota('digest', title=TITLE_A)
    check('首次放行', ok, why)
    P.append_record('digest', TITLE_A)
    ok2, why2 = P.consume_quota('digest', title=TITLE_A)
    check('同内容二次被拦', not ok2 and '内容重复' in why2, why2)
    ok3, why3 = P.consume_quota('digest', title=TITLE_B)
    check('不同内容放行', ok3, why3)

    print('\n[3] PUSH_DEDUP_MIN 开关语义')
    os.environ['PUSH_DEDUP_MIN'] = '0'
    ok4, why4 = P.consume_quota('digest', title=TITLE_A)
    check('PUSH_DEDUP_MIN=0 关闭去重后放行', ok4, why4)
    os.environ['PUSH_DEDUP_MIN'] = '30'
    ok5, why5 = P.consume_quota('digest', title=TITLE_A)
    check('30 分钟窗口内仍被拦', not ok5, why5)
    os.environ.pop('PUSH_DEDUP_MIN')
    ok6, why6 = P.consume_quota('digest', title=TITLE_A)
    check('恢复默认（按天）仍然拦住', not ok6, why6)


def t_stale():
    print('\n[4] 账本不可信时保守拒绝')
    remote = FakeRemote()
    fresh_env(remote)
    P.append_record('digest', TITLE_B)
    # 把缓存时间改老 → source='stale'
    cache = json.load(open(P.LOCAL_CACHE))
    cache['_cached_at'] = '2020-01-01 00:00:00'
    json.dump(cache, open(P.LOCAL_CACHE, 'w'))
    P.gh_token_backup = P._api
    # 让远端也不可达：GET 返回 404
    P._api = lambda m, p, payload=None, timeout=20: (404, None)
    ok, why = P.consume_quota('digest', title='全新内容ZZZ')
    check('远端不可达 + 缓存过期 → 拒绝', not ok and '账本不可靠' in why, why)
    fresh_env(remote)


def t_claim():
    print('\n[5] claim 原子占坑')
    remote = fresh_env(FakeRemote())
    ok, why = P.claim('digest', slot='digest:am', title=TITLE_A)
    check('首个 claim 成功', ok, why)
    check('占坑后账本有 1 条 pending',
          len(remote.snapshot()['entries']) == 1
          and remote.snapshot()['entries'][0]['status'] == 'pending')
    ok2, why2 = P.claim('digest', slot='digest:pm', title=TITLE_A)
    check('同内容第二次 claim 被拦（不同档位也拦）',
          not ok2 and ('内容重复' in why2 or '档位' in why2), why2)
    ok3, why3 = P.claim('digest', slot='digest:am', title=TITLE_B)
    check('同档位再 claim 被拦', not ok3 and '档位' in why3, why3)
    ok4, _ = P.claim('index_rebound_v2', title=TITLE_B + ' 2')
    check('不同内容+不同档位放行', ok4)
    total = len(remote.snapshot()['entries'])
    check('账本共 2 条（不是翻倍）', total == 2, f'实际 {total}')


def t_race():
    print('\n[6] ★ 并发竞态：模拟本地 automation 与云端 job 同时到点')
    remote = fresh_env(FakeRemote())

    # A 读出账本(sha=X) 但先不提交
    led_a, _ = P.load_ledger()
    sha_a = led_a.get('_sha')
    check('A 读到空账本', len(led_a['entries']) == 0)

    # B 完整跑一次 claim 并成功（模拟云端 job 抢先）
    ok_b, _ = P.claim('digest', slot='digest:am', title=TITLE_A)
    check('B 抢先占坑成功', ok_b)
    check('远端 sha 已变化', remote.sha != sha_a)

    # A 拿旧 sha 提交 → 必须拿到 409，然后重判、拒绝
    P._api = remote.api
    newA = dict(led_a)
    newA['entries'] = [{
        'ts': P.bj_now().strftime('%Y-%m-%d %H:%M:%S'), 'type': 'digest',
        'slot': '', 'fp': P.content_fp(TITLE_A), 'env': 'local',
        'title': TITLE_A[:60], 'status': 'pending'}]
    newA['date'] = P.today_str()
    conflict, written = P._put_if_match(newA, sha_a)
    check('旧 sha 提交 → 报冲突且不落盘', conflict and not written)

    ok_a, why_a = P.claim('digest', slot='digest:am', title=TITLE_A)
    check('A 重试后看到 B 的坑 → 被拦（这才是重复推送的真解药）',
          not ok_a, why_a)
    check('最终账本仍然只有 1 条', len(remote.snapshot()['entries']) == 1,
          str(remote.snapshot()['entries']))


def t_release():
    print('\n[7] release 归还坑')
    remote = fresh_env(FakeRemote())
    P.claim('digest', slot='digest:am', title=TITLE_A)
    before = len(remote.snapshot()['entries'])
    ok = P.release(title=TITLE_A, slot='digest:am')
    after = len(remote.snapshot()['entries'])
    check('release 成功且移除 pending', ok and before == 1 and after == 0,
          f'before={before} after={after}')
    ok2, _ = P.claim('digest', slot='digest:am', title=TITLE_A)
    check('归还后可以重新 claim', ok2)
    check('重复 release 幂等', P.release(title=TITLE_A, slot='digest:am'))


def t_wechat_gate():
    print('\n[8] wechat_notify 闸门接线')
    remote = fresh_env(FakeRemote())
    for m in list(sys.modules):
        if m in ('wechat_notify',):
            del sys.modules[m]
    try:
        W = importlib.import_module('wechat_notify')
    except Exception as e:
        check('导入 wechat_notify', False, str(e))
        return
    W._LEDGER_OK = True
    W.claim, W.release = P.claim, P.release
    ok1, mode1, why1 = W._claim_quota('digest', TITLE_A, slot='s1')
    check('首次通过且是 claim 模式', ok1 and mode1 == 'claim', why1)
    ok2, mode2, why2 = W._claim_quota('digest', TITLE_A, slot='s1')
    check('同内容第二次被拦', not ok2, why2)
    ok3, mode3, _ = W._claim_quota('digest', TITLE_B, slot='s2')
    check('不同内容放行', ok3 and mode3 == 'claim')
    # 发送失败后的归还
    W._release_claim(TITLE_B, slot='s2')
    ents = remote.snapshot()['entries']
    check('归还后 pending 被摘掉', len(ents) == 1, str(ents))


def main():
    print('=' * 72)
    print('推送去重 / 原子占坑 回归测试')
    print('=' * 72)
    t_fp()
    t_consume_quota()
    t_stale()
    t_claim()
    t_race()
    t_release()
    t_wechat_gate()
    print('\n' + '=' * 72)
    print(f'通过 {len(PASS)} 项，失败 {len(FAIL)} 项')
    for f in FAIL:
        print('  ❌', f)
    print('=' * 72)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
