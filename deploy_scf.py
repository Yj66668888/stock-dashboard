# -*- coding: utf-8 -*-
"""腾讯云函数全自动部署：创建 kdj-watch-trigger + 定时触发器 + 测试调用
用法: python3 deploy_scf.py <SecretId> <SecretKey>
零第三方依赖（纯标准库实现 TC3-HMAC-SHA256 签名）
"""
import sys, os, json, time, hashlib, hmac, zipfile, io, base64, urllib.request

SECRET_ID, SECRET_KEY = sys.argv[1], sys.argv[2]
REGION = 'ap-guangzhou'
SERVICE = 'scf'
HOST = 'scf.tencentcloudapi.com'
TOKEN = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.github_token')).read().strip()

FUNC_CODE = '''# -*- coding: utf-8 -*-
# 腾讯云定时器 → GitHub Actions 三路调度器（10/14 升级：GitHub 自带 schedule 连续哑火）
# 每5分钟一个tick，按北京时间分发：
#   - 盘中每tick(9:00-15:59): cloud-index-kdj-watch（大盘30分KDJ回升监控+个股推送）
#   - 8:35 tick: cloud-full-scan（早间全量选票+注入+推送）
#   - 10:10/11:10/13:10/14:10/14:45 tick: cloud-dashboard-update（盘中5档数据刷新+推送）
#   推送窗口由 wechat_notify._PUSH_TIMES 控制（10:00/11:00/13:00/14:00/14:40），晚几分钟仍在窗内
import json, os, urllib.request
from datetime import datetime, timedelta


def _dispatch(token, wf):
    url = ('https://api.github.com/repos/Yj66668888/stock-dashboard'
           '/actions/workflows/%s/dispatches' % wf)
    req = urllib.request.Request(
        url, method='POST',
        data=json.dumps({'ref': 'main'}).encode(),
        headers={
            'Authorization': 'Bearer ' + token,
            'Accept': 'application/vnd.github+json',
            'User-Agent': 'scf-trigger',
            'Content-Type': 'application/json',
        })
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status
    except Exception as e:
        return str(e)[:120]


def main_handler(event, context):
    token = os.environ.get('GH_TOKEN', '')
    now = datetime.utcnow() + timedelta(hours=8)  # 北京时间（SCF运行环境为UTC）
    h, m = now.hour, now.minute
    results = {}
    # 1) 大盘KDJ监控：交易日盘中每tick（workflow自身有交易时段/去重闸门）
    if 9 <= h <= 15:
        results['kdj-watch'] = _dispatch(token, 'cloud-index-kdj-watch.yml')
    # 2) 早间全量扫描：8:35 tick（窗口8:33-8:39只含一个tick，防重复）
    if h == 8 and 33 <= m <= 39:
        results['full-scan'] = _dispatch(token, 'cloud-full-scan.yml')
    # 3) 仪表盘更新5档：10:10/11:10/13:10/14:10/14:45 tick
    if (h in (10, 11, 13, 14) and 7 <= m <= 13) or (h == 14 and 43 <= m <= 49):
        results['dashboard-update'] = _dispatch(token, 'cloud-dashboard-update.yml')
    ok = all(isinstance(v, int) and v < 300 for v in results.values()) if results else True
    return {'ok': ok, 'time': now.strftime('%m-%d %H:%M'), 'dispatched': results}
'''

# 2026-10-14 扩窗 9-15 → 8-15：覆盖 8:35 全量扫描 tick（秒 分 时 日 月 周 年，UTC+8）
CRON = '0 */5 8-15 * * 1-5 *'


def sign(action, payload):
    body = json.dumps(payload, separators=(',', ':'), ensure_ascii=False).encode()
    unix_ts = int(time.time())
    ts = time.strftime('%Y-%m-%dT%H:%M:%S+08:00')
    date = time.strftime('%Y-%m-%d', time.gmtime(unix_ts))  # UTC 日期
    ct = hashlib.sha256(body).hexdigest()
    canonical = (f'POST\n/\n\ncontent-type:application/json\nhost:{HOST}\n'
                 f'x-tc-action:{action.lower()}\n\ncontent-type;host;x-tc-action\n{ct}')
    credential_scope = f'{date}/{SERVICE}/tc3_request'
    # ★ 腾讯 TC3 规范：StringToSign 第二行是 Unix 时间戳（不是 ISO 格式）
    string_to_sign = (f'TC3-HMAC-SHA256\n{unix_ts}\n{credential_scope}\n'
                      + hashlib.sha256(canonical.encode()).hexdigest())
    def h(key, msg): return hmac.new(key, msg.encode(), hashlib.sha256).digest()
    secret = h(('TC3' + SECRET_KEY).encode(), date)
    secret = h(secret, SERVICE)
    secret = h(secret, 'tc3_request')
    sig = hmac.new(secret, string_to_sign.encode(), hashlib.sha256).hexdigest()
    auth = (f'TC3-HMAC-SHA256 Credential={SECRET_ID}/{credential_scope}, '
            f'SignedHeaders=content-type;host;x-tc-action, Signature={sig}')
    req = urllib.request.Request(f'https://{HOST}/', data=body, method='POST')
    for k, v in [('Authorization', auth), ('Content-Type', 'application/json'),
                 ('Host', HOST), ('X-TC-Action', action), ('X-TC-Timestamp', str(unix_ts)),
                 ('X-TC-Version', '2018-04-16'), ('X-TC-Region', REGION)]:
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def main():
    # 1) 打包 zip（内存中）
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('index.py', FUNC_CODE)
    zip_b64 = base64.b64encode(buf.getvalue()).decode()

    # 2) 若已存在同名函数先删除（幂等重跑）
    try:
        r = sign('DeleteFunction', {'FunctionName': 'kdj-watch-trigger'})
        print('删除旧函数:', r.get('Response', {}).get('Error', {}).get('Message', '成功/不存在'))
    except urllib.error.HTTPError as e:
        print('删除旧函数（可忽略）:', e.read().decode()[:120])
    time.sleep(2)

    # 3) 创建函数
    try:
        r = sign('CreateFunction', {
            'FunctionName': 'kdj-watch-trigger',
            'Type': 'Event',
            'Runtime': 'Python3.9',
            'Code': {'ZipFile': zip_b64},
            'Handler': 'index.main_handler',
            'Description': '每5分钟三路调度GitHub：KDJ监控+8:35全量扫描+5档仪表盘更新',
            'MemorySize': 128,
            'Timeout': 30,
            'Environment': {'Variables': [
                {'Key': 'GH_TOKEN', 'Value': TOKEN},
            ]},
        })
        resp = r.get('Response', {})
        if 'Error' in resp:
            print('❌ 创建失败:', json.dumps(resp['Error'], ensure_ascii=False)); return 1
        print('✅ 函数创建成功')
    except urllib.error.HTTPError as e:
        print('❌ 创建失败 HTTP', e.code, ':', e.read().decode()[:300]); return 1
    time.sleep(3)

    # 4) 创建定时触发器
    try:
        r = sign('CreateTrigger', {
            'FunctionName': 'kdj-watch-trigger',
            'TriggerName': 'every5min-trading-hours',
            'Type': 'timer',
            'TriggerDesc': CRON,
            'Enable': 'OPEN',
            'CustomArgument': '',
        })
        resp = r.get('Response', {})
        if 'Error' in resp:
            print('❌ 触发器失败:', json.dumps(resp['Error'], ensure_ascii=False)); return 1
        print('✅ 定时触发器创建成功:', CRON)
    except urllib.error.HTTPError as e:
        print('❌ 触发器失败 HTTP', e.code, ':', e.read().decode()[:300]); return 1
    time.sleep(2)

    # 5) 测试调用（应返回 ok:true http:204）
    try:
        r = sign('Invoke', {'FunctionName': 'kdj-watch-trigger',
                            'InvocationType': 'RequestResponse'})
        resp = r.get('Response', {})
        if 'Error' in resp:
            print('❌ 测试失败:', json.dumps(resp['Error'], ensure_ascii=False)); return 1
        print('✅ 测试调用返回:', resp.get('RetMsg'))
        print('\n全部完成！GitHub 侧我将验证运行记录。')
        return 0
    except urllib.error.HTTPError as e:
        print('❌ 测试失败 HTTP', e.code, ':', e.read().decode()[:300]); return 1


if __name__ == '__main__':
    sys.exit(main())
