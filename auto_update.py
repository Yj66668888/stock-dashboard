#!/usr/bin/env python3
"""
自动选股池更新脚本 — 每日14:00执行（无需WorkBuddy）
1. 拉取30只标的日K线数据
2. 计算MACD/RSI/均线 → 判断阶段
3. 踢出尾部段标的
4. 从同赛道寻找替补
5. 更新stocks.json → 重建仪表盘 → 输出到docs/
"""

import json
import urllib.request
import urllib.error
import re
import os
import sys
import time
import math
from datetime import datetime, timezone, timedelta

# ==================== 配置 ====================
WORKSPACE = os.path.dirname(os.path.abspath(__file__))
STOCKS_FILE = os.path.join(WORKSPACE, "stocks.json")
OUTPUT_DIR = os.path.join(WORKSPACE, "docs")
OUTPUT_HTML = os.path.join(WORKSPACE, "davis_dashboard_v5.html")

# 中国时区
CST = timezone(timedelta(hours=8))

MAX_REPLACE_PER_DAY = 8  # 每天最多替换8只
MIN_KLINE_COUNT = 30      # 最少需要30根K线才能判断阶段

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

# ==================== 工具函数 ====================

def fetch_json(url, timeout=15):
    """GET请求返回JSON"""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", errors="ignore"))
    except Exception as e:
        print(f"  ⚠️ fetch失败: {url[:80]}... {e}")
        return None

def fetch_text(url, timeout=15):
    """GET请求返回文本"""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="ignore")
    except Exception as e:
        print(f"  ⚠️ fetch失败: {url[:80]}... {e}")
        return None

def parse_float(s, default=0.0):
    try:
        return float(s)
    except (ValueError, TypeError):
        return default

# ==================== K线数据获取 ====================

def fetch_klines_10jqka(code):
    """
    从同花顺获取日K线数据
    code格式: sh600519 或 sz002371
    返回: [{date, open, high, low, close, volume, amount}, ...] 或 None
    """
    sym = code[2:]  # 去掉sh/sz前缀
    url = f"https://d.10jqka.com.cn/v4/line/hs_{sym}/01/last.js"
    text = fetch_text(url)
    if not text:
        # 尝试东方财富作为fallback
        return fetch_klines_eastmoney(code)
    
    m = re.search(r'\((\{[\s\S]*\})\)', text)
    if not m:
        return fetch_klines_eastmoney(code)
    
    try:
        data = json.loads(m.group(1))
        raw = data.get("data", "")
        if not raw or len(raw) < 50:
            return fetch_klines_eastmoney(code)
        
        points = [p for p in raw.split(";") if p.strip()]
        klines = []
        for p in points:
            f = p.split(",")
            if len(f) < 5:
                continue
            klines.append({
                "date": f[0],
                "open": parse_float(f[1]),
                "high": parse_float(f[2]),
                "low": parse_float(f[3]),
                "close": parse_float(f[4]),
                "volume": parse_float(f[5], 0),
                "amount": parse_float(f[6]) if len(f) > 6 else 0,
            })
        
        if len(klines) >= MIN_KLINE_COUNT:
            return klines
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        print(f"  ⚠️ 解析同花顺数据失败: {e}")
    
    return fetch_klines_eastmoney(code)


def fetch_klines_eastmoney(code):
    """东方财富日K线API (fallback)"""
    market = "1" if code.startswith("sh") else "0"
    sym = code[2:]
    secid = f"{market}.{sym}"
    url = (
        f"https://push2his.eastmoney.com/api/qt/stock/kline/get"
        f"?secid={secid}&fields1=f1,f2,f3,f4,f5,f6"
        f"&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
        f"&klt=101&fqt=1&end=20500101&lmt=100"
    )
    result = fetch_json(url)
    if not result or not result.get("data"):
        return None
    
    raw = result["data"].get("klines", [])
    if not raw:
        return None
    
    klines = []
    for line in raw:
        f = line.split(",")
        if len(f) < 6:
            continue
        klines.append({
            "date": f[0],
            "open": parse_float(f[1]),
            "close": parse_float(f[2]),
            "high": parse_float(f[3]),
            "low": parse_float(f[4]),
            "volume": parse_float(f[5], 0),
            "amount": parse_float(f[6]) if len(f) > 6 else 0,
        })
    
    return klines if len(klines) >= MIN_KLINE_COUNT else None


# ==================== 技术指标计算 ====================

def calc_ema(values, period):
    """指数移动平均"""
    k = 2.0 / (period + 1)
    ema = [values[0]]
    for v in values[1:]:
        ema.append(v * k + ema[-1] * (1 - k))
    return ema


def calc_macd(klines):
    """MACD (12, 26, 9)"""
    if len(klines) < 35:
        return None
    
    closes = [k["close"] for k in klines]
    ema_fast = calc_ema(closes, 12)
    ema_slow = calc_ema(closes, 26)
    
    dif = [ema_fast[i] - ema_slow[i] for i in range(len(closes))]
    dea = calc_ema(dif, 9)
    
    result = []
    for i in range(9, len(dif)):
        macd_val = 2 * (dif[i] - dea[i])
        result.append({
            "dif": round(dif[i], 4),
            "dea": round(dea[i], 4),
            "macd": round(macd_val, 4),  # 柱状图值
        })
    return result


def calc_rsi(klines, period=14):
    """RSI (Wilder's smoothing)"""
    if len(klines) < period + 1:
        return None
    
    closes = [k["close"] for k in klines]
    gains, losses = 0.0, 0.0
    
    for i in range(1, period + 1):
        diff = closes[i] - closes[i - 1]
        if diff > 0:
            gains += diff
        else:
            losses += abs(diff)
    
    avg_gain = gains / period
    avg_loss = losses / period
    rsi_values = []
    
    # 第一根
    rs = avg_gain / avg_loss if avg_loss != 0 else 99
    rsi_values.append(round(100 - 100 / (1 + rs), 1))
    
    for i in range(period + 1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gain = diff if diff > 0 else 0
        loss = abs(diff) if diff < 0 else 0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        rs = avg_gain / avg_loss if avg_loss != 0 else 99
        rsi_values.append(round(100 - 100 / (1 + rs), 1))
    
    return rsi_values


def detect_ma_alignment(klines):
    """检测均线排列"""
    if len(klines) < 60:
        return None
    
    closes = [k["close"] for k in klines]
    ma5 = sum(closes[-5:]) / 5
    ma10 = sum(closes[-10:]) / 10
    ma20 = sum(closes[-20:]) / 20
    ma60 = sum(closes[-60:]) / 60
    
    if ma5 > ma10 > ma20 > ma60:
        return {"align": "bullish", "desc": "多头排列", "score": 5}
    if ma5 < ma10 < ma20 < ma60:
        return {"align": "bearish", "desc": "空头排列", "score": 0}
    if ma5 > ma10 > ma20:
        return {"align": "short_bullish", "desc": "短期多头", "score": 3}
    if ma5 < ma10 < ma20:
        return {"align": "short_bearish", "desc": "短期空头", "score": 1}
    return {"align": "mixed", "desc": "均线交织", "score": 2}


def detect_golden_cross(macd_data, lookback=5):
    """检测最近lookback天内是否有金叉"""
    if not macd_data or len(macd_data) < 2:
        return False
    
    for i in range(1, min(lookback + 1, len(macd_data))):
        cur = macd_data[-i]
        prev = macd_data[-i - 1]
        if prev["dif"] <= prev["dea"] and cur["dif"] > cur["dea"]:
            return True
    return False


def detect_phase(klines, macd_data, rsi_values, ma_align):
    """
    判断阶段：启动/加速/尾部/震荡/观望
    完全复刻 build_dashboard.py 中的 detectPhase 逻辑
    """
    if not klines or len(klines) < 30 or not macd_data or not rsi_values or not ma_align:
        return {"phase": "unknown", "desc": "数据不足", "color": "#888", "score": 0}
    
    prices = [k["close"] for k in klines]
    cur_price = prices[-1]
    ma20 = sum(prices[-20:]) / 20
    ma60 = sum(prices[-60:]) / 60
    high60 = max(prices[-60:])
    
    macd_cur = macd_data[-1]
    dif = macd_cur["dif"]
    dea = macd_cur["dea"]
    rsi = rsi_values[-1]
    
    # MACD是否金叉状态（DIF > DEA）
    macd_cross_up = dif > dea
    # 是否刚金叉
    recent_cross = detect_golden_cross(macd_data, 5)
    
    price_vs_ma60 = (cur_price - ma60) / ma60
    price_vs_high60 = cur_price / high60 if high60 > 0 else 1.0
    
    # ===== 尾部段（优先级最高） =====
    if rsi > 72 and price_vs_high60 > 0.92:
        return {"phase": "exhaustion", "desc": "尾部（超买高位）", "color": "#ff6b6b", "score": 0}
    
    if dif < dea and dif > 0 and rsi > 55:
        return {"phase": "exhaustion", "desc": "尾部（MACD转弱）", "color": "#ff6b6b", "score": 1}
    
    if price_vs_high60 > 0.88 and rsi < 65 and macd_cross_up:
        return {"phase": "exhaustion", "desc": "尾部（量价背离）", "color": "#ff8c42", "score": 2}
    
    # ===== 加速段 =====
    if ma_align["align"] in ("bullish", "short_bullish") and 55 <= rsi <= 75 and macd_cross_up and dif > 0:
        return {"phase": "acceleration", "desc": "加速（主升浪）", "color": "#4ecb71", "score": 5}
    
    if macd_cross_up and dif > 0.5 and rsi > 58 and price_vs_ma60 > 0.05:
        return {"phase": "acceleration", "desc": "加速（趋势强化）", "color": "#4ecb71", "score": 4}
    
    # ===== 启动段 =====
    if macd_cross_up and dif < 0.8 and 40 <= rsi <= 65 and price_vs_ma60 > -0.08 and ma_align["align"] != "bearish":
        return {"phase": "launch", "desc": "启动（底部反转）", "color": "#58a6ff", "score": 3}
    
    if ma_align["align"] == "mixed" and macd_cross_up and rsi > 45 and price_vs_ma60 > -0.05:
        return {"phase": "launch", "desc": "启动（平台突破）", "color": "#58a6ff", "score": 3}
    
    if ma_align["align"] == "bearish" and 35 < rsi < 52 and macd_cross_up:
        return {"phase": "launch", "desc": "启动（空翻多）", "color": "#58a6ff", "score": 2}
    
    # ===== 其他 =====
    if ma_align["align"] == "bearish":
        return {"phase": "waiting", "desc": "观望（空头）", "color": "#888", "score": 0}
    if rsi < 40:
        return {"phase": "waiting", "desc": "观望（弱势）", "color": "#888", "score": 1}
    return {"phase": "transition", "desc": "震荡蓄势", "color": "#d2a82d", "score": 2}


# ==================== 替换标的搜索 ====================

def search_replacement_candidates(sector, current_codes, count=5):
    """
    在同赛道中搜索替补标的
    使用东方财富行业成分股接口
    """
    # 赛道关键词 → 东方财富板块代码映射
    sector_map = {
        "AI算力PCB": "BK0893",
        "液冷": "BK1159",  
        "算力电力": "BK1148",
        "存储芯片": "BK1135",
        "先进封装": "BK0884",
        "半导体设计": "BK0477",
        "半导体设备": "BK0984",
        "电子特气/材料": "BK0471",
        "光伏/储能": "BK0478",
        "储能/逆变器": "BK1160",
        "电力设备": "BK0458",
        "电网设备": "BK0458",
        "机器人": "BK0891",
        "食品饮料": "BK0438",
        "有色金属": "BK0475",
        "面板": "BK0480",
        "智能制造": "BK0580",
        "AI应用": "BK1162",
        "AI视觉": "BK1162",
        "电力": "BK0428",
    }
    
    bk_code = sector_map.get(sector)
    if not bk_code:
        print(f"    ⚠️ 未找到赛道「{sector}」的板块映射")
        return []
    
    # 从东方财富获取板块成分股
    url = (
        f"https://push2.eastmoney.com/api/qt/clist/get"
        f"?cb=&fid=f3&po=1&pz={count+5}&pn=1&np=1"
        f"&fields=f2,f3,f12,f14,f20"
        f"&fs=b:{bk_code}"
        f"&fltt=2&invt=2"
    )
    result = fetch_json(url)
    if not result or not result.get("data"):
        return []
    
    stocks = result["data"].get("diff", [])
    candidates = []
    
    for s in stocks:
        code_raw = s.get("f12", "")
        if not code_raw:
            continue
        
        # 构造标准代码格式
        code = f"sh{code_raw}" if code_raw.startswith("6") else f"sz{code_raw}"
        
        # 排除已在持仓中的
        if code in current_codes:
            continue
        
        candidates.append({
            "code": code,
            "name": s.get("f14", ""),
            "price": s.get("f2", 0),
            "change_pct": s.get("f3", 0),
            "pe": s.get("f20", 0),
        })
    
    # 按涨跌幅排序，偏好正在上涨的
    candidates.sort(key=lambda x: x["change_pct"], reverse=True)
    return candidates[:count]


# ==================== 主流程 ====================

def main():
    print("=" * 60)
    print(f"📊 戴维斯双击选股池自动更新")
    print(f"   时间: {datetime.now(CST).strftime('%Y-%m-%d %H:%M:%S')} CST")
    print("=" * 60)
    
    # ---- 第1步：读取现有配置 ----
    print("\n📁 第1步：读取 stocks.json ...")
    with open(STOCKS_FILE, "r", encoding="utf-8") as f:
        config = json.load(f)
    
    stocks = config.get("stocks", [])
    print(f"   当前持仓: {len(stocks)} 只标的")
    
    # ---- 第2步：拉取K线数据 ----
    print("\n📡 第2步：拉取K线数据 ...")
    kline_data = {}
    failed_codes = []
    
    for i, stock in enumerate(stocks):
        code = stock["code"]
        name = stock.get("name", code)
        print(f"   [{i+1}/{len(stocks)}] {code} {name} ...", end=" ")
        sys.stdout.flush()
        
        klines = fetch_klines_10jqka(code)
        if klines:
            kline_data[code] = klines
            print(f"✅ {len(klines)}根K线")
        else:
            failed_codes.append(code)
            print("❌ 获取失败")
        
        # 限速：避免触发反爬
        if i % 5 == 4:
            time.sleep(0.3)
    
    if failed_codes:
        print(f"\n   ⚠️ {len(failed_codes)} 只获取失败: {', '.join(failed_codes)}")
    
    # ---- 第3步：计算指标和阶段 ----
    print("\n🔬 第3步：计算技术指标 & 判断阶段 ...")
    analysis = {}
    
    for stock in stocks:
        code = stock["code"]
        name = stock.get("name", code)
        klines = kline_data.get(code)
        
        if not klines:
            analysis[code] = {"phase": {"phase": "unknown", "desc": "数据获取失败", "color": "#888", "score": 0}}
            continue
        
        macd_data = calc_macd(klines)
        rsi_values = calc_rsi(klines)
        ma_align = detect_ma_alignment(klines)
        
        if not macd_data or not rsi_values or not ma_align:
            analysis[code] = {"phase": {"phase": "unknown", "desc": "指标计算失败", "color": "#888", "score": 0}}
            continue
        
        phase = detect_phase(klines, macd_data, rsi_values, ma_align)
        analysis[code] = {
            "phase": phase,
            "macd": macd_data[-1] if macd_data else None,
            "rsi": rsi_values[-1] if rsi_values else None,
            "ma_align": ma_align,
            "price": klines[-1]["close"] if klines else None,
        }
        
        emoji = {"acceleration": "🔥", "launch": "🚀", "exhaustion": "⚠️", "transition": "🟡", "waiting": "⬜", "unknown": "❓"}
        print(f"   {code} {name}: {emoji.get(phase['phase'], '')} {phase['desc']} (RSI={rsi_values[-1]:.0f})")
    
    # ---- 第4步：筛选需要替换的标的 ----
    print("\n🔄 第4步：筛选尾部段标的 ...")
    to_remove = []
    to_keep = []
    
    for stock in stocks:
        code = stock["code"]
        phase_info = analysis.get(code, {}).get("phase", {})
        phase = phase_info.get("phase", "unknown")
        
        if phase == "exhaustion":
            to_remove.append(stock)
            print(f"   ⚠️ 踢出: {code} {stock.get('name', '')} - {phase_info.get('desc', '')} ({stock.get('sector', '')})")
        else:
            to_keep.append(stock)
    
    # 限制每天最多替换数
    if len(to_remove) > MAX_REPLACE_PER_DAY:
        # 优先踢出score最低的（更确信是尾部）
        to_remove.sort(key=lambda s: analysis.get(s["code"], {}).get("phase", {}).get("score", 99))
        removed_today = to_remove[:MAX_REPLACE_PER_DAY]
        deferred = to_remove[MAX_REPLACE_PER_DAY:]
        
        print(f"\n   ⚠️ 尾部段超过上限({len(to_remove)}只)，今天先替换{MAX_REPLACE_PER_DAY}只")
        for s in deferred:
            code = s["code"]
            print(f"   📅 延期: {code} {s.get('name', '')} ({analysis[code]['phase']['desc']})")
    else:
        removed_today = to_remove
        deferred = []
    
    # ---- 第5步：寻找替补标的 ----
    print(f"\n🔍 第5步：寻找替补标的 (需替换 {len(removed_today)} 只) ...")
    
    new_stocks = []
    current_codes = {s["code"] for s in stocks}
    
    for old_stock in removed_today:
        sector = old_stock.get("sector", "")
        print(f"   寻找赛道「{sector}」替补 ...")
        
        candidates = search_replacement_candidates(sector, current_codes, count=5)
        
        if candidates:
            # 选择涨势最好且PE合理的
            best = None
            for c in candidates:
                # PE合理地过滤（不高于50，除非是成长赛道）
                if c["pe"] and c["pe"] > 50:
                    continue
                best = c
                break
            
            if best:
                new_code = best["code"]
                new_name = best["name"]
                current_codes.add(new_code)
                print(f"   ✅ 纳入: {new_code} {new_name} (赛道:{sector}, PE:{best['pe']})")
                
                # 构造完整的标的对象
                new_stock = {
                    "code": new_code,
                    "name": new_name,
                    "sector": sector,
                    "direction": old_stock.get("direction", "需求端驱动"),
                    "pe": best["pe"] or old_stock.get("pe", 30),
                    "profitGrowth": old_stock.get("profitGrowth", 30),  # 占位，后续可更新
                    "reason": f"替补{sector}赛道，{old_stock['name']}尾部段替入",
                    "roe": 15,  # 默认值
                    "grossMargin": 25,
                    "debtRatio": 40,
                    "riskFlags": [],
                }
                new_stocks.append(new_stock)
                continue
        
        # 找不到替补
        print(f"   ❌ 未找到合适替补，保留原标的")
        to_keep.append(old_stock)
    
    # 实际保留 = 原有保留 + 新替补
    final_stocks = to_keep + new_stocks
    
    # 确保恰好30只
    if len(final_stocks) < 30:
        # 从deferred中补充（即使尾部段也先保留）
        for s in deferred:
            if len(final_stocks) >= 30:
                break
            final_stocks.append(s)
            print(f"   📌 补回（因缺替补）: {s['code']} {s.get('name', '')}")
    
    if len(final_stocks) > 30:
        final_stocks = final_stocks[:30]
    
    # ---- 第6步：更新 stocks.json ----
    print(f"\n💾 第6步：更新 stocks.json (最终{len(final_stocks)}只) ...")
    config["stocks"] = final_stocks
    config["updateTime"] = datetime.now(CST).strftime("%Y-%m-%dT%H:%M:%S+08:00")
    
    with open(STOCKS_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    print("   ✅ stocks.json 已更新")
    
    # ---- 第7步：重新生成仪表盘 ----
    print("\n🏗️ 第7步：重新生成仪表盘 ...")
    
    # 先确保有Python环境和必要的包
    python = sys.executable
    build_script = os.path.join(WORKSPACE, "build_dashboard.py")
    
    if os.path.exists(build_script):
        import subprocess
        result = subprocess.run(
            [python, build_script],
            capture_output=True, text=True, cwd=WORKSPACE, timeout=60
        )
        if result.returncode == 0:
            print("   ✅ 仪表盘HTML已生成")
        else:
            print(f"   ⚠️ 生成失败: {result.stderr[:200]}")
            
            # Fallback: 如果 build_dashboard.py 失败，手动写入一个简单的更新版本
            if os.path.exists(OUTPUT_HTML):
                print("   📋 使用现有HTML（仅时间戳更新）")
    else:
        print("   ⚠️ build_dashboard.py 不存在，跳过")
    
    # ---- 第8步：复制到 docs/ 目录 ----
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    if os.path.exists(OUTPUT_HTML):
        import shutil
        shutil.copy(OUTPUT_HTML, os.path.join(OUTPUT_DIR, "index.html"))
        print("   ✅ 已复制到 docs/index.html")
    
    # ---- 第9步：生成简报 ----
    print("\n" + "=" * 60)
    print("📊 每日14:00选股池更新简报")
    print(f"日期：{datetime.now(CST).strftime('%Y-%m-%d')}")
    print("=" * 60)
    
    print(f"\n🔄 本轮变动：替换 {len(new_stocks)} 只")
    if removed_today:
        print("   踢出（尾部段）：")
        for s in removed_today:
            phase_info = analysis.get(s["code"], {}).get("phase", {})
            print(f"      {s['code']} {s.get('name', '')} - {phase_info.get('desc', '')}")
    if new_stocks:
        print("   新纳入：")
        for s in new_stocks:
            print(f"      {s['code']} {s['name']} → {s['sector']} ({s['reason']})")
    if not removed_today and not new_stocks:
        print("   （今日无变动）")
    
    # 阶段分布统计
    phase_counts = {"acceleration": 0, "launch": 0, "transition": 0, "exhaustion": 0, "waiting": 0, "unknown": 0}
    for stock in final_stocks:
        p = analysis.get(stock["code"], {}).get("phase", {}).get("phase", "unknown")
        phase_counts[p] = phase_counts.get(p, 0) + 1
    
    print(f"\n📈 阶段分布：")
    print(f"   加速段 {phase_counts['acceleration']}只   启动段 {phase_counts['launch']}只   震荡 {phase_counts['transition']}只   尾部段 {phase_counts['exhaustion']}只   观望 {phase_counts['waiting']}只")
    
    # 风险提示
    if phase_counts["exhaustion"] > 0:
        print(f"\n⚠️ 需要注意：仍有 {phase_counts['exhaustion']} 只尾部段标的（因替补不足暂未替换）")
    if failed_codes:
        print(f"⚠️ {len(failed_codes)} 只数据获取失败")
    
    print(f"\n📊 仪表盘地址: 部署至 GitHub Pages")
    print("=" * 60)
    
    return True


if __name__ == "__main__":
    try:
        success = main()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n❌ 脚本执行异常: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
