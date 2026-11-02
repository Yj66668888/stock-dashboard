#!/usr/bin/env python3
"""把「大盘双名单」的三个块（CSS / 页面 HTML / JS）**替换**到线上 docs/index.html。

与 patch_dual_to_docs.py 的区别
-------------------------------
- patch_dual_to_docs.py：远端「没有」双名单时用（首次注入，只增不替）
- 本脚本：远端「已有」双名单时用（改版，整块替换）

为什么不能直接把 deploy/index.html 覆盖到 docs/：
docs/index.html 被云端流水线注入了实时行情/KDJ/MACD 快照，比本地新，
整体覆盖会把云端的实时数据回退掉。所以只换双名单自己那几块。

用法: python patch_dual_update.py [--dry]
"""
import io
import os
import re
import sys
import base64
import json
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
TOK = open(os.path.join(BASE, '.github_token')).read().strip()
API = 'https://api.github.com/repos/Yj66668888/stock-dashboard/contents/'
OUT = os.path.join(BASE, 'docs', 'index.html')
REMOTE_DOC = 'docs/index.html'
BRANCH = 'main'


def http(url, method='GET', body=None, timeout=120):
    req = urllib.request.Request(url, method=method, headers={
        'Authorization': 'token ' + TOK,
        'Accept': 'application/vnd.github+json',
        'User-Agent': 'stock-dashboard-dual-patch',
        'Content-Type': 'application/json',
    }, data=json.dumps(body).encode() if body else None)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    return json.loads(raw.decode()) if raw else {}


def fetch_remote(path, ref=None):
    url = API + urllib.request.quote(path) + ('?ref=' + ref if ref else '')
    m = http(url)
    return m['sha'], base64.b64decode(m['content']).decode('utf-8')


def extract_blocks(src):
    """从本地 deploy 抽出三块（与 patch_dual_to_docs.py 同一套正则）"""
    b = {}
    m = re.search(r'(  /\* ===== 大盘双名单 \(2026-10-31\) ===== \*/.*?)\n</style>', src, re.S)
    b['css'] = m.group(1) if m else None

    m = re.search(r'(  <!-- Page: 大盘双名单.*?)  <!-- Page: 量价状态', src, re.S)
    b['page'] = m.group(1) if m else None

    m = re.search(r'(/\* ===== 大盘双名单 \(2026-10-31 新增\) ===== \*/.*?)\n(?=\s*</script>)',
                  src, re.S)
    b['js'] = ('\n' + m.group(1)) if m else None
    return b


def replace_block(doc, start_pat, end_pat, new, name, stat):
    """把 doc 里 start_pat … end_pat 之间（不含 end_pat）的旧块整体换成 new"""
    si = re.search(start_pat, doc)
    if not si:
        stat[name] = '❌ 远端找不到起始标记'
        return doc
    ei = re.search(end_pat, doc[si.start():])
    if not ei:
        stat[name] = '❌ 远端找不到结束标记'
        return doc
    end = si.start() + ei.start()
    old = doc[si.start():end]
    if old.strip() == new.strip():
        stat[name] = '= 无变化'
        return doc
    stat[name] = '✓ 替换 %d → %d 字符' % (len(old), len(new))
    return doc[:si.start()] + new + doc[end:]


def main():
    dry = '--dry' in sys.argv
    src = io.open(os.path.join(BASE, 'deploy', 'index.html'), encoding='utf-8').read()
    b = extract_blocks(src)
    for k, v in b.items():
        print('  提取 %-5s %s' % (k, ('%d 字符' % len(v)) if v else '❌ 未找到'))
    if not all(b.values()):
        print('\n!! deploy 里缺少分块，终止')
        return 1

    print('\n拉取远端 %s …' % REMOTE_DOC)
    rsha, doc = fetch_remote(REMOTE_DOC, BRANCH)
    print('  远端 sha=%s  大小=%d' % (rsha[:8], len(doc)))

    stat = {}
    new = replace_block(doc, r'/\* ===== 大盘双名单 \(2026-10-31\) ===== \*/',
                        r'\n</style>', b['css'], 'css', stat)
    new = replace_block(new, r'  <!-- Page: 大盘双名单',
                        r'  <!-- Page: 量价状态', b['page'], 'page', stat)
    new = replace_block(new, r'/\* ===== 大盘双名单 \(2026-10-31 新增\) ===== \*/',
                        r'\n\s*</script>', b['js'], 'js', stat)

    print('  结果：')
    for k, v in stat.items():
        print('    %-5s %s' % (k, v))
    if not any('✓' in v for v in stat.values()):
        print('\n!! 没有实际改动，终止（远端可能已是最新版）')
        return 0

    if dry:
        print('\n[dry] 未写入。新大小 = %d（原 %d，%+.0f）'
              % (len(new), len(doc), len(new) - len(doc)))
        return 0

    io.open(OUT, 'w', encoding='utf-8').write(new)
    print('\n已写入本地 %s（%+.0f 字符）' % (OUT, len(new) - len(doc)))
    print('接下来请用 push_files_api.py 推送（base_tree 取远端 head，安全）：')
    print('  python push_files_api.py --files docs/index.html dual_picks.json '
          '--message "feat: 双名单加日K趋势档（日线方向轴）"')
    return 0


if __name__ == '__main__':
    sys.exit(main())
