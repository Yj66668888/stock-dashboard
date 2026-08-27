#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
微信推送模块 — 通过Server酱(ServerChan)推送消息到个人微信

使用方式:
    from wechat_notify import send_wechat, notify_bottom_accumulation
    send_wechat("标题", "内容markdown")
    notify_bottom_accumulation(enriched_stocks, context='指标计算')

SendKey来源优先级:
    1. 环境变量 SERVERCHAN_SENDKEY (GitHub Actions secret)
    2. wechat_config.json (本地配置, gitignored)

底部缩量建仓判定标准 (与仪表盘 lowVolAccum 筛选器完全一致):
    1. preLaunchPhase in [on_deck, approaching, building, launching]
    2. turnover > 3%
    3. volRatio < 0.95 (量比萎缩, volSignal含"缩量")
    4. dailyFlow > 0 (主力资金当日流入)
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
# 推送时间闸门（2026-08-24 用户要求：推送只在指定时间点发出）
#   - 数据刷新频率不变（云端仍每30分钟跑），只是非时间窗的推送被拦截
#   - 同一时间窗 + 同一推送类型，每天只发一次（防 docs/root 双跑重复推送）
#   - FORCE_PUSH=1 环境变量可绕过闸门（手动测试用）
# ============================================================

_PUSH_TIMES = [(10, 0), (11, 0), (13, 0), (14, 0), (14, 40)]
_PUSH_TIMES_STR = '10:00/11:00/13:00/14:00/14:40'
_PUSH_WINDOW_BEFORE = 3   # 提前3分钟开窗
_PUSH_WINDOW_AFTER = 45   # 延后45分钟关窗——GitHub cron高峰期实测延迟可达38分钟
_STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.wechat_push_state.json')


def _beijing_now():
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td
    return _dt.now(_tz(_td(hours=8)))


def _anchor_now():
    """闸门锚点时刻：优先用 PIPELINE_START(管道启动epoch秒)。

    云端 GitHub cron 可能延迟几十分钟才启动管道（midday 实测延迟38分钟），
    判断"本轮属于哪个推送窗口"必须用管道启动时刻，而不是推送执行时刻，
    否则管道跑完早已出窗，本该推送的会被误拦。本地脚本同理。
    """
    try:
        ts = float(os.environ.get('PIPELINE_START', ''))
        if ts > 0:
            from datetime import datetime as _dt, timezone as _tz, timedelta as _td
            return _dt.fromtimestamp(ts, _tz(_td(hours=8)))
    except (TypeError, ValueError):
        pass
    return _beijing_now()


def _match_push_window():
    """锚点时刻落在任一推送时间窗内则返回窗口key(如'1440')，否则返回None。

    多个窗口重叠时（如14:00窗[13:57,14:42]与14:40窗[14:37,15:25]有交叠）
    取最晚的目标时间——14:38启动的管道属于14:40档而非14:00档。
    """
    now = _anchor_now()
    minutes = now.hour * 60 + now.minute
    best = None
    for h, m in _PUSH_TIMES:
        t = h * 60 + m
        if t - _PUSH_WINDOW_BEFORE <= minutes <= t + _PUSH_WINDOW_AFTER:
            if best is None or t > best[0]:
                best = (t, f"{h:02d}{m:02d}")
    if best:
        return best[1], now
    return None, now


def _load_push_state():
    try:
        with open(_STATE_FILE, 'r') as f:
            return json.load(f)
    except Exception:
        return {}


def _save_push_state(state):
    try:
        with open(_STATE_FILE, 'w') as f:
            json.dump(state, f, ensure_ascii=False)
    except Exception:
        pass


def push_gate(push_type):
    """推送闸门检查，返回 (allowed, window, state_key)
    allowed=False 时 window 携带拦截原因说明"""
    if os.environ.get('FORCE_PUSH') == '1':
        return True, 'forced', None
    window, now = _match_push_window()
    if not window:
        return False, f"管道启动时刻{now.strftime('%H:%M')}不在推送时间窗({_PUSH_TIMES_STR})内", None
    state_key = f"{push_type}:{now.strftime('%Y-%m-%d')}"
    if _load_push_state().get(state_key) == window:
        return False, f"时间窗{window}类型{push_type}今日已推送过", None
    return True, window, state_key


# ============================================================
# 核心推送函数
# ============================================================

def send_wechat(title, desp='', push_type='default'):
    """
    通过 Server酱 推送消息到微信

    Args:
        title: 消息标题 (不超过100字)
        desp:   消息正文 (Markdown格式, 不超过32KB)
        push_type: 推送类型('bottom'建仓预警/'prelaunch'低位启动)，
                   用于时间窗去重——同一窗口不同类型互不挤占

    Returns:
        True=推送成功, False=失败/未配置/被时间闸门拦截
    """
    sendkey = load_sendkey()
    if not sendkey:
        print("[WeChat] 未配置 SendKey, 跳过推送")
        return False

    # 推送时间闸门：非指定时间点一律拦截（FORCE_PUSH=1 可绕过）
    allowed, window, state_key = push_gate(push_type)
    if not allowed:
        print(f"[WeChat] 推送被时间闸门拦截: {window}")
        return False

    # 截断防止超限
    title = title[:100]
    if desp:
        desp = desp[:30000]

    try:
        url = f'https://sctapi.ftqq.com/{sendkey}.send'
        data = urllib.parse.urlencode({
            'title': title,
            'desp': desp,
            # 指定只走微信服务号通道，避免用户在Server酱后台开启的
            # 邮箱等其他通道同时收到一份推送（2026-08-24 用户要求取消邮件推送）
            'channel': '9'
        }).encode('utf-8')
        req = urllib.request.Request(url, data=data, method='POST')
        req.add_header('User-Agent', 'StockDashboard/1.0')
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode('utf-8')
            result = json.loads(body)
            if result.get('code') == 0 or result.get('data', {}).get('error') == 'SUCCESS':
                print(f"[WeChat] 推送成功: {title}")
                if state_key:
                    state = _load_push_state()
                    state[state_key] = window
                    _save_push_state(state)
                return True
            else:
                print(f"[WeChat] 推送失败: {body}")
                return False
    except Exception as e:
        print(f"[WeChat] 推送异常: {e}")
        return False


# ============================================================
# 量比获取 — 从东方财富API批量获取 (与仪表盘 volSignal 同源)
# ============================================================

def _to_secid(code):
    """stock code -> eastmoney secid (sh601208 -> 1.601208, sz001266 -> 0.001266)"""
    code = code.strip()
    if code.startswith('sh') or code.startswith('1.'):
        num = code.lstrip('sh').lstrip('1.')
        return f'1.{num}'
    elif code.startswith('sz') or code.startswith('0.'):
        num = code.lstrip('sz').lstrip('0.')
        return f'0.{num}'
    # 纯数字：6开头=沪市，0/3开头=深市
    if code.startswith('6'):
        return f'1.{code}'
    else:
        return f'0.{code}'


def fetch_vol_ratios(codes, timeout=10):
    """
    从东方财富API批量获取量比(f10)

    Args:
        codes: 股票代码列表, 如 ['sh601208', 'sz001266', ...]
        timeout: 超时秒数

    Returns:
        dict: {code: volRatio}, 获取失败的code不包含在内
    """
    if not codes:
        return {}

    secids = ','.join(_to_secid(c) for c in codes)
    # f10=量比, f12=代码, f14=名称
    url = (f'https://push2.eastmoney.com/api/qt/ulist.np/get'
           f'?secids={secids}&fields=f10,f12,f14&fltt=2')

    result = {}
    try:
        req = urllib.request.Request(url)
        req.add_header('User-Agent', 'Mozilla/5.0')
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode('utf-8')
            data = json.loads(body)
            if not data or not data.get('data') or not data['data'].get('diff'):
                print("[WeChat] 量比API返回空数据")
                return {}
            for item in data['data']['diff']:
                vol_ratio = item.get('f10')
                raw_code = item.get('f12', '')
                if vol_ratio is None or vol_ratio == '-':
                    continue
                # 转换回 sh/sz 格式
                market = item.get('f13', 0)  # 1=沪, 0=深
                prefix = 'sh' if market == 1 else 'sz'
                code = f'{prefix}{raw_code}'
                result[code] = float(vol_ratio)
    except Exception as e:
        print(f"[WeChat] 获取量比失败: {e}")

    return result


# ============================================================
# 底部缩量建仓检测 + 推送 (与仪表盘 lowVolAccum 筛选器一致)
# ============================================================

# 防重复推送标记 (云端pipeline会对多个HTML文件跑预计算，只需推送一次)
_notification_sent = False

# 仪表盘 lowVolAccum 筛选器的阶段白名单
_DASHBOARD_PHASES = {'on_deck', 'approaching', 'building', 'launching'}


def detect_bottom_accumulation(stocks, vol_ratios=None):
    """
    从候选票中筛选"底部缩量建仓"形态的票

    判定标准 (与仪表盘 lowVolAccum 筛选器完全一致):
    1. preLaunchPhase in [on_deck, approaching, building, launching]
    2. turnover > 3%
    3. volRatio < 0.95 (量比萎缩, 等价于 volSignal 含"缩量")
    4. dailyFlow > 0 (主力资金当日流入)

    Args:
        stocks: 经过 precompute_daily_indicators.py 富化后的股票列表
                (需含 preLaunchPhase, turnover, dailyFlow 字段)
        vol_ratios: 量比字典 {code: volRatio}, 如为None则自动获取

    Returns:
        list of dict — 符合底部缩量建仓形态的票
    """
    # 如果没传量比数据，自动获取
    if vol_ratios is None:
        codes = [s.get('code', '') for s in stocks if s.get('code')]
        vol_ratios = fetch_vol_ratios(codes)
        print(f"[WeChat] 量比获取: {len(vol_ratios)}/{len(codes)} 只成功")

    results = []
    for s in stocks:
        code = s.get('code', '')
        phase = s.get('preLaunchPhase', '')
        turnover = s.get('turnover')
        daily_flow = s.get('dailyFlow') or 0
        vol_ratio = vol_ratios.get(code)

        # 1. 启动阶段
        if phase not in _DASHBOARD_PHASES:
            continue

        # 2. 换手率 > 3%
        if turnover is None or turnover <= 3:
            continue

        # 3. 量比 < 0.95 (缩量)
        #    量比数据缺失时放行 (非交易时段API可能返回空)
        if vol_ratio is not None and vol_ratio >= 0.95:
            continue

        # 4. 主力资金当日流入
        if daily_flow <= 0:
            continue

        # 附加信号 (仅用于展示, 不影响筛选)
        signals = []
        drop_20d = s.get('drop20d') or s.get('drop_20d')
        flow_5d = s.get('flow5d') or s.get('flow_5d') or 0
        flow_10d = s.get('flow10d') or s.get('flow_10d') or 0

        if vol_ratio is not None:
            if vol_ratio < 0.7:
                signals.append('极度缩量')
            elif vol_ratio < 0.85:
                signals.append('明显缩量')
            else:
                signals.append('轻微缩量')

        if drop_20d is not None:
            if drop_20d < -20:
                signals.append('超跌')
            elif drop_20d < -10:
                signals.append('回调到位')

        if flow_10d < 0 and flow_5d > 0:
            signals.append('资金反转')
        if flow_5d > flow_10d * 1.2 and flow_5d > 0:
            signals.append('资金加速')

        phase_labels = {
            'on_deck': '启动在即',
            'approaching': '接近启动',
            'building': '启动段',
            'launching': '已启动',
        }

        results.append({
            'name': s.get('name', '?'),
            'code': code,
            'health': s.get('healthScore', 0),
            'phase': phase,
            'phase_label': phase_labels.get(phase, phase),
            'turnover': turnover,
            'daily_flow': daily_flow,
            'vol_ratio': vol_ratio,
            'drop_20d': drop_20d,
            'flow_5d': flow_5d,
            'flow_10d': flow_10d,
            'signals': signals,
        })

    return results


def notify_bottom_accumulation(stocks, context='指标计算', vol_ratios=None):
    """
    检测底部缩量建仓票并推送微信通知

    Args:
        stocks: 经过 precompute_daily_indicators.py 富化后的股票列表
        context: 推送上下文
        vol_ratios: 预获取的量比字典, None则自动获取

    Returns:
        list — 符合条件的票 (可能为空)
    """
    global _notification_sent
    if _notification_sent:
        print("[WeChat] 本次运行已推送过, 跳过重复推送")
        return []

    bottom_stocks = detect_bottom_accumulation(stocks, vol_ratios)

    if not bottom_stocks:
        print(f"[WeChat] 本次{context}未发现底部缩量建仓票, 跳过推送")
        _notification_sent = True
        return []

    # 构建Markdown消息
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    # 标题直接带票名，微信通知栏不用点开就能看到
    names_str = '/'.join(s['name'] for s in bottom_stocks)
    title = f"底部建仓{len(bottom_stocks)}只: {names_str}"

    lines = [
        f"## 底部缩量建仓预警\n",
        f"> 扫描时间: {now_str}\n",
        f"> 来源: {context}\n",
        f"> 标准: 与仪表盘一致 (阶段+换手>3%+缩量+主力流入)\n",
        f"> 共发现 **{len(bottom_stocks)}只** 底部缩量建仓形态票\n\n",
        f"---\n\n",
    ]

    for i, s in enumerate(bottom_stocks, 1):
        signals_str = ' / '.join(s['signals']) if s['signals'] else '无'
        vr_str = f"{s['vol_ratio']:.2f}" if s['vol_ratio'] is not None else "N/A"
        drop_str = f"{s['drop_20d']:+.1f}%" if s['drop_20d'] is not None else "N/A"

        lines.append(f"### {i}. {s['name']} {s['code']} [{s['phase_label']}]")
        lines.append(f"- 健康分: **{s['health']:.0f}** | 换手率: {s['turnover']:.1f}%")
        lines.append(f"- 量比: **{vr_str}** (缩量) | 当日主力: **{s['daily_flow']:+.0f}万**")
        lines.append(f"- 20日涨跌: {drop_str}")
        lines.append(f"- 5日主力: {s['flow_5d']:+.0f}万 | 10日主力: {s['flow_10d']:+.0f}万")
        lines.append(f"- 信号: {signals_str}\n")

    desp = '\n'.join(lines)

    # 推送
    ok = send_wechat(title, desp, push_type='bottom')
    _notification_sent = True
    if ok:
        print(f"[WeChat] 底部缩量建仓预警已推送: {len(bottom_stocks)}只")
    else:
        print(f"[WeChat] 底部缩量建仓预警未发送(时间闸门拦截或推送失败): {len(bottom_stocks)}只")
    for s in bottom_stocks:
        vr_str = f"{s['vol_ratio']:.2f}" if s['vol_ratio'] is not None else "N/A"
        print(f"  {s['name']:6s} {s['code']} 阶段={s['phase']:12s} 换手={s['turnover']:.1f}% 量比={vr_str} 当日={s['daily_flow']:+.0f}万")

    return bottom_stocks
