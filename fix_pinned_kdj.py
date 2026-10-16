"""2026-10-16 一次性修复：固定票(PINNED)从不参与选票计算，kdj30/kdj5 恒为 null。

做法：拉远端 docs/index.html（云端最新）→ 对缺 KDJ 字段的固定票用 rescan 的同口径
函数(腾讯 m30/m5 KDJ 9,3,3)补算 → 同步写回 deploy/docs/root 三份并 PUT 推送。
"""
import os, re, json, base64, time, socket, sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
import importlib.util

BASE = '/Users/fuckyouasshole/WorkBuddy/2026-07-01-11-18-34'
TK = open(os.path.join(BASE, '.github_token')).read().strip()
API = 'https://api.github.com/repos/Yj66668888/stock-dashboard'
socket.setdefaulttimeout(120)

# 复用 rescan 的 KDJ 计算（同口径，避免前端 fetchIndexKDJ30 与后端不一致）
spec = importlib.util.spec_from_file_location('rescan', os.path.join(BASE, 'rescan_low_position.py'))
R = importlib.util.module_from_spec(spec)
spec.loader.exec_module(R)
PINNED = R.PINNED

op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
HDR = {'Authorization': 'token ' + TK, 'Accept': 'application/vnd.github+json',
       'User-Agent': 'wb'}


def get_remote(path):
    r = urllib.request.Request(f'{API}/contents/{path}', headers=HDR)
    d = json.load(op.open(r, timeout=60))
    return d['sha'], base64.b64decode(d['content']).decode('utf-8')


def put(path, content_b64, sha, msg):
    body = {'message': msg, 'content': content_b64, 'sha': sha, 'branch': 'main'}
    req = urllib.request.Request(f'{API}/contents/{path}', data=json.dumps(body).encode(),
                                 headers={**HDR, 'Content-Type': 'application/json'}, method='PUT')
    return json.load(op.open(req, timeout=120))


def patch_local(paths):
    """就地补算（云端 pipeline 用）：不拉远端、不推送，只改本地 HTML 文件"""
    total = 0
    for p in paths:
        if not os.path.exists(p):
            print(f'   [SKIP] {p} 不存在')
            continue
        html = open(p, encoding='utf-8').read()
        m = re.search(r'(const STOCKS\s*=\s*)(\[.*?\])(\s*;\s*\n)', html, re.S)
        if not m:
            print(f'   [SKIP] {p} 未找到 STOCKS 数组')
            continue
        arr = json.loads(m.group(2))
        need = [s for s in arr if s['code'] in PINNED
                and (s.get('kdj30') in (None, '', 0) or s.get('kdj5') in (None, '', 0))]
        if not need:
            print(f'   [OK] {os.path.basename(p)} 固定票 KDJ 齐全，无需补算')
            continue
        print(f'   [FIX] {os.path.basename(p)} 缺 KDJ 固定票 {len(need)} 只，补算中...')

        def worker(s):
            try:
                return s['code'], R.calc_kdj_m30(s['code']), R.calc_kdj_m5(s['code'])
            except Exception as e:
                print(f'     [ERR] {s["code"]}: {e}')
                return s['code'], None, None

        res = {}
        with ThreadPoolExecutor(max_workers=3) as ex:
            for code, r30, r5 in ex.map(worker, need):
                res[code] = (r30, r5)
        fixed = 0
        for s in arr:
            if s['code'] not in res:
                continue
            r30, r5 = res[s['code']]
            if r30:
                trend = R.classify_trend(r30)
                tier = R.classify_tier(r30['k'], trend)
                s['kdj30'] = r30['k']
                s['kdj30Dir'] = '↑' if trend == 'up' else ('↗' if trend == 'prep_up' else '↓')
                s['kdj30Trend'] = trend
                s['kdj30Tier'] = tier if tier is not None else 6
            if r5:
                s['kdj5'] = r5['k']
                s['kdj5Dir'] = '↑' if r5['k'] > r5['k_prev'] else '↓'
                s['kdj5J'] = r5['j']
            if r30 or r5:
                fixed += 1
                print(f'     {s["code"]} {s.get("name","")} K30={s.get("kdj30")}'
                      f'{s.get("kdj30Dir","")} K5={s.get("kdj5")}')
        new_json = json.dumps(arr, ensure_ascii=False, indent=2)
        html = html[:m.start(2)] + new_json + html[m.end(2):]
        open(p, 'w', encoding='utf-8').write(html)
        total += fixed
        print(f'   [FIX] {os.path.basename(p)} 补算 {fixed}/{len(need)}')
    print(f'[FIX] 固定票 KDJ 就地补算完成，共 {total} 只')
    return total


def main():
    if len(sys.argv) > 2 and sys.argv[1] == '--local':
        patch_local(sys.argv[2:])
        return

    print('1) 拉取远端 docs/index.html ...')
    sha, html = get_remote('docs/index.html')
    m = re.search(r'(const STOCKS\s*=\s*)(\[.*?\])(\s*;\s*\n)', html, re.S)
    if not m:
        print('❌ 未找到 STOCKS 数组'); sys.exit(1)
    arr = json.loads(m.group(2))
    print(f'   池子 {len(arr)} 只')

    need = [s for s in arr if s['code'] in PINNED
            and (s.get('kdj30') in (None, '', 0) or s.get('kdj5') in (None, '', 0))]
    print(f'2) 缺 KDJ 的固定票 {len(need)} 只，补算中...')
    if not need:
        print('   ✅ 无需修复'); return

    def worker(s):
        try:
            return s['code'], R.calc_kdj_m30(s['code']), R.calc_kdj_m5(s['code'])
        except Exception as e:
            print(f'   [ERR] {s["code"]}: {e}')
            return s['code'], None, None

    results = {}
    with ThreadPoolExecutor(max_workers=3) as ex:
        for code, r30, r5 in ex.map(worker, need):
            results[code] = (r30, r5)

    fixed = 0
    for s in arr:
        if s['code'] not in results:
            continue
        r30, r5 = results[s['code']]
        if r30:
            trend = R.classify_trend(r30)
            tier = R.classify_tier(r30['k'], trend)
            s['kdj30'] = r30['k']
            s['kdj30Dir'] = '↑' if trend == 'up' else ('↗' if trend == 'prep_up' else '↓')
            s['kdj30Trend'] = trend
            s['kdj30Tier'] = tier if tier is not None else 6
        if r5:
            s['kdj5'] = r5['k']
            s['kdj5Dir'] = '↑' if r5['k'] > r5['k_prev'] else '↓'
            s['kdj5J'] = r5['j']
        if r30 or r5:
            fixed += 1
            print(f'   [FIX] {s["code"]} {s.get("name","")} K30={s.get("kdj30")}'
                  f'{s.get("kdj30Dir","")} K5={s.get("kdj5")}')

    new_json = json.dumps(arr, ensure_ascii=False, indent=2)
    html2 = html[:m.start(2)] + new_json + html[m.end(2):]
    print(f'3) 补算完成 {fixed}/{len(need)}，同步三份并推送...')

    msg = f'fix: 补算 {fixed} 只固定票的30分/5分KDJ字段（原样保留分支导致恒为null）'
    b64 = base64.b64encode(html2.encode('utf-8')).decode('ascii').replace('\n', '')
    for path in ('deploy/index.html', 'docs/index.html', 'index.html'):
        try:
            cur_sha, _ = get_remote(path)
        except Exception as e:
            print(f'   ⚠️ {path} 读取失败: {e}'); continue
        res = put(path, b64, cur_sha, msg)
        print(f'   ✅ {path} → {res["commit"]["sha"][:10]}')
        time.sleep(1)
    # 本地同步
    for p in ('deploy/index.html', 'docs/index.html', 'index.html'):
        open(os.path.join(BASE, p), 'w', encoding='utf-8').write(html2)
    print('✅ 本地三份已同步')


if __name__ == '__main__':
    main()
