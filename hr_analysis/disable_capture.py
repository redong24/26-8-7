import shutil, time
SRC='/home/lsz/real_time_plus/real_time_Demo/test2.py'
bak=SRC+'.before_capoff_'+time.strftime('%Y%m%d_%H%M%S')
shutil.copy2(SRC,bak); print('备份:',bak)

data=open(SRC,'rb').read()
before_cr=data.count(b'\r'); before_len=len(data)

MARKS=[('A','# 🔒 [已锁定-生产稳定] 2026-08-08 心率计算修复区块 START',
            '# 🔒 [已锁定-生产稳定] 2026-08-08 心率计算修复区块 END (compute_hr_with_tracking)'),
       ('B','# 🔒 [已锁定-生产稳定] 心率生产计算区块 START',
            '# 🔒 [已锁定-生产稳定] 心率生产计算区块 END')]
def block(d,s,e):
    i=d.find(s.encode()); assert i>=0,'START未找到: '+s
    j=d.find(e.encode(),i); assert j>i,'END未找到: '+e
    return d[i:j]
base={k:block(data,s,e) for k,s,e in MARKS}
for k in base: print('锁定区%s 基线长度 = %d 字节'%(k,len(base[k])))

for old,new in [(b'FRAME_CAPTURE_ENABLED = True',  b'FRAME_CAPTURE_ENABLED = False'),
                (b'SHADOW_CAPTURE_ENABLED = True', b'SHADOW_CAPTURE_ENABLED = False')]:
    n=data.count(old); assert n==1,'期望唯一匹配, 实际%d处: %s'%(n,old)
    data=data.replace(old,new); print('已改:',old.decode(),'->',new.decode())

assert data.count(b'\r')==before_cr, 'CR数量变化, 行尾被破坏!'
assert len(data)==before_len+2, '长度变化异常: %d'%(len(data)-before_len)
for k,s,e in MARKS:
    assert block(data,s,e)==base[k], '锁定区%s被改动!'%k
open(SRC,'wb').write(data)
print('OK: CR保持%d, 长度+2(True->False x2), 两个锁定区字节级一致'%before_cr)
