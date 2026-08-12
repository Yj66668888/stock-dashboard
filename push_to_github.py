#!/usr/bin/env python3
"""推送 docs/index.html 到 GitHub Pages（走 Git Database API，绕过 GFW 对 git 协议的干扰）"""
import urllib.request, json, base64, os, sys, time

BASE = os.path.dirname(os.path.abspath(__file__))
LOCAL_FILE = os.path.join(BASE, "docs", "index.html")

TOKEN = os.environ.get("GITHUB_TOKEN", "")
if not TOKEN:
    # 回退到本地的 .github_token 文件（不纳入版本管理）
    token_file = os.path.join(BASE, ".github_token")
    if os.path.exists(token_file):
        with open(token_file) as f:
            TOKEN = f.read().strip()
if not TOKEN:
    print("ERROR: 未找到 GITHUB_TOKEN 环境变量或 .github_token 文件")
    sys.exit(1)
OWNER = "Yj66668888"
REPO = "stock-dashboard"
BRANCH = "main"
FILE_PATH = "docs/index.html"

def api(url, method="GET", data=None, timeout=120):
    headers = {
        "Authorization": f"token {TOKEN}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "stock-dashboard-updater"
    }
    if data:
        headers["Content-Type"] = "application/json"
        data = json.dumps(data).encode()
    req = urllib.request.Request(url, method=method, headers=headers, data=data)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read()
        return json.loads(body.decode()) if body else {}

def main():
    if not os.path.exists(LOCAL_FILE):
        print(f"ERROR: {LOCAL_FILE} 不存在")
        return False

    # 先同步 deploy → docs（确保最新）
    deploy_file = os.path.join(BASE, "deploy", "index.html")
    if os.path.exists(deploy_file):
        with open(deploy_file, "rb") as f:
            deploy_content = f.read()
        with open(LOCAL_FILE, "wb") as f:
            f.write(deploy_content)
        print(f"   已同步 deploy → docs ({len(deploy_content)} bytes)")

    # 注入版本号（替换 vXXXX-XXXX 为当前时间）
    import re as _re
    ver_str = time.strftime("v%m%d-%H%M")
    with open(LOCAL_FILE, "r", encoding="utf-8") as f:
        html = f.read()

    # 注入起涨前预判数据（如果存在）
    pre_breakout_file = os.path.join(BASE, "pre_breakout_results.json")
    if os.path.exists(pre_breakout_file):
        try:
            with open(pre_breakout_file, "r", encoding="utf-8") as f:
                pb_data = json.load(f)
            pb_strong = pb_data.get("strong_signals", [])[:15]
            pb_moderate = pb_data.get("moderate_signals", [])[:15]
            pb_js = f"const PRE_BREAKOUT = {json.dumps({'scan_time': pb_data.get('scan_time',''), 'strong': pb_strong, 'moderate': pb_moderate}, ensure_ascii=False)};"
            html = html.replace(
                '// PRE_BREAKOUT_DATA_PLACEHOLDER',
                pb_js
            )
            print(f"0. 起涨预判注入: 强烈{pb_data.get('strong_signals',[]).__len__()}只, 中等{pb_data.get('moderate_signals',[]).__len__()}只")
        except Exception as e:
            print(f"0. 起涨预判注入失败: {e}")

    # 预计算 KDJ 数据并注入（服务端Python计算，浏览器端零延迟加载）
    kdj_script = os.path.join(BASE, "precompute_kdj.py")
    if os.path.exists(kdj_script):
        import subprocess
        try:
            result = subprocess.run(
                [sys.executable, kdj_script, LOCAL_FILE],
                capture_output=True, text=True, timeout=120, cwd=BASE
            )
            if result.returncode == 0 and result.stdout.strip():
                kdj_data = result.stdout.strip()
                # 优先尝试占位符替换
                if '// KDJ_PRECOMPUTED_DATA_PLACEHOLDER' in html:
                    html = html.replace('// KDJ_PRECOMPUTED_DATA_PLACEHOLDER', kdj_data)
                    print(f"0. KDJ预计算(占位符): 注入成功")
                else:
                    # 兜底：正则匹配替换已有的 const KDJ_PRECOMPUTED = {...};
                    import re as _re_kdj
                    kdj_match = _re_kdj.search(
                        r'(// KDJ 预计算数据.*?\n// 数据源: [^\n]*\n)?const KDJ_PRECOMPUTED = \{.*?\};',
                        html, _re_kdj.DOTALL
                    )
                    if kdj_match:
                        html = html.replace(kdj_match.group(0), kdj_data)
                        print(f"0. KDJ预计算(regex): 替换成功")
                    else:
                        print(f"0. KDJ预计算: 未找到注入点，跳过")
                # 提取成功/失败数量
                import re as _re2
                m = _re2.search(r'成功(\d+).*?失败(\d+)', result.stderr)
                if m:
                    print(f"   成功{m.group(1)}只, 失败{m.group(2)}只")
            else:
                print(f"0. KDJ预计算失败: {result.stderr[:200]}")
        except Exception as e:
            print(f"0. KDJ预计算异常: {e}")

    # 预计算主力资金数据并注入
    # 先检查 capital_flow.json 数据新鲜度
    cf_json = os.path.join(BASE, "capital_flow.json")
    if os.path.exists(cf_json):
        try:
            with open(cf_json, "r", encoding="utf-8") as f:
                cf_check = json.load(f)
            # 找最新日期
            latest_date = ""
            for code, s in cf_check.get("stocks", {}).items():
                for d in s.get("daily", []):
                    if d.get("date", "") > latest_date:
                        latest_date = d["date"]
            today_str = time.strftime("%Y-%m-%d")
            if latest_date and latest_date < today_str:
                print(f"  ⚠️ capital_flow.json 最新日期={latest_date}, 今天={today_str} — 缓存过期！")
                print(f"  ⚠️ 建议先运行 quick_capital_flow.py 刷新资金流数据，否则前端将跳过缓存等待实时获取")
            elif not latest_date:
                print(f"  ⚠️ capital_flow.json 无日期信息 — 缓存可能为空")
        except Exception as e:
            print(f"  ⚠️ capital_flow.json 检查失败: {e}")

    cf_script = os.path.join(BASE, "precompute_capital_flow.py")
    if os.path.exists(cf_script):
        import subprocess as _sp2
        try:
            result = _sp2.run(
                [sys.executable, cf_script, LOCAL_FILE, os.path.join(BASE, "capital_flow.json")],
                capture_output=True, text=True, timeout=60, cwd=BASE
            )
            if result.returncode == 0 and result.stdout.strip():
                cf_data = result.stdout.strip()
                # 优先尝试占位符替换
                if '// CAPITAL_FLOW_CACHE_PLACEHOLDER' in html:
                    html = html.replace('// CAPITAL_FLOW_CACHE_PLACEHOLDER', cf_data)
                    print(f"0. 资金流预计算(占位符): 注入成功")
                else:
                    # 兜底：正则匹配替换已有的 const CAPITAL_FLOW_CACHE = {...};
                    import re as _re_cf
                    cf_match = _re_cf.search(
                        r'(// 主力资金预计算数据.*?\n// 数据源: capital_flow\.json[^\n]*\n// 字段: [^\n]*\n)?const CAPITAL_FLOW_CACHE = \{.*?\};',
                        html, _re_cf.DOTALL
                    )
                    if cf_match:
                        html = html.replace(cf_match.group(0), cf_data)
                        print(f"0. 资金流预计算(regex): 替换成功")
                    else:
                        print(f"0. 资金流预计算: 未找到注入点，跳过")
                import re as _re3
                m = _re3.search(r'成功(\d+).*?失败(\d+)', result.stderr)
                if m:
                    print(f"   成功{m.group(1)}只, 失败{m.group(2)}只")
            else:
                print(f"0. 资金流预计算失败: {result.stderr[:200]}")
        except Exception as e:
            print(f"0. 资金流预计算异常: {e}")

    # 预计算大盘跟随度并注入
    mc_script = os.path.join(BASE, "precompute_market_corr.py")
    if os.path.exists(mc_script):
        import subprocess as _sp3
        try:
            result = _sp3.run(
                [sys.executable, mc_script, LOCAL_FILE],
                capture_output=True, text=True, timeout=120, cwd=BASE
            )
            if result.returncode == 0 and result.stdout.strip():
                mc_data = result.stdout.strip()
                if '// MARKET_CORR_PLACEHOLDER' in html:
                    html = html.replace('// MARKET_CORR_PLACEHOLDER', mc_data)
                    print(f"0. 大盘跟随度预计算(占位符): 注入成功")
                else:
                    import re as _re_mc
                    mc_match = _re_mc.search(
                        r'(// 大盘跟随度预计算.*?\n// 基准: [^\n]*\n// 字段: [^\n]*\n)?const MARKET_CORR_CACHE = \{.*?\};',
                        html, _re_mc.DOTALL
                    )
                    if mc_match:
                        html = html.replace(mc_match.group(0), mc_data)
                        print(f"0. 大盘跟随度预计算(regex): 替换成功")
                    else:
                        print(f"0. 大盘跟随度: 未找到注入点，跳过")
                import re as _re4
                m = _re4.search(r'成功(\d+).*?失败(\d+)', result.stderr)
                if m:
                    print(f"   成功{m.group(1)}只, 失败{m.group(2)}只")
            else:
                print(f"0. 大盘跟随度预计算失败: {result.stderr[:200]}")
        except Exception as e:
            print(f"0. 大盘跟随度预计算异常: {e}")

    # 恢复 deploy/index.html 中的占位符（保证下次推送能正常工作）
    deploy_html_path = os.path.join(BASE, "deploy", "index.html")
    if os.path.exists(deploy_html_path):
        with open(deploy_html_path, "r", encoding="utf-8") as f:
            deploy_html = f.read()
        deploy_modified = False
        # 如果 deploy 里有注入过的 const 而没有占位符，替换回占位符
        if '// CAPITAL_FLOW_CACHE_PLACEHOLDER' not in deploy_html and 'const CAPITAL_FLOW_CACHE = {' in deploy_html:
            import re as _re_cf2
            deploy_html = _re_cf2.sub(
                r'(// 主力资金预计算数据.*?\n// 数据源: capital_flow\.json[^\n]*\n// 字段: [^\n]*\n)?const CAPITAL_FLOW_CACHE = \{.*?\};',
                '// CAPITAL_FLOW_CACHE_PLACEHOLDER',
                deploy_html, flags=_re_cf2.DOTALL
            )
            deploy_modified = True
            print(f"0. 已恢复 deploy 资金流占位符")

        # 恢复 KDJ 占位符
        if '// KDJ_PRECOMPUTED_DATA_PLACEHOLDER' not in deploy_html and 'const KDJ_PRECOMPUTED = {' in deploy_html:
            import re as _re_kdj2
            deploy_html = _re_kdj2.sub(
                r'(// KDJ 预计算数据.*?\n// 数据源: [^\n]*\n)?const KDJ_PRECOMPUTED = \{.*?\};',
                '// KDJ_PRECOMPUTED_DATA_PLACEHOLDER',
                deploy_html, flags=_re_kdj2.DOTALL
            )
            deploy_modified = True
            print(f"0. 已恢复 deploy KDJ占位符")

        # 恢复大盘跟随度占位符
        if '// MARKET_CORR_PLACEHOLDER' not in deploy_html and 'const MARKET_CORR_CACHE = {' in deploy_html:
            import re as _re_mc2
            deploy_html = _re_mc2.sub(
                r'(// 大盘跟随度预计算.*?\n// 基准: [^\n]*\n// 字段: [^\n]*\n)?const MARKET_CORR_CACHE = \{.*?\};',
                '// MARKET_CORR_PLACEHOLDER',
                deploy_html, flags=_re_mc2.DOTALL
            )
            deploy_modified = True
            print(f"0. 已恢复 deploy 大盘跟随度占位符")

        if deploy_modified:
            with open(deploy_html_path, "w", encoding="utf-8") as f:
                f.write(deploy_html)

    html = _re.sub(r'v\d{4}-\d{4}', ver_str, html, count=1)
    with open(LOCAL_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"1. 版本号注入: {ver_str}, 文件大小: {len(html)} bytes")

    with open(LOCAL_FILE, "rb") as f:
        content = f.read()

    # 1. 创建 blob
    b64 = base64.b64encode(content).decode()
    r = api("https://api.github.com/repos/{}/{}/git/blobs".format(OWNER, REPO), "POST", {
        "content": b64, "encoding": "base64"
    })
    if "sha" not in r:
        print(f"2. 创建blob失败: {r}")
        return False
    blob_sha = r["sha"]
    print(f"2. Blob: {blob_sha[:12]}")

    # 2. 获取当前 commit
    r = api("https://api.github.com/repos/{}/{}/git/refs/heads/{}".format(OWNER, REPO, BRANCH))
    if "object" not in r:
        print(f"3. 获取ref失败: {r}")
        return False
    commit_sha = r["object"]["sha"]
    print(f"3. 当前commit: {commit_sha[:12]}")

    # 3. 获取 tree
    r = api("https://api.github.com/repos/{}/{}/git/commits/{}".format(OWNER, REPO, commit_sha))
    tree_sha = r["tree"]["sha"]
    print(f"4. 当前tree: {tree_sha[:12]}")

    # 4. 创建新 tree（同时推送 docs/index.html 和根目录 index.html）
    r = api("https://api.github.com/repos/{}/{}/git/trees".format(OWNER, REPO), "POST", {
        "base_tree": tree_sha,
        "tree": [
            {"path": FILE_PATH, "mode": "100644", "type": "blob", "sha": blob_sha},
            {"path": "index.html", "mode": "100644", "type": "blob", "sha": blob_sha}
        ]
    })
    if "sha" not in r:
        print(f"5. 创建tree失败: {r}")
        return False
    new_tree_sha = r["sha"]
    print(f"5. 新tree: {new_tree_sha[:12]}")

    # 5. 创建 commit
    r = api("https://api.github.com/repos/{}/{}/git/commits".format(OWNER, REPO), "POST", {
        "message": "自动更新仪表盘 {}".format(time.strftime("%Y-%m-%d %H:%M")),
        "tree": new_tree_sha,
        "parents": [commit_sha]
    })
    if "sha" not in r:
        print(f"6. 创建commit失败: {r}")
        return False
    new_commit_sha = r["sha"]
    print(f"6. 新commit: {new_commit_sha[:12]}")

    # 6. 更新 ref
    r = api("https://api.github.com/repos/{}/{}/git/refs/heads/{}".format(OWNER, REPO, BRANCH), "PATCH", {
        "sha": new_commit_sha, "force": False
    })
    if "object" not in r:
        print(f"7. 更新ref失败: {r}")
        return False
    print(f"7. ✓ Ref更新成功！main → {new_commit_sha[:12]}")
    print(f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"✅ GitHub 推送完成（commit: {new_commit_sha[:12]}）")
    print(f"📄 GitHub Pages: https://yj66668888.github.io/stock-dashboard/")
    print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    return True

if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
