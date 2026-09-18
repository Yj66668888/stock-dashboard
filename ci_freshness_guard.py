#!/usr/bin/env python3
"""云端推送前「数据新鲜度」护栏 — 2026-09-18 加

背景（9/18 事故）:
  GitHub Actions 作业在**启动时刻** checkout 提交。若作业启动之后、推送之前，
  本地或其他作业向 main 推了新数据，本作业跑完仍会拿**过期 checkout** 生成的结果
  覆盖远端 → 造成「数据日期回退」（实测：9-18 的 24 只新票被 9-17 的 34 只旧票覆盖，
  且因为 rebase 用了 -X theirs "保留本次数据"，旧数据反而赢了）。

做法:
  比较 工作区 docs/index.html 与 origin/main:docs/index.html 的 STOCKS flowDate。
  若本次比远端旧 → exit(1) 拒绝推送（宁可少更一轮，也绝不让线上数据回退）。

用法（放在 pipeline 之后、Commit and push 之前）:
  python3 ci_freshness_guard.py
"""
import json
import re
import subprocess
import sys


def flow_date_from_text(html):
    m = re.search(r'const STOCKS\s*=\s*(\[.*?\]);', html, re.DOTALL)
    if not m:
        return ''
    try:
        stocks = json.loads(m.group(1))
    except Exception:
        return ''
    return stocks[0].get('flowDate', '') if stocks else ''


def flow_date_from_file(path):
    try:
        with open(path, encoding='utf-8') as f:
            return flow_date_from_text(f.read())
    except FileNotFoundError:
        return ''


def flow_date_from_ref(ref):
    r = subprocess.run(['git', 'show', f'{ref}:docs/index.html'],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return ''
    return flow_date_from_text(r.stdout)


def main():
    subprocess.run(['git', 'fetch', 'origin', 'main'], check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    local = flow_date_from_file('docs/index.html') or flow_date_from_ref('HEAD')
    remote = flow_date_from_ref('origin/main')
    print(f'[guard] 本次生成 flowDate={local or "?"} | 远端 flowDate={remote or "?"}')
    if local and remote and local < remote:
        print(f'[guard] FATAL: 本次数据({local}) 比远端({remote}) 旧 —— '
              f'作业使用了过期 checkout，拒绝推送以避免线上数据回退')
        sys.exit(1)
    print('[guard] OK: 数据新鲜度校验通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
