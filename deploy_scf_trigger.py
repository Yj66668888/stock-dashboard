# -*- coding: utf-8 -*-
"""补挂触发器+测试调用（函数已存在时用这个）"""
import time, json, sys

src = open('deploy_scf.py').read().split("def main():")[0]
g = {'__file__': __file__}
exec(src.replace("SECRET_ID, SECRET_KEY = sys.argv[1], sys.argv[2]",
                 "SECRET_ID, SECRET_KEY = %r, %r" % (sys.argv[1], sys.argv[2])), g)
sign = g['sign']

# 1) 查函数状态
r = sign('GetFunction', {'FunctionName': 'kdj-watch-trigger'})
resp = r.get('Response', {})
if 'Error' in resp:
    print('❌ 函数不存在:', resp['Error']['Message'])
    sys.exit(1)
print(f"✅ 函数就绪: 状态={resp.get('Status')} 运行时={resp.get('Runtime')} "
      f"内存={resp.get('MemorySize')}MB 超时={resp.get('Timeout')}s")

# 2) 若触发器已存在先删（幂等）
sign('DeleteTrigger', {'FunctionName': 'kdj-watch-trigger',
                       'TriggerName': 'every5min-trading-hours', 'Type': 'timer'})
time.sleep(1)

# 3) 挂定时触发器（cron 从 deploy_scf.py 统一读取，避免两处不一致）
r = sign('CreateTrigger', {
    'FunctionName': 'kdj-watch-trigger',
    'TriggerName': 'every5min-trading-hours',
    'Type': 'timer',
    'TriggerDesc': g['CRON'],
    'Enable': 'OPEN',
})
resp = r.get('Response', {})
if 'Error' in resp:
    print('❌ 触发器失败:', json.dumps(resp['Error'], ensure_ascii=False)[:200])
    sys.exit(1)
ti = resp.get('TriggerInfo', {})
print(f"✅ 定时触发器创建成功: {ti.get('TriggerDesc')} 启用={ti.get('Enable')}")

# 4) 测试调用
time.sleep(2)
r = sign('Invoke', {'FunctionName': 'kdj-watch-trigger', 'InvocationType': 'RequestResponse'})
resp = r.get('Response', {})
if 'Error' in resp:
    print('❌ 测试失败:', json.dumps(resp['Error'], ensure_ascii=False)[:200])
    sys.exit(1)
print('✅ 测试调用返回:', resp.get('RetMsg'))
