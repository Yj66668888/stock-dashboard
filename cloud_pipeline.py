#!/usr/bin/env python3
"""
Cloud Pipeline - GitHub Actions 云端自动化编排
================================================
支持两种模式：
  full:   全市场扫描 -> 选股 -> 起涨预判 -> 预计算 -> 部署
  update: 起涨预判 -> 预计算 -> 部署

用法:
  python cloud_pipeline.py full    # 早盘前全量扫描（~20分钟）
  python cloud_pipeline.py update  # 交易时段更新（~5分钟）
"""
import json, os, re, subprocess, sys, time, shutil

BASE = os.path.dirname(os.path.abspath(__file__))
DEPLOY_HTML = os.path.join(BASE, 'deploy', 'index.html')
DOCS_HTML = os.path.join(BASE, 'docs', 'index.html')
DOCS_DIR = os.path.join(BASE, 'docs')
ROOT_HTML = os.path.join(BASE, 'index.html')  # GitHub Pages 从根目录构建


def run_script(name, script_name, timeout=300):
    """运行脚本，返回是否成功"""
    script_path = os.path.join(BASE, script_name)
    if not os.path.exists(script_path):
        print(f"  [SKIP] {script_name} 不存在")
        return False

    print(f"\n{'=' * 60}")
    print(f"  {name}")
    print(f"  > {script_name}")
    print(f"{'=' * 60}")

    try:
        result = subprocess.run(
            [sys.executable, script_path],
            capture_output=True, text=True,
            timeout=timeout, cwd=BASE
        )
        if result.stdout:
            print(result.stdout[-3000:] if len(result.stdout) > 3000 else result.stdout)
        if result.stderr:
            # 只打印最后2000字符避免刷屏
            print("[stderr]", result.stderr[-2000:] if len(result.stderr) > 2000 else result.stderr)
        if result.returncode != 0:
            print(f"  [WARN] {name} 退出码={result.returncode}，继续下一步")
            return False
        print(f"  [OK] {name}")
        return True
    except subprocess.TimeoutExpired:
        print(f"  [WARN] {name} 超时({timeout}s)，继续下一步")
        return False
    except Exception as e:
        print(f"  [WARN] {name} 异常: {e}，继续下一步")
        return False


def inject_precomputation(html_path):
    """在 docs/index.html 上运行预计算脚本并注入数据（复刻 push_to_github.py 逻辑）"""
    with open(html_path, 'r', encoding='utf-8') as f:
        html = f.read()

    # 1. KDJ 预计算
    kdj_script = os.path.join(BASE, 'precompute_kdj.py')
    if os.path.exists(kdj_script):
        try:
            result = subprocess.run(
                [sys.executable, kdj_script, html_path],
                capture_output=True, text=True, timeout=120, cwd=BASE
            )
            if result.returncode == 0 and result.stdout.strip():
                kdj_data = result.stdout.strip()
                if '// KDJ_PRECOMPUTED_DATA_PLACEHOLDER' in html:
                    html = html.replace('// KDJ_PRECOMPUTED_DATA_PLACEHOLDER', kdj_data)
                    print("  [OK] KDJ预计算(占位符): 注入成功")
                else:
                    html = re.sub(
                        r'(// KDJ 预计算数据.*?\n// 数据源: [^\n]*\n)?const KDJ_PRECOMPUTED = \{.*?\};',
                        kdj_data, html, flags=re.DOTALL
                    )
                    print("  [OK] KDJ预计算(regex): 替换成功")
                m = re.search(r'成功(\d+).*?失败(\d+)', result.stderr)
                if m:
                    print(f"        成功{m.group(1)}只, 失败{m.group(2)}只")
            else:
                print(f"  [WARN] KDJ预计算失败: {result.stderr[:200]}")
        except Exception as e:
            print(f"  [WARN] KDJ预计算异常: {e}")

    # 2. 资金流预计算
    cf_script = os.path.join(BASE, 'precompute_capital_flow.py')
    cf_file = os.path.join(BASE, 'capital_flow.json')
    if os.path.exists(cf_script) and os.path.exists(cf_file):
        try:
            result = subprocess.run(
                [sys.executable, cf_script, html_path, cf_file],
                capture_output=True, text=True, timeout=60, cwd=BASE
            )
            if result.returncode == 0 and result.stdout.strip():
                cf_data = result.stdout.strip()
                if '// CAPITAL_FLOW_CACHE_PLACEHOLDER' in html:
                    html = html.replace('// CAPITAL_FLOW_CACHE_PLACEHOLDER', cf_data)
                    print("  [OK] 资金流预计算(占位符): 注入成功")
                else:
                    html = re.sub(
                        r'(// 主力资金预计算数据.*?\n// 数据源: capital_flow\.json[^\n]*\n// 字段: [^\n]*\n)?const CAPITAL_FLOW_CACHE = \{.*?\};',
                        cf_data, html, flags=re.DOTALL
                    )
                    print("  [OK] 资金流预计算(regex): 替换成功")
                m = re.search(r'成功(\d+).*?失败(\d+)', result.stderr)
                if m:
                    print(f"        成功{m.group(1)}只, 失败{m.group(2)}只")
            else:
                print(f"  [WARN] 资金流预计算失败: {result.stderr[:200]}")
        except Exception as e:
            print(f"  [WARN] 资金流预计算异常: {e}")

    # 3. 大盘跟随度预计算
    mc_script = os.path.join(BASE, 'precompute_market_corr.py')
    if os.path.exists(mc_script):
        try:
            result = subprocess.run(
                [sys.executable, mc_script, html_path],
                capture_output=True, text=True, timeout=120, cwd=BASE
            )
            if result.returncode == 0 and result.stdout.strip():
                mc_data = result.stdout.strip()
                if '// MARKET_CORR_PLACEHOLDER' in html:
                    html = html.replace('// MARKET_CORR_PLACEHOLDER', mc_data)
                    print("  [OK] 大盘跟随度预计算(占位符): 注入成功")
                else:
                    html = re.sub(
                        r'(// 大盘跟随度预计算.*?\n// 基准: [^\n]*\n// 字段: [^\n]*\n)?const MARKET_CORR_CACHE = \{.*?\};',
                        mc_data, html, flags=re.DOTALL
                    )
                    print("  [OK] 大盘跟随度预计算(regex): 替换成功")
                m = re.search(r'成功(\d+).*?失败(\d+)', result.stderr)
                if m:
                    print(f"        成功{m.group(1)}只, 失败{m.group(2)}只")
            else:
                print(f"  [WARN] 大盘跟随度预计算失败: {result.stderr[:200]}")
        except Exception as e:
            print(f"  [WARN] 大盘跟随度预计算异常: {e}")

    # 注入版本号
    ver_str = time.strftime("v%m%d-%H%M")
    html = re.sub(r'v\d{4}-\d{4}', ver_str, html, count=1)
    print(f"  [OK] 版本号: {ver_str}")

    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"  [OK] docs/index.html 已保存 ({len(html)} bytes)")


def validate_js_syntax(html_path):
    """验证 HTML 文件中的 JavaScript 语法，防止推送有语法错误的页面

    用 node --check 检查所有 <script> 块的 JS 代码。
    返回 True 如果语法正确，False 如果有错误。
    """
    with open(html_path, 'r', encoding='utf-8') as f:
        html = f.read()

    # 提取所有 <script> 块（不含外部 src 引用）
    scripts = re.findall(r'<script(?:\s[^>]*)?>(.*?)</script>', html, re.DOTALL)
    if not scripts:
        print(f"  [WARN] {os.path.basename(html_path)} 未找到 <script> 块")
        return True  # 没有 script 块不算错误

    js_code = '\n'.join(scripts)
    # 必须以 .js 结尾：GitHub Runner 2026-08-25 起 Node 升到 24，
    # node --check 拒绝 .js.tmp 等未知扩展名(ERR_UNKNOWN_FILE_EXTENSION)，
    # 曾导致安全网误判语法错误、云端管道连续两天全军覆没
    tmp_path = html_path + '.check.js'
    with open(tmp_path, 'w', encoding='utf-8') as f:
        f.write(js_code)

    try:
        result = subprocess.run(
            ['node', '--check', tmp_path],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            print(f"  [OK] JS 语法验证通过: {os.path.basename(html_path)} ({len(js_code)} chars)")
            return True
        else:
            print(f"  [ERROR] JS 语法错误: {os.path.basename(html_path)}")
            print(f"  {result.stderr[:500]}")
            return False
    except FileNotFoundError:
        # node 不存在时跳过验证（不阻断流程）
        print(f"  [SKIP] node 未安装，跳过 JS 语法验证: {os.path.basename(html_path)}")
        return True
    except Exception as e:
        print(f"  [WARN] JS 语法验证异常: {e}")
        return True
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def restore_deploy_placeholders():
    """恢复 deploy/index.html 中的占位符"""
    if not os.path.exists(DEPLOY_HTML):
        return
    with open(DEPLOY_HTML, 'r', encoding='utf-8') as f:
        html = f.read()

    modified = False

    if '// CAPITAL_FLOW_CACHE_PLACEHOLDER' not in html and 'const CAPITAL_FLOW_CACHE = {' in html:
        html = re.sub(
            r'(// 主力资金预计算数据.*?\n// 数据源: capital_flow\.json[^\n]*\n// 字段: [^\n]*\n)?const CAPITAL_FLOW_CACHE = \{.*?\};',
            '// CAPITAL_FLOW_CACHE_PLACEHOLDER', html, flags=re.DOTALL
        )
        modified = True
        print("  [OK] deploy 资金流占位符已恢复")

    if '// KDJ_PRECOMPUTED_DATA_PLACEHOLDER' not in html and 'const KDJ_PRECOMPUTED = {' in html:
        html = re.sub(
            r'(// KDJ 预计算数据.*?\n// 数据源: [^\n]*\n)?const KDJ_PRECOMPUTED = \{.*?\};',
            '// KDJ_PRECOMPUTED_DATA_PLACEHOLDER', html, flags=re.DOTALL
        )
        modified = True
        print("  [OK] deploy KDJ占位符已恢复")

    if '// MARKET_CORR_PLACEHOLDER' not in html and 'const MARKET_CORR_CACHE = {' in html:
        html = re.sub(
            r'(// 大盘跟随度预计算.*?\n// 基准: [^\n]*\n// 字段: [^\n]*\n)?const MARKET_CORR_CACHE = \{.*?\};',
            '// MARKET_CORR_PLACEHOLDER', html, flags=re.DOTALL
        )
        modified = True
        print("  [OK] deploy 大盘跟随度占位符已恢复")

    if modified:
        with open(DEPLOY_HTML, 'w', encoding='utf-8') as f:
            f.write(html)


def full_scan(with_prelaunch=False):
    """全量扫描模式：市场扫描 -> 选股 -> 起涨预判 -> 预计算 -> 部署
    with_prelaunch=True 时附带低位启动扫描+微信推送（midday 模式用，13:00午盘数据新鲜）"""
    print("=" * 60)
    print("  Cloud Pipeline - FULL SCAN MODE" + (" + PRELAUNCH" if with_prelaunch else ""))
    print(f"  {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # Step 1: 全市场预测扫描（生成 daily_predictions.json）
    run_script("[1/9] 全市场预测扫描", 'gap_up_predictor.py', timeout=600)

    # Step 2: 基本面因子扫描（生成 fundamental_factors.json）
    run_script("[2/9] 基本面因子扫描", 'gap_up_fundamental_factors.py', timeout=300)

    # Step 3: 主力资金流扫描（依赖 daily_predictions.json）
    run_script("[3/9] 主力资金流扫描", 'gap_up_capital_flow.py', timeout=600)

    # Step 4: KDJ因子扫描
    run_script("[4/9] KDJ因子扫描", 'gap_up_kdj_factor.py', timeout=300)

    # Step 5: 板块共振扫描
    run_script("[5/9] 板块共振扫描", 'gap_up_sector_resonance.py', timeout=120)

    # Step 6: 概念板块预筛选
    run_script("[6/9] 概念板块预筛选", 'sector_pre_filter.py', timeout=120)

    # Step 7: 低位重扫 + 注入HTML（分层KD放宽 + 换手率2-7%硬过滤，与本地规则一致）
    #         替代旧 dynamic_stock_selector.py（无分层KD/换手率硬过滤，导致云端推送票≠仪表盘票）
    os.environ['SKIP_PUSH'] = '1'
    run_script("[7/9] 低位重扫+注入HTML(分层KD/换手率硬过滤)", 'rescan_low_position.py', timeout=300)

    # Step 7b: 补齐字段（赛道/PE/PB/ROE/主力资金5日10日/板块共振/逻辑/healthScore）
    #          rescan 注入的票默认 dailyFlow=0/sector空，必须补数否则前端列全"--"且评分为0
    run_script("[7b/9] 补齐缺失字段", 'enrich_missing_fields.py', timeout=300)

    # Step 7c: 全市场30分KDJ竞价候选池（供「低位竞价」Tab：60/00主板 K≤35 且上升/待升，注入 deploy/index.html）
    #          竞价阶段当天首根30分K未走完，30分KDJ停在上一交易日收盘状态 → 盘前预扫即可
    run_script("[7c/9] 全市场KDJ竞价候选池", 'scan_kdj_auction_watchlist.py', timeout=900)

    # Step 8: 起涨前预判
    run_script("[8/9] 起涨前预判扫描", 'pre_breakout_v2.py', timeout=300)

    # Step 8b: 低位启动扫描（只扫仪表盘30只选票，有信号则微信推送 channel=9）
    #          仅 midday 模式执行——8:30盘前30分K线是隔夜数据，会误报
    if with_prelaunch:
        run_script("[8b/9] 低位启动扫描(仪表盘选票,微信推送)", 'scan_pre_launch.py', timeout=180)

    # Step 9: 复制 deploy -> docs + root + 预计算注入
    print(f"\n{'=' * 60}")
    print("  [9/9] 预计算注入 + 部署准备")
    print(f"{'=' * 60}")

    os.makedirs(DOCS_DIR, exist_ok=True)
    shutil.copy2(DEPLOY_HTML, DOCS_HTML)
    shutil.copy2(DEPLOY_HTML, ROOT_HTML)
    print(f"  [OK] deploy -> docs + root ({os.path.getsize(DOCS_HTML)} bytes)")

    inject_precomputation(DOCS_HTML)
    inject_precomputation(ROOT_HTML)

    # 日线指标预计算（MACD/均线/趋势/支撑价/高开率/板块共振/阶段）
    di_script = os.path.join(BASE, 'precompute_daily_indicators.py')
    if os.path.exists(di_script):
        for html_file in [DOCS_HTML, ROOT_HTML]:
            try:
                result = subprocess.run(
                    [sys.executable, di_script, html_file],
                    capture_output=True, text=True, timeout=300, cwd=BASE
                )
                if result.returncode == 0:
                    print(f"  [OK] 日线指标预计算: {os.path.basename(html_file)} 注入成功")
                else:
                    print(f"  [WARN] 日线指标预计算失败: {result.stderr[:200]}")
            except Exception as e:
                print(f"  [WARN] 日线指标预计算异常: {e}")

    # ★ JS 语法验证安全网 — 防止推送有语法错误的 HTML 导致页面白屏
    print(f"\n{'=' * 60}")
    print("  JS 语法验证")
    print(f"{'=' * 60}")
    js_ok = True
    for html_file in [DOCS_HTML, ROOT_HTML]:
        if not validate_js_syntax(html_file):
            js_ok = False
    if not js_ok:
        print("\n  [FATAL] JS 语法验证失败！中止推送以保护线上页面。")
        print("  请检查 deploy/index.html 和预计算注入脚本。")
        sys.exit(1)

    restore_deploy_placeholders()

    print("\n" + "=" * 60)
    print("  FULL SCAN DONE - ready for git commit & push")
    print("=" * 60)


def update_only():
    """更新模式：起涨预判 -> 预计算 -> 部署"""
    print("=" * 60)
    print("  Cloud Pipeline - UPDATE MODE")
    print(f"  {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # Step 1: 起涨前预判（更新 deploy/index.html 中的 preBreakoutScore 等字段）
    run_script("[1/3] 起涨前预判扫描", 'pre_breakout_v2.py', timeout=300)

    # Step 2: 复制 deploy -> docs + root
    print(f"\n{'=' * 60}")
    print("  [2/3] 复制 + 预计算注入")
    print(f"{'=' * 60}")

    os.makedirs(DOCS_DIR, exist_ok=True)
    shutil.copy2(DEPLOY_HTML, DOCS_HTML)
    shutil.copy2(DEPLOY_HTML, ROOT_HTML)
    print(f"  [OK] deploy -> docs + root ({os.path.getsize(DOCS_HTML)} bytes)")

    # Step 3: 预计算注入（KDJ + 资金流 + 大盘跟随度 + 日线指标）
    inject_precomputation(DOCS_HTML)
    inject_precomputation(ROOT_HTML)

    # 日线指标预计算（MACD/均线/趋势/支撑价/高开率/板块共振/阶段）
    di_script = os.path.join(BASE, 'precompute_daily_indicators.py')
    if os.path.exists(di_script):
        for html_file in [DOCS_HTML, ROOT_HTML]:
            try:
                result = subprocess.run(
                    [sys.executable, di_script, html_file],
                    capture_output=True, text=True, timeout=300, cwd=BASE
                )
                if result.returncode == 0:
                    print(f"  [OK] 日线指标预计算: {os.path.basename(html_file)} 注入成功")
                else:
                    print(f"  [WARN] 日线指标预计算失败: {result.stderr[:200]}")
            except Exception as e:
                print(f"  [WARN] 日线指标预计算异常: {e}")

    # ★ JS 语法验证安全网 — 防止推送有语法错误的 HTML 导致页面白屏
    print(f"\n{'=' * 60}")
    print("  JS 语法验证")
    print(f"{'=' * 60}")
    js_ok = True
    for html_file in [DOCS_HTML, ROOT_HTML]:
        if not validate_js_syntax(html_file):
            js_ok = False
    if not js_ok:
        print("\n  [FATAL] JS 语法验证失败！中止推送以保护线上页面。")
        print("  请检查 deploy/index.html 和预计算注入脚本。")
        sys.exit(1)

    restore_deploy_placeholders()

    print("\n" + "=" * 60)
    print("  UPDATE DONE - ready for git commit & push")
    print("=" * 60)


if __name__ == '__main__':
    # 推送时间闸门锚点：以"本轮管道启动时刻"为准。
    # GitHub cron 高峰期可能延迟数十分钟（实测 midday 延迟38分钟才启动），
    # 若按推送执行时刻判断窗口，跑完早就出窗了，本该推送的会被误拦。
    os.environ.setdefault('PIPELINE_START', str(time.time()))
    mode = sys.argv[1] if len(sys.argv) > 1 else 'update'
    if mode == 'full':
        full_scan()
    elif mode == 'midday':
        # 午盘模式：换票 + 低位启动扫描微信推送（13:00 workflow 用）
        full_scan(with_prelaunch=True)
    elif mode == 'update':
        update_only()
    else:
        print(f"未知模式: {mode}")
        print("用法: python cloud_pipeline.py [full|midday|update]")
        sys.exit(1)
