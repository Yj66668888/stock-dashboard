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

# ---- 2026-10-22 新增：跨环境全局额度闸门 ----
# 背景事故：本地 / 云端 GitHub Actions(每5分钟一个全新容器) 各自把「今天推过几次」
#   记在自己的本地文件里（且都在 .gitignore 中不会被 checkout 带上），
#   于是每个容器都以为今天是第一次推 —— 同一档个股播报被推了 3 遍，
#   Server酱 5 条/日额度一上午打光。
# 修复：所有出站推送统一先过 push_ledger 的全局账本（远端 push_ledger.json，
#   带本地缓存兜底），账本才是当日额度的唯一真相源。
try:
    from push_ledger import consume_quota, append_record, mark_exhausted
    _LEDGER_OK = True
except ImportError:
    _LEDGER_OK = False
    def mark_exhausted(reason=''):
        return False


def _global_quota_ok(push_type, title='', slot=''):
    """全局额度闸门（唯一真相源）。返回 (ok, reason)

    slot 传档位标识（如 digest:am:2026-10-22）时，额外做跨环境「同档去重」。
    """
    if os.environ.get('FORCE_PUSH') == '1':
        return True, 'forced'
    if not _LEDGER_OK:
        return True, 'ledger-unavailable(降级: 仅本地闸门)'
    try:
        return consume_quota(push_type, slot=slot or None)
    except Exception as e:
        print(f"[WeChat] 账本读取异常({e})，降级为仅本地闸门")
        return True, f'ledger-error:{e}'


def _global_quota_note(push_type, title='', slot=''):
    """推送成功后记账到全局账本"""
    if not _LEDGER_OK or os.environ.get('FORCE_PUSH') == '1':
        return
    try:
        append_record(push_type, title, slot)
    except Exception as e:
        print(f"[WeChat] 账本记账失败(不影响本次推送): {e}")


def _is_quota_exceeded(text):
    """判断 Server酱 响应是否是「当日发送次数超限」"""
    t = text or ''
    return ('40001' in t) or ('发送次数限制' in t) or ('超过当天' in t)


def _handle_serverchan_error(exc=None, body=''):
    """发送失败时的收尾：若 Server酱 明确报当日超限，反向熔断全局账本。

    Server酱 才是真正的计费方，本地账本可能与它不一致（10/22 事故里
    本地只认到 1 条、实际已打满 5 条）。一旦发现 40001，
    立刻把当天账本置为熔断，后续所有环境（含每 5 分钟一个的云端 job）
    会在本地直接拒绝，不再逐个白撞 HTTP。
    """
    text = body or ''
    if exc is not None:
        try:
            raw = exc.read()
            if isinstance(raw, bytes):
                raw = raw.decode('utf-8', 'ignore')
            text = text or (raw or str(exc))
        except Exception:
            text = text or str(exc)
    reason = text
    try:                       # Server酱 返回的是 JSON，取人类可读字段
        obj = json.loads(text)
        reason = obj.get('info') or obj.get('message') or text
    except Exception:
        pass
    if not _is_quota_exceeded(text):
        return False
    print(f"[WeChat] ⛔ Server酱当日额度已耗尽: {reason[:90]}")
    if os.environ.get('FORCE_PUSH') == '1':
        return True
    try:
        mark_exhausted(str(reason)[:80] or 'Server酱返回当日超限')
    except Exception as e:
        print(f"[WeChat] 熔断写入失败: {e}")
    return True


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
#   - 同一时间窗 + 同一推送类型，每天只发一次（防 docs/root 双跑重复推送）
#   - FORCE_PUSH=1 环境变量可绕过闸门（手动测试用）
# 2026-10-14 演变：凌晨两档(10:00/13:30) → 早间用户改回原版五档，
#   云端 update 在每个窗口起跑一次（10:07/11:07/13:07/14:07/14:43）
# 2026-10-20 用户改版（第二轮）：五档 → 三档
#   ① 10:40（上午一次）② 13:10（区间 13:00-14:00）③ 14:30（区间 14:00-14:40）
#   第四档原 11:10 已删除 —— Server酱免费额度每天仅 5 条，
#   三档个股推送 + 1~2 条大盘到位信号正好卡在额度内，绝不能再多推。
#   结论式播报("所以呢?"那段)不单独发条，合并进当档推送正文，
#   更新完成后补推时复用同一 push_type，同样只占 1 条额度。
#   AFTER 保留 45 分钟容错——GitHub cron 实测延迟可达 38 分钟，漏推比晚推更糟
# ============================================================

_PUSH_TIMES = [(10, 40)]
_PUSH_TIMES_STR = '10:40 + 下午收尾档(13:45-14:45更新完成即推)'

# ★ 下午收尾档（2026-10-20 用户拍板）：
#   用户要求「下午13:50自动更新，尽量在14:30前更新好，更新好后推一次带结论的」。
#   云端 update 实际耗时 20~30 分钟，13:50 起跑 → 14:10~14:20 完成，
#   固定 14:30 窗口根本等不到（闸门只认管道启动时刻）。
#   故单独设第三档：管道启动时刻落在 [13:45, 14:45] 内即算「下午收尾档」，
#   用 PIPELINE_START 锚点 + 当日去重，13:50 和 14:30 两种起跑方式都能命中，
#   但一天只推 1 次，绝不双推。
_PM_PUSH_TYPE = 'daily_pm'
_PM_PUSH_FROM = 13 * 60 + 45     # 13:45 起
_PM_PUSH_TO = 14 * 60 + 45       # 14:45 止

# ---- 大盘通道共享额度（2026-10-20）----
# Server酱免费额度 5 条/日：2 条个股换票推送 + 3 条大盘信号 = 5 条刚好卡满。
# 大盘通道按用户要求「不限次数和时间」（全天任意时刻可触发），
# 但物理上限就是那 3 条，故用共享预算做硬闸门，防止大盘信号把额度吃光导致个股档推不出。
MKT_PUSH_BUDGET = 3
_MKT_PUSH_TYPES = ('index_rebound_v2', 'index_rebound', 'index_fundflow')

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

    allowed=False 时 window 携带拦截原因说明

    两档个股推送（用户 10/20 第三轮拍板，额度有限一天只 2 条）：
      ① digest am      : 10:40 固定窗口（早上换一次票推一次）
      ② digest pm-close: _PM_PUSH_TYPE='daily_pm'，13:45-14:45 任意时刻起跑即命中，
                   用于「13:50 自动更新 → 14:30 前更新好 → 推一次带结论」
    大盘信号（30分KDJ到位 / 资金回流）走独立脚本，不受这两档限制，全天任意时刻可推，
    但两者**共享 MKT_PUSH_BUDGET 条/日的额度**（Server酱免费额度仅 5 条/日）：
      2 条个股 + 3 条大盘 = 5 条，刚好卡满不溢出。
    其余 push_type（bottom/intraday 等）沿用原固定窗口逻辑。
    """
    if os.environ.get('FORCE_PUSH') == '1':
        return True, 'forced', None

    now = _anchor_now()
    minutes = now.hour * 60 + now.minute
    today = now.strftime('%Y-%m-%d')

    # ---- 大盘通道共享额度闸门（30分KDJ到位 / 资金回流 共用）----
    if push_type in _MKT_PUSH_TYPES:
        state = _load_push_state()
        used = int(state.get(f'mkt_budget:{today}') or 0)
        if used >= MKT_PUSH_BUDGET:
            return False, f"大盘通道今日已达共享额度上限({MKT_PUSH_BUDGET}条/日)", None
        return True, 'mkt', f'{push_type}:{today}'

    # ---- 下午收尾档：13:45-14:45 之间起跑都算，每日 1 次 ----
    if push_type == _PM_PUSH_TYPE:
        if not (_PM_PUSH_FROM <= minutes <= _PM_PUSH_TO):
            return False, f"管道启动时刻{now.strftime('%H:%M')}不在下午收尾档(13:45-14:45)内", None
        state_key = f"{push_type}:{today}"
        if _load_push_state().get(state_key):
            return False, f"下午收尾档今日已推送过", None
        return True, 'pm-close', state_key

    # ---- 其余：原固定窗口逻辑 ----
    window, _ = _match_push_window()
    if not window:
        return False, f"管道启动时刻{now.strftime('%H:%M')}不在推送时间窗({_PUSH_TIMES_STR})内", None
    state_key = f"{push_type}:{today}"
    if _load_push_state().get(state_key) == window:
        return False, f"时间窗{window}类型{push_type}今日已推送过", None
    return True, window, state_key


def digest_gate():
    """每日两档合并推送('digest')的闸门：自动识别落在哪一档。

    返回 (allowed, window_key, state_key)
      - 13:45-14:45 起跑  → 'pm-close'（下午收尾档：13:50 更新跑完即推）
      - 10:40 固定窗      → 'am'（早上换一次票推一次）
    两档各只推 1 条/天，state_key 按档位分开记账。
    2026-10-20 用户第三轮：原 13:10 午盘档删除，只留早/晚两次换票推送。
    """
    if os.environ.get('FORCE_PUSH') == '1':
        return True, 'forced', None
    now = _anchor_now()
    minutes = now.hour * 60 + now.minute
    today = now.strftime('%Y-%m-%d')

    if _PM_PUSH_FROM <= minutes <= _PM_PUSH_TO:
        key = 'pm-close'
    else:
        fixed, _ = _match_push_window()
        if not fixed:
            return False, f"管道启动时刻{now.strftime('%H:%M')}不在两档推送窗(10:40/下午收尾档)内", None
        key = 'am'

    state_key = f"digest:{key}:{today}"
    if _load_push_state().get(state_key):
        return False, f"{key} 档今日已推送过", None
    return True, key, state_key


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

    # 全局额度闸门（2026-10-22 加）：本地/云端多容器共享同一份当日账本，
    # 这是唯一能挡住「同一档被不同环境各推一遍」的地方
    quota_ok, quota_reason = _global_quota_ok(push_type, title, slot=state_key or '')
    if not quota_ok:
        print(f"[WeChat] 推送被全局额度闸门拦截: {quota_reason}")
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
                    # 大盘通道记账：共享预算逐条扣减（KDJ到位 / 资金回流 共用）
                    if window == 'mkt':
                        today = _anchor_now().strftime('%Y-%m-%d')
                        bkey = f'mkt_budget:{today}'
                        used = int(state.get(bkey) or 0) + 1
                        state[bkey] = used
                        print(f"[WeChat] 大盘通道今日已用 {used}/{MKT_PUSH_BUDGET}")
                    _save_push_state(state)
                # 全局账本记账（跨环境共享当日额度）
                _global_quota_note(push_type, title, slot=state_key or '')
                return True
            else:
                print(f"[WeChat] 推送失败: {body}")
                _handle_serverchan_error(body=body)
                return False
    except Exception as e:
        print(f"[WeChat] 推送异常: {e}")
        _handle_serverchan_error(exc=e)
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


# ============================================================
# 每日三档合并推送（digest）—— 2026-10-20 用户拍板
# ============================================================
# 用户原话：「在股票更新完成后进行一次推送」+「要有下面这段文字」，
#   并强调那段文字必须实时（"不要我这里写大盘30分趋势向下，你就写个向下"）。
#
# 三条硬约束：
# 1. Server酱免费额度每天仅 5 条，个股推送 3 档已占 3 条，大盘到位信号再占 1~2 条。
#    所以结论段**不单独发条**，而是作为每档推送正文的第一段，
#    底部缩量建仓明细接在其后 —— 一档只消耗 1 条额度。
# 2. 大盘判断实时拉腾讯分钟线重算 KDJ(9,3,3)，任何结论词都不写死。
#    五种实时情形各对应一句措辞，随数据变化：
#      超低位回升 / 趋势向下(5分金叉只是先兆) / 黄金位向上 / 高位不追 / 方向未明
# 3. 个股真到位 = 30分K<25 且拐头(↑/↗)，剔除当日跌超5%（大跌带后没拐头=接刀），
#    剔掉的票名要在正文里点名说明为什么剔。
#
# 三档：10:40(am) / 13:10(noon) / 13:45-14:45起跑(pm-close，覆盖13:50自动更新)
# ============================================================

_MARKET_BRIEF_SENT = False   # 本次进程只推一次（多HTML重复注入防重）


def _index_m5_goldencross():
    """大盘5分KDJ 是否金叉/回升（K向上 + 已上穿D 或 J低位）。失败返回 (None, '')"""
    try:
        from index_kdj_watch import fetch_index_m30
        kdj = fetch_index_m30(period='m5')
        if not kdj:
            return None, ''
        cur, prev = kdj['cur'], kdj['prev']
        golden = cur['k'] > prev['k'] and (cur['k'] > cur['d'] or cur['j'] < 30)
        return golden, f"K={cur['k']}/D={cur['d']}/J={cur['j']}"
    except Exception as e:
        print(f"[WeChat] 大盘5分KDJ获取失败: {e}")
        return None, ''


def _num(s, key, default=None):
    try:
        v = s.get(key)
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _chg_of(s):
    """当日涨跌幅：chg_today / dayChg 双字段兼容"""
    v = _num(s, 'chg_today')
    if v is None:
        v = _num(s, 'dayChg')
    return v


def _judge_market():
    """实时拉大盘30分/5分KDJ并给结论。返回 (verdict, advice, 指数行, 是否到位)"""
    try:
        from index_kdj_watch import fetch_index_m30
        idx = fetch_index_m30()
    except Exception as e:
        print(f"[WeChat] 大盘30分KDJ获取失败: {e}")
        idx = None

    if not idx:
        return ('大盘数据异常',
                '本次大盘30分KDJ未取到，以下候选仅供参考，暂不据此决策。', '—', False)

    cur, prev = idx['cur'], idx['prev']
    k30, d30, j30 = cur['k'], cur['d'], cur['j']
    rising = cur['k'] > prev['k']        # 本bar K是否向上
    above_d = k30 >= d30                 # K是否已上穿D
    golden5, m5_str = _index_m5_goldencross()
    m5_txt = f"，5分{m5_str}" if m5_str else ""
    idx_line = f"上证 {cur['close']:.2f}｜30分K={k30} D={d30} J={j30}"

    # —— 实时分情形，措辞随数据变（绝不写死"向下"）——
    if k30 < 25 and rising:
        verdict = '大盘30分K超低位回升，接近真到位'
        advice = (f"大盘30分K={k30}，已从超卖低位回升（前一bar K={prev['k']}）{m5_txt}。"
                  f"等 K 有效反穿 D（当前 D={d30}）并站稳再动手，可分批轻仓试。")
        in_zone = True
    elif not above_d and not rising:
        gc = '5分金叉只是盘中反弹先兆' if golden5 else '5分也未见金叉'
        verdict = '现在不是买点'
        advice = (f"大盘30分趋势仍向下（K={k30} 仍在 D={d30} 下方，且本bar继续走弱）{m5_txt}，{gc}。"
                  f"要等 30分 K 反穿 D + 站稳才考虑。")
        in_zone = False
    elif 40 <= k30 <= 65 and above_d and rising:
        verdict = '大盘黄金位，回调票可关注'
        advice = (f"大盘30分K={k30} 处于 40-65 黄金位，已反穿 D（{k30}≥{d30}）并向上{m5_txt}，"
                  f"到位票可按清单分批关注，仍不追高。")
        in_zone = True
    elif k30 > 65:
        verdict = '大盘高位，不追'
        advice = f"大盘30分K={k30} 已偏高（>65）{m5_txt}，只看不动手，等回调。"
        in_zone = False
    else:
        verdict = '大盘方向未明，观望'
        advice = (f"大盘30分K={k30} D={d30}（{'已反穿D' if above_d else '仍在D下方'}，"
                  f"本bar{'向上' if rising else '走弱'}）{m5_txt}，方向未明，等信号。")
        in_zone = False
    return verdict, advice, idx_line, in_zone


def build_market_brief(stocks):
    """构建「所以呢?」结论段（全部实时计算）。返回 (title, desp)"""
    now_str = datetime.now().strftime('%m-%d %H:%M')
    verdict, advice, idx_line, _ = _judge_market()

    # ---- 真到位候选：30分K<25 且拐头，剔除当日跌超5% ----
    cands, excluded = [], []
    for s in stocks:
        k30s = _num(s, 'kdj30')
        if k30s is None or k30s >= 25:
            continue
        if s.get('kdj30Dir') not in ('↑', '↗'):
            continue
        chg = _chg_of(s)
        item = {'name': s.get('name', '?'), 'code': s.get('code', ''),
                'k30': k30s, 'chg': chg}
        if chg is not None and chg <= -5:
            excluded.append(item)     # 大跌带后没真拐头，追进去就是接刀
            continue
        item.update({
            'k5': _num(s, 'kdj5', 0.0),
            'score': _num(s, 'score', 0.0),
            'flow': _num(s, 'dailyFlow', 0.0),
            'pe': _num(s, 'pe'),
            'roe': _num(s, 'roe', 0.0),
            'rank': _num(s, 'final_rank', 9999.0),
        })
        cands.append(item)

    cands.sort(key=lambda x: x['rank'])
    total = len(cands) + len(excluded)
    cands = cands[:8]

    if cands:
        names_str = '/'.join(c['name'] for c in cands)
        title = f"更新播报｜{verdict}｜候选{len(cands)}只: {names_str}"
    else:
        title = f"更新播报｜{verdict}｜暂无真到位候选"

    lines = ["## 所以呢？", "", f"> {now_str}｜{idx_line}", "",
             f"**1、{verdict}。**{advice}", ""]

    if cands:
        line2 = f"**2、真到位候选 {total} 只**（30分K<25 且拐头）"
        if excluded:
            line2 += (f"，要排除今日跌超 5% 的（{'/'.join(e['name'] for e in excluded)}）"
                      f"——大跌带后没拐头，追进去就是接刀")
        lines += [line2 + "。", "", f"**3、剩余 {len(cands)} 只**：{names_str}——"]
        pe_cands = [c for c in cands if c['pe'] and c['pe'] > 0]
        if pe_cands:
            top2 = sorted(pe_cands, key=lambda c: (c['pe'], -c['roe']))[:2]
            pick_desc = '、'.join(
                f"**{c['name']}**（PE {c['pe']:.1f}，ROE {c['roe']:.1f}）"
                if c['roe'] and c['roe'] > 0 else f"**{c['name']}**（PE {c['pe']:.1f}）"
                for c in top2)
            lines += [f"其中{pick_desc}基本面最扎实，大盘真到位时优先看。", ""]
        else:
            lines += ["基本面暂无突出标的，按技术面（30分K拐头+资金）择优。", ""]
        lines += ["<font color=\"comment\">明细｜30分K · 5分K · 今日涨跌 · 主力 · 评分</font>", ""]
        for c in cands:
            chg_s = f"{c['chg']:+.1f}%" if c['chg'] is not None else "—"
            flow_s = f"{'🟢入' if c['flow'] >= 0 else '🔴出'}{abs(c['flow']):.0f}万"
            lines += [f"· {c['name']}({c['code']}) 30分K={c['k30']:.0f}↑ · 5分K={c['k5']:.0f} · "
                      f"今日{chg_s} · {flow_s} · 评分{c['score']:.0f}", ""]
    else:
        lines += ["**2、暂无真到位候选**：池子里没有 30分K<25 且拐头的票，继续等。", ""]

    lines += ["---", "",
              "> 口径：大盘方向看30分K/D关系与本bar走向；5分金叉≠买点；"
              "候选=30分K<25且拐头，剔除当日跌超5%的接刀票。"]
    return title, '\n'.join(lines)


def _send_with_key(title, desp, state_key):
    """绕过 push_gate 直接发（state_key 已由 digest_gate 判定并记账）。

    注意：虽然绕过了「时间窗闸门」，但**全局额度闸门仍然生效** ——
    2026-10-22 事故正是出在个股digest档：本地和云端各推一遍，本地闸门拦不住对方。
    """
    sendkey = load_sendkey()
    if not sendkey:
        print("[WeChat] 未配置 SendKey, 跳过推送")
        return False

    # 全局额度闸门（必须与时间窗闸门独立存在）
    quota_ok, quota_reason = _global_quota_ok('digest', title, slot=state_key or '')
    if not quota_ok:
        print(f"[WeChat] 推送被全局额度闸门拦截: {quota_reason}")
        return False

    title = title[:100]
    desp = desp[:30000]
    try:
        url = f'https://sctapi.ftqq.com/{sendkey}.send'
        data = urllib.parse.urlencode(
            {'title': title, 'desp': desp, 'channel': '9'}).encode('utf-8')
        req = urllib.request.Request(url, data=data, method='POST')
        req.add_header('User-Agent', 'StockDashboard/1.0')
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode('utf-8')
            result = json.loads(body)
            if result.get('code') == 0 or result.get('data', {}).get('error') == 'SUCCESS':
                print(f"[WeChat] 推送成功: {title}")
                if state_key:
                    state = _load_push_state()
                    state[state_key] = 'digest'
                    _save_push_state(state)
                _global_quota_note('digest', title, slot=state_key or '')
                return True
            print(f"[WeChat] 推送失败: {body}")
            _handle_serverchan_error(body=body)
            return False
    except Exception as e:
        print(f"[WeChat] 推送异常: {e}")
        _handle_serverchan_error(exc=e)
        return False


def notify_market_brief(stocks, context='股票更新'):
    """每档合并推送：结论段 + 底部缩量建仓明细，一条消息只占 1 条 Server酱额度。"""
    global _MARKET_BRIEF_SENT
    if _MARKET_BRIEF_SENT:
        print("[WeChat] 本次运行合并推送已发过, 跳过")
        return False

    try:
        title, desp = build_market_brief(stocks)
    except Exception as e:
        print(f"[WeChat] 结论段构建失败: {e}")
        return False

    # 底部缩量建仓明细接在结论后面（同一条消息内，不额外占额度）
    try:
        bottom = detect_bottom_accumulation(stocks)
    except Exception:
        bottom = []
    if bottom:
        desp += "\n\n---\n\n### 📦 底部缩量建仓（同期命中）\n"
        for i, s in enumerate(bottom, 1):
            vr = f"{s['vol_ratio']:.2f}" if s['vol_ratio'] is not None else "N/A"
            drop = f"{s['drop_20d']:+.1f}%" if s['drop_20d'] is not None else "N/A"
            sig = ' / '.join(s['signals']) if s['signals'] else '无'
            desp += (f"{i}. **{s['name']}**({s['code']}) {s['phase_label']} · "
                     f"换手{s['turnover']:.1f}% · 量比{vr} · 当日主力{s['daily_flow']:+.0f}万 "
                     f"· 20日{drop}\n   信号: {sig}\n\n")

    allowed, window, state_key = digest_gate()
    if not allowed:
        print(f"[WeChat] 合并推送未发送: {window}")
        return False
    ok = _send_with_key(title, desp, state_key)
    _MARKET_BRIEF_SENT = True
    print(f"[WeChat] 合并推送{'已发送' if ok else '发送失败'}｜档位={window}｜来源={context}")
    return ok
