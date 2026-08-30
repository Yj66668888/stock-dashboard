#!/usr/bin/env python3
"""
每日统一更新入口（本地版）— 与云端 cloud_pipeline 规则完全一致

流程:
  1. rescan_low_position.py     分层KD+换手率硬过滤选股(26只+4固定), 注入 deploy/index.html
  2. enrich_missing_fields.py   补齐 赛道/PE/PB/ROE/换手/资金流/共振/逻辑/healthScore
  3. precompute_daily_indicators.py  日线指标(MACD/均线/阶段) + 底部缩量建仓微信推送
  4. precompute_kdj.py          5/30/60分钟KDJ预计算(腾讯源)
  5. precompute_market_corr.py  大盘跟随度(腾讯源)
  6. pre_breakout_v2.py         起涨前预判
  7. scan_pre_launch.py         低位启动前扫描
  8. cp deploy/index.html -> docs/index.html
  9. push_to_github.py          推送 docs 到 GitHub Pages

注意: 不要再运行 auto_update_full.py / dynamic_stock_selector.py(旧换血, 已废弃)

用法:
  python daily_refresh.py            # 完整流程(选股+低位启动推送)
  python daily_refresh.py --no-scan  # 跳过选股+低位启动扫描(云端已接管), 只做补数+预计算+推送
"""
import subprocess
import sys
import os
import shutil
import time

BASE = os.path.dirname(os.path.abspath(__file__))
DEPLOY_HTML = os.path.join(BASE, 'deploy', 'index.html')
DOCS_HTML = os.path.join(BASE, 'docs', 'index.html')


def run(name, script, timeout=600):
    print(f"\n{'=' * 60}\n  {name}\n{'=' * 60}")
    t0 = time.time()
    r = subprocess.run(
        [sys.executable, os.path.join(BASE, script)],
        capture_output=True, text=True, timeout=timeout, cwd=BASE
    )
    tail = (r.stdout or '').strip().splitlines()[-3:]
    for line in tail:
        print(f"  {line}")
    if r.returncode != 0:
        print(f"  [WARN] {script} 退出码 {r.returncode}: {(r.stderr or '')[:200]}")
    else:
        print(f"  [OK] {script} ({time.time() - t0:.0f}s)")
    return r.returncode == 0


def main():
    no_scan = '--no-scan' in sys.argv
    # 推送时间闸门锚点：以本轮脚本启动时刻为准(与云端 cloud_pipeline 同机制)，
    # 本地14:20定时任务启动时刻不在任何推送窗口内，其建仓预警推送会被闸门拦下
    os.environ.setdefault('PIPELINE_START', str(time.time()))
    print(f"\n### 每日统一更新 {time.strftime('%Y-%m-%d %H:%M')} "
          f"({'只刷新数据' if no_scan else '完整流程: 选股+补数+预计算+推送'})\n")

    if not no_scan:
        run("[1/7] 分层KD选股+注入", 'rescan_low_position.py')
        run("[2/7] 补齐缺失字段", 'enrich_missing_fields.py')
        run("[7/7] 低位启动扫描", 'scan_pre_launch.py')
    else:
        print("[选股] 跳过 (--no-scan, 换票由云端8:30/13:00负责)")
        print("[低位启动] 跳过 (--no-scan, 微信推送由云端13:00负责)")

    run("[3/7] 日线指标+建仓预警", 'precompute_daily_indicators.py')
    run("[4/7] KDJ预计算(腾讯源)", 'precompute_kdj.py')
    run("[5/7] 大盘跟随度", 'precompute_market_corr.py')
    run("[6/7] 起涨前预判", 'pre_breakout_v2.py')
    # 全市场30分KDJ竞价候选池（「低位竞价」Tab用，收盘后跑=当日完整K线，云端8:30会再刷新一遍）
    run("[6b/7] 全市场KDJ竞价候选池", 'scan_kdj_auction_watchlist.py', timeout=900)

    # 同步 deploy -> docs
    os.makedirs(os.path.dirname(DOCS_HTML), exist_ok=True)
    shutil.copy2(DEPLOY_HTML, DOCS_HTML)
    print(f"\n[OK] deploy -> docs ({os.path.getsize(DOCS_HTML)} bytes)")

    # 数据完整性自检
    import re
    import json
    html = open(DOCS_HTML, encoding='utf-8').read()
    m = re.search(r'const STOCKS\s*=\s*(\[.*?\]);', html, re.DOTALL)
    stocks = json.loads(m.group(1)) if m else []
    hs = sum(1 for x in stocks if x.get('healthScore') not in (None, '', '--'))
    print(f"[自检] STOCKS {len(stocks)} 只, healthScore {hs} 只"
          f"{' ✅' if hs == len(stocks) and len(stocks) >= 30 else ' ⚠️ 字段缺失!'}")
    if hs < len(stocks):
        print("[自检] 有票缺 healthScore, 重跑补数...")
        run("[补数] enrich_missing_fields.py", 'enrich_missing_fields.py')
        shutil.copy2(DEPLOY_HTML, DOCS_HTML)

    # 推送
    if os.environ.get('SKIP_PUSH') != '1':
        run("[推送] push_to_github.py", 'push_to_github.py', timeout=180)

    print("\n### 完成: https://yj66668888.github.io/stock-dashboard/")


if __name__ == '__main__':
    main()
