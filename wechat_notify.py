#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
微信推送模块 — 通过Server酱(ServerChan)推送消息到个人微信

使用方式:
    from wechat_notify import send_wechat, notify_bottom_accumulation
    send_wechat("标题", "内容markdown")
    notify_bottom_accumulation(final_picks)

SendKey来源优先级:
    1. 环境变量 SERVERCHAN_SENDKEY (GitHub Actions secret)
    2. wechat_config.json (本地配置, gitignored)
"""

import json
import os
import urllib.request
import urllib.parse
from datetime import datetime

# ============================================================
# 配置
# ============================================================

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_CONFIG_FILE = os.path.join(_SCRIPT_DIR, 'wechat_config.json')


def load_sendkey():
    """读取 SendKey — 优先环境变量，其次配置文件"""
    # 1. 环境变量 (GitHub Actions secret)
    key = os.environ.get('SERVERCHAN_SENDKEY', '').strip()
    if key:
        return key
    # 2. 本地配置文件
    if os.path.exists(_CONFIG_FILE):
        try:
            with open(_CONFIG_FILE, 'r') as f:
                return json.load(f).get('sendkey', '').strip() or None
        except Exception:
            pass
    return None


# ============================================================
# 核心推送函数
# ============================================================

def send_wechat(title, desp=''):
    """
    通过 Server酱 推送消息到微信

    Args:
        title: 消息标题 (不超过32字)
        desp:   消息正文 (Markdown格式, 不超过32KB)

    Returns:
        True=推送成功, False=失败或未配置
    """
    sendkey = load_sendkey()
    if not sendkey:
        print("[WeChat] 未配置 SendKey, 跳过推送")
        return False

    # 截断防止超限
    title = title[:100]
    if desp:
        desp = desp[:30000]

    try:
        url = f'https://sctapi.ftqq.com/{sendkey}.send'
        data = urllib.parse.urlencode({
            'title': title,
            'desp': desp
        }).encode('utf-8')
        req = urllib.request.Request(url, data=data, method='POST')
        req.add_header('User-Agent', 'StockDashboard/1.0')
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode('utf-8')
            result = json.loads(body)
            if result.get('code') == 0 or result.get('data', {}).get('error') == 'SUCCESS':
                print(f"[WeChat] 推送成功: {title}")
                return True
            else:
                print(f"[WeChat] 推送失败: {body}")
                return False
    except Exception as e:
        print(f"[WeChat] 推送异常: {e}")
        return False


# ============================================================
# 底部缩量建仓检测 + 推送
# ============================================================

def detect_bottom_accumulation(candidates):
    """
    从候选票中筛选"底部缩量建仓"形态的票

    判定标准 (全部满足):
    1. 低位: drop_20d < -5 (20日内跌超5%, 处于相对底部)
    2. 资金流入: flow_5d > 0 (5日主力净流入为正)
    3. 缩量: avg_vol_up < 0.95 (近期量能萎缩, 卖盘枯竭)
    4. 30分KDJ不高: kdj30_k < 50 (30分钟级别未超买, 有上涨空间)
    5. 换手率合理: 2% <= turnover <= 5% (有流动性但不过热)

    额外加分项 (不强制):
    - 资金加速: flow_5d > flow_10d * 1.2 (5日流入加速)
    - 资金反转: flow_10d < 0 且 flow_5d > 0 (资金从流出转流入)
    - 极度缩量: avg_vol_up < 0.7 (极度萎缩, 变盘前兆)

    Args:
        candidates: 选股结果列表, 每个元素是候选票dict (c)

    Returns:
        list of dict — 符合底部缩量建仓形态的票
    """
    results = []
    for c in candidates:
        drop_20d = c.get('drop_20d')
        flow_5d = c.get('flow_5d', 0) or 0
        flow_10d = c.get('flow_10d')
        avg_vol_up = c.get('avg_vol_up')
        kdj30_k = c.get('kdj30_k')
        turnover = c.get('turnover')

        # 跳过数据缺失的
        if drop_20d is None or flow_5d is None:
            continue

        # 1. 低位
        is_low = drop_20d < -5
        # 2. 资金流入
        is_inflow = flow_5d > 0
        # 3. 缩量
        is_shrinking = avg_vol_up is not None and avg_vol_up < 0.95
        # 4. 30分KDJ不高
        is_low_kdj = kdj30_k is not None and kdj30_k < 50
        # 5. 换手率合理
        if turnover is not None:
            is_reasonable_turnover = 2.0 <= turnover <= 5.0
        else:
            is_reasonable_turnover = True  # 无换手率数据时放行

        if is_low and is_inflow and is_shrinking and is_low_kdj and is_reasonable_turnover:
            # 额外信号
            signals = []
            if flow_10d is not None and flow_10d < 0 and flow_5d > 0:
                signals.append('资金反转')
            if flow_10d is not None and flow_5d > flow_10d * 1.2 and flow_5d > 0:
                signals.append('资金加速')
            if avg_vol_up is not None and avg_vol_up < 0.7:
                signals.append('极度缩量')
            if -20 <= drop_20d <= -10:
                signals.append('回调到位')
            elif drop_20d < -20:
                signals.append('超跌')

            results.append({
                'name': c.get('name', '?'),
                'code': c.get('code', '?'),
                'health': c.get('health', 0),
                'score': c.get('score', 0),
                'drop_20d': drop_20d,
                'flow_5d': flow_5d,
                'flow_10d': flow_10d or 0,
                'avg_vol_up': avg_vol_up,
                'kdj30_k': kdj30_k,
                'kdj30_rising': c.get('kdj30_rising'),
                'turnover': turnover,
                'signals': signals,
                'sector': c.get('sector_tier'),
                'consecutive': c.get('consecutive'),
            })

    return results


def notify_bottom_accumulation(candidates, context='选股'):
    """
    检测底部缩量建仓票并推送微信通知

    Args:
        candidates: 选股结果列表
        context: 推送上下文 (选股/扫描/全量)

    Returns:
        list — 符合条件的票 (可能为空)
    """
    bottom_stocks = detect_bottom_accumulation(candidates)

    if not bottom_stocks:
        print(f"[WeChat] 本次{context}未发现底部缩量建仓票, 跳过推送")
        return []

    # 构建Markdown消息
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    title = f"底部缩量建仓预警 ({len(bottom_stocks)}只)"

    lines = [
        f"## 底部缩量建仓预警\n",
        f"> 扫描时间: {now_str}\n",
        f"> 来源: {context}\n",
        f"> 共发现 **{len(bottom_stocks)}只** 底部缩量建仓形态票\n\n",
        f"---\n\n",
    ]

    for i, s in enumerate(bottom_stocks, 1):
        arrow = 'UP' if s['kdj30_rising'] else 'DOWN'
        signals_str = ' / '.join(s['signals']) if s['signals'] else '无明显附加信号'
        sec_str = f" T{s['sector']}" if s['sector'] else ''

        lines.append(f"### {i}. {s['name']} {s['code']}{sec_str}")
        lines.append(f"- 健康分: **{s['health']:.1f}** | 评分: {s['score']:.1f}")
        lines.append(f"- 20日涨跌: **{s['drop_20d']:+.1f}%**")
        lines.append(f"- 5日主力: **{s['flow_5d']:+.0f}万** | 10日主力: {s['flow_10d']:+.0f}万")
        lines.append(f"- 30分KDJ: K={s['kdj30_k']:.0f} {arrow}")
        vol_str = f"{s['avg_vol_up']:.2f}" if s['avg_vol_up'] is not None else "N/A"
        lines.append(f"- 量比: {vol_str} | 换手率: {s['turnover']:.1f}%" if s['turnover'] else f"- 量比: {vol_str}")
        lines.append(f"- 信号: {signals_str}\n")

    desp = '\n'.join(lines)

    # 推送
    send_wechat(title, desp)
    print(f"[WeChat] 底部缩量建仓预警已推送: {len(bottom_stocks)}只")
    for s in bottom_stocks:
        print(f"  {s['name']:6s} {s['code']} 20日{s['drop_20d']:+.1f}% 5日{s['flow_5d']:+.0f}万 K30={s['kdj30_k']:.0f}")

    return bottom_stocks
