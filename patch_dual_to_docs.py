#!/usr/bin/env python3
"""把「大盘双名单」的四个块（CSS / 导航按钮 / 页面 HTML / JS）注入线上 docs/index.html。

背景
----
- deploy/index.html = 源模板（本地权威，云端每轮会用它覆盖 docs）
- docs/index.html   = 注入了实时行情/KDJ 后的产物（远端比本地新）
  → **不能整体覆盖**，否则会把云端的实时数据回退掉。

本脚本的做法：以「远端 docs」为底，只把双名单的四个块追加进去，
其余内容原样保留，最后通过 push_files_api.py（base_tree 取远端 head）推送。

四个块的来源 = 本地 deploy/index.html（与远端 deploy 已校验一致）。

用法: python patch_dual_to_docs.py [--dry]
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
REPO_API = 'https://api.github.com/repos/Yj66668888/stock-dashboard'
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
    """从本地 deploy 抽出四块"""
    b = {}
    m = re.search(r'(  /\* ===== 大盘双名单 \(2026-10-31\) ===== \*/.*?)\n</style>', src, re.S)
    b['css'] = m.group(1) if m else None

    m = re.search(r'(  <!-- Page: 大盘双名单.*?)  <!-- Page: 量价状态', src, re.S)
    b['page'] = m.group(1) if m else None

    m = re.search(r'(    <button class="nav-btn" onclick="switchPage\(\'dual\', event\)">[^\n]*\n)', src)
    b['nav'] = m.group(1) if m else None

    # 注意：deploy 里这块 JS 在文件末尾（最后一个 </script> 之前），注释无缩进
    m = re.search(r'(/\* ===== 大盘双名单 \(2026-10-31 新增\) ===== \*/.*?)\n(?=\s*</script>)',
                  src, re.S)
    b['js'] = ('\n' + m.group(1)) if m else None
    return b


def inject(doc, b):
    """把四块注入远端 docs 内容"""
    n = {}

    # 1) CSS —— 放在 </style> 前
    if b['css'] and '.dual-banner{' not in doc:
        doc = doc.replace('</style>', b['css'] + '\n</style>', 1)
        n['css'] = 1

    # 2) 导航按钮 —— 放在「量价状态」按钮前
    if b['nav'] and "switchPage('dual'" not in doc:
        anchor = ('<button class="nav-btn" onclick="switchPage(\'volume\', event)">'
                  '📉 量价状态</button>')
        if anchor in doc:
            doc = doc.replace(anchor, b['nav'] + anchor, 1)
            n['nav'] = 1

    # 3) 页面 HTML —— 放在「Page: 量价状态」注释前
    if b['page'] and 'id="page-dual"' not in doc:
        anchor = '  <!-- Page: 量价状态'
        if anchor in doc:
            doc = doc.replace(anchor, b['page'] + anchor, 1)
            n['page'] = 1

    # 4) JS —— 放在最后一个 </script> 前
    if b['js'] and 'function initDualOnce' not in doc:
        i = doc.rfind('</script>')
        if i > 0:
            doc = doc[:i] + b['js'] + '\n' + doc[i:]
            n['js'] = 1

    # 5) switchPage 懒加载钩子
    hook_old = '  // 量价状态页：首次进入时懒加载日K并计算'
    hook_new = ("  // 大盘双名单页：首次进入时加载名单 + 实时判定大盘状态\n"
                "  if (pageId === 'dual') { initDualOnce(); }\n") + hook_old
    if hook_old in doc and "pageId === 'dual'" not in doc:
        doc = doc.replace(hook_old, hook_new, 1)
        n['hook'] = 1

    return doc, n


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

    if 'id="page-dual"' in doc and 'function initDualOnce' in doc:
        print('  ✓ 远端已有完整双名单，无需打补丁')
        return 0

    new, n = inject(doc, b)
    print('  注入：%s' % (', '.join('%s✓' % k for k in n) or '无'))

    if not n:
        print('  !! 无任何注入，终止')
        return 1

    if dry:
        print('\n[dry] 未写入。新大小 = %d（原 %d，+%d）'
              % (len(new), len(doc), len(new) - len(doc)))
        return 0

    io.open(OUT, 'w', encoding='utf-8').write(new)
    print('\n已写入本地 %s（+%d 字符）' % (OUT, len(new) - len(doc)))
    print('接下来请用 push_files_api.py 推送（base_tree 取远端 head，安全）：')
    print('  python push_files_api.py --files docs/index.html '
          '--message "feat: 大盘双名单同步到 docs（保留云端实时数据）"')
    return 0


if __name__ == '__main__':
    sys.exit(main())
