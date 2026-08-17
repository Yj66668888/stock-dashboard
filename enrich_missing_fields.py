#!/usr/bin/env python3
"""给仪表盘当前30只票补齐缺失字段：
   赛道(sector) / 基本面(pe/pb/roe) / 换手率 / 主力资金(dailyFlow/flow5d/flow10d)
   板块共振(sectorResonance/sectorTier) / 逻辑(reason) / healthScore
   数据源：push2delay.eastmoney.com(估值行业) + push2his.eastmoney.com(资金流)
   全部用 subprocess curl --noproxy '*' 绕过本地代理（同 quick_capital_flow.py 方案）
"""
import json, re, subprocess, time, os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

BASE = "/Users/fuckyouasshole/WorkBuddy/2026-07-01-11-18-34"
HTML = f"{BASE}/deploy/index.html"
DYN_FILE = f"{BASE}/dynamic_stocks.json"
SECTOR_FILTER_FILE = f"{BASE}/sector_filter.json"

_CLEAN_ENV = {k: v for k, v in os.environ.items() if 'proxy' not in k.lower()}


def curl_get_json(url, retries=4, timeout=10):
    for attempt in range(retries):
        try:
            r = subprocess.run(
                ['curl', '-s', '--noproxy', '*', '--connect-timeout', str(timeout),
                 '-H', 'User-Agent: Mozilla/5.0', url],
                capture_output=True, text=True, timeout=timeout + 5, env=_CLEAN_ENV)
            if r.returncode == 0 and r.stdout.strip():
                return json.loads(r.stdout)
        except Exception:
            pass
        if attempt < retries - 1:
            time.sleep(0.4)
    return None


def pure_code(c):
    return c[-6:]


def fetch_quote(code):
    """行业/PE/PB/ROE/换手率 push2delay"""
    p = pure_code(code)
    secid = ('1.' if p.startswith('6') else '0.') + p
    url = (f'https://push2delay.eastmoney.com/api/qt/stock/get?secid={secid}'
           f'&fields=f57,f58,f127,f162,f167,f173,f168,f116'
           f'&ut=b2884a393a59ad64002292a3e90d46a5')
    d = curl_get_json(url)
    if not d or not d.get('data'):
        return None
    data = d['data']
    def div(v, n=100):
        try:
            return round(float(v) / n, 2)
        except (TypeError, ValueError):
            return None
    return {
        'industry': data.get('f127') or '',
        'pe': div(data.get('f162')),
        'pb': div(data.get('f167')),
        'roe': round(float(data['f173']), 2) if data.get('f173') is not None else None,
        'turnover': div(data.get('f168')),
        'circ_mv_yi': round(float(data['f116']) / 1e8, 1) if data.get('f116') else None,
    }


def fetch_all_flows(codes):
    """批量获取当日/5日/10日主力净额（ulist.np 接口，一次拉全部）
       f62=当日主力净额 f164=5日主力净额 f174=10日主力净额（单位:元，转万元）
       注: push2his fflow历史接口不稳定，此接口实测与fflow明细一致
    """
    secids = ','.join(('1.' if pure_code(c).startswith('6') else '0.') + pure_code(c) for c in codes)
    url = (f'https://push2delay.eastmoney.com/api/qt/ulist.np/get'
           f'?secids={secids}&fields=f12,f14,f62,f164,f174&fltt=2&invt=2')
    d = curl_get_json(url)
    if not d:
        return {}
    out = {}
    today = datetime.now().strftime('%Y-%m-%d')
    for it in (d.get('data') or {}).get('diff') or []:
        def wan(v):
            try:
                return round(float(v) / 1e4, 0)
            except (TypeError, ValueError):
                return None
        daily = wan(it.get('f62'))
        f5 = wan(it.get('f164'))
        f10 = wan(it.get('f174'))
        if daily is None:
            continue
        out[str(it.get('f12'))] = {
            'dailyFlow': daily, 'flow5d': f5, 'flow10d': f10, 'flowDate': today,
        }
    return out


# ---- 行业 → 仪表盘赛道归类 ----
SECTOR_MAP_RULES = {
    '医药医疗': ['医药', '药', '医疗', '中药', '生物制品', '医疗器械', '医疗服务', '兽医'],
    '半导体电子': ['半导体', '电子', '光学光电子', '元件', '消费电子', '电子化学品'],
    '计算机软件': ['软件', '计算机', '互联网', 'IT服务', '游戏'],
    '通信': ['通信', '运营商'],
    '电力设备': ['电力设备', '电网', '配电', '电池', '光伏', '风电', '电源'],
    '机械设备': ['机械', '设备', '仪器', '自动化', '工程机械', '通用设备'],
    '汽车': ['汽车', '汽配', '零部件', '乘用车', '商用车'],
    '有色金属': ['有色', '金属', '铝', '铜', '锂', '小金属', '贵金属', '能源金属'],
    '钢铁煤炭': ['钢铁', '煤炭', '采掘'],
    '化工': ['化工', '化学制品', '化学原料', '化纤', '塑料', '橡胶'],
    '建筑装饰': ['建筑', '装饰', '基建', '工程咨询'],
    '交通运输': ['公路', '铁路', '航运', '港口', '物流', '机场', '高速'],
    '银行金融': ['银行', '保险', '证券', '多元金融', '信托'],
    '房地产': ['房地产', '地产', '园区开发'],
    '食品饮料': ['食品', '饮料', '白酒', '乳品', '调味', '啤酒'],
    '农林牧渔': ['农业', '牧渔', '种植', '养殖', '饲料', '动物保健'],
    '公用环保': ['电力', '水务', '环保', '燃气', '供热'],
    '国防军工': ['军工', '航空装备', '航天', '兵器', '舰船'],
    '家电轻工': ['家电', '白色家电', '小家电', '家居', '包装', '造纸'],
    '纺织服装': ['纺织', '服装', '饰品'],
    '传媒文旅': ['传媒', '影视', '出版', '广告', '旅游', '酒店', '餐饮'],
    '石油石化': ['石油', '石化', '油服'],
    '非银金属': ['非金属', '玻璃', '水泥', '陶瓷'],
}


def map_sector(industry):
    for sec, kws in SECTOR_MAP_RULES.items():
        if any(kw in industry for kw in kws):
            return sec
    return industry or '综合'


# 合格板块（今日强势板块）的赛道名集合
def load_qualified_sectors():
    try:
        d = json.load(open(SECTOR_FILTER_FILE))
        return set(d.get('qualified_sectors', {}).keys())
    except Exception:
        return set()


PHASE_TAG = {
    'on_deck': '⏳预启动', 'approaching': '🔍待启动',
    'building': '🛠️低位构筑', 'launching': '🚀启动段',
}


def build_reason(entry, quote, flow, sector, resonance):
    parts = []
    phase = entry.get('preLaunchPhase') or entry.get('phase') or ''
    tag = PHASE_TAG.get(phase, '🔄低位票')
    drop = entry.get('drop20d')
    if drop is not None and drop < -10:
        parts.append(f'20日回调{drop:.0f}%超跌')
    elif drop is not None and drop < -3:
        parts.append(f'20日回调{drop:.0f}%位置低')
    k30 = entry.get('kdj30')
    if k30 is not None and k30 < 45:
        parts.append(f'30分K={k30:.0f}低位无追高')
    if flow:
        f5 = flow['flow5d']
        if f5 > 3000:
            parts.append(f'5日主力净流入{f5:+.0f}万')
        elif f5 > 0:
            parts.append(f'5日主力小幅回流{f5:+.0f}万')
        else:
            parts.append(f'5日主力仍流出{f5:+.0f}万(待回流确认)')
    if quote and quote.get('roe') and quote['roe'] >= 10:
        parts.append(f'ROE{quote["roe"]:.0f}%基本面扎实')
    if quote and quote.get('pe') and 0 < quote['pe'] <= 15:
        parts.append(f'PE{quote["pe"]:.0f}低估')
    res_str = f'｜{sector}·{resonance}' if sector else ''
    return f'{tag}（{"，".join(parts) if parts else "综合优选"}）{res_str}'


def main():
    html = open(HTML).read()
    m = re.search(r'(const STOCKS\s*=\s*)(\[.*?\])(\s*;\s*\n)', html, re.S)
    if not m:
        print('❌ 未找到 STOCKS 数组'); return
    stocks = json.loads(m.group(2))
    qualified = load_qualified_sectors()
    print(f'仪表盘 {len(stocks)} 只票，今日强势板块: {qualified or "无"}')

    results = {}
    def worker(s):
        code = s['code']
        q = fetch_quote(code)
        return code, q

    with ThreadPoolExecutor(max_workers=10) as ex:
        futs = [ex.submit(worker, s) for s in stocks]
        for fu in as_completed(futs):
            code, q = fu.result()
            results[code] = (q, None)

    # 批量拉资金流
    all_flows = fetch_all_flows([s['code'] for s in stocks])
    for s in stocks:
        p = pure_code(s['code'])
        if p in all_flows:
            results[s['code']] = (results.get(s['code'], (None, None))[0], all_flows[p])

    ok_q = sum(1 for q, f in results.values() if q)
    ok_f = sum(1 for q, f in results.values() if f)
    print(f'估值/行业: {ok_q}/{len(stocks)} 成功 | 资金流: {ok_f}/{len(stocks)} 成功')

    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    for s in stocks:
        q, f = results.get(s['code'], (None, None))
        industry = q['industry'] if q else ''
        sector = map_sector(industry)
        if sector in qualified:
            tier, resonance = 1, '板块启动(共振)'
        else:
            # 独立走势：不在今日强势板块，靠自身资金/逻辑驱动
            tier = None
            resonance = '独立走势'
        s['sector'] = sector
        s['industry'] = industry
        if q:
            if q['pe']: s['pe'] = q['pe']
            if q['pb']: s['pb'] = q['pb']
            if q['roe'] is not None: s['roe'] = q['roe']
            if q['turnover'] is not None: s['turnover'] = q['turnover']
        if f:
            s['dailyFlow'] = f['dailyFlow']
            s['flow5d'] = f['flow5d']
            s['flow10d'] = f['flow10d']
            s['flowDate'] = f['flowDate']
        s['sectorTier'] = tier
        s['sectorResonance'] = resonance
        s['reason'] = build_reason(s, q, f, sector, resonance)
        if s.get('score') is not None:
            s['healthScore'] = s['score']

    new_arr = json.dumps(stocks, ensure_ascii=False, indent=2)
    html = html[:m.start(2)] + new_arr + html[m.end(2):]
    open(HTML, 'w').write(html)
    print(f'✅ 已注入 {HTML} ({now})')

    # 同步 dynamic_stocks.json
    try:
        dyn = json.load(open(DYN_FILE))
        by_code = {s['code']: s for s in stocks}
        for ds in dyn.get('stocks', []):
            full = by_code.get(ds['code'])
            if full:
                for k in ('sector', 'industry', 'pe', 'pb', 'roe', 'turnover',
                          'dailyFlow', 'flow5d', 'flow10d', 'flowDate',
                          'sectorTier', 'sectorResonance', 'reason'):
                    if k in full:
                        ds[k] = full[k]
        json.dump(dyn, open(DYN_FILE, 'w'), ensure_ascii=False, indent=2)
        print(f'✅ 已同步 {DYN_FILE}')
    except Exception as e:
        print(f'⚠️ dynamic_stocks.json 同步失败: {e}')

    # 摘要
    print('\n--- 补数摘要 ---')
    for s in stocks:
        q, f = results.get(s['code'], (None, None))
        print(f"{s['name']}({s['code']}): {s.get('sector','?')}/{s.get('industry','?')} "
              f"PE={s.get('pe','--')} PB={s.get('pb','--')} ROE={s.get('roe','--')} "
              f"当日={s.get('dailyFlow','--')}万 5日={s.get('flow5d','--')}万 "
              f"{'OK' if q and f else 'FAIL'}")


if __name__ == '__main__':
    main()
