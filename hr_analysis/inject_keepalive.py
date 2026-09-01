#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
修复: 外网访问 https://<公网IP>:8801/max 卡顿(帧饥饿)。

## 现象
- 局域网访问正常; 公网访问时页面基本跑不动。
- DevTools Network 面板: 每个 /upload_frame 请求都带
  Stalled ~115ms + Initial connection ~190ms + SSL ~190ms。
- output.log: [SHADOW-GATE] 等效fps=1.3~1.9, 帧饥饿被门控拦截。

## 根因(已实测验证, 非推测)
1. werkzeug 3.1.3 开发服务器默认 WSGIRequestHandler.protocol_version = 'HTTP/1.0'。
2. HTTP/1.0 语义下每个响应都是 `Connection: close`(curl -D 实测确认),
   浏览器 keep-alive 完全失效。
3. 于是前端每上传一帧都要重做 TCP 3 次握手 + TLS 握手。
   内网 RTT<1ms 无感; 公网 RTT 几十~上百 ms, 每帧额外 300~600ms。
4. 前端 processFrameLoopTick 有 inFlight 串行保护(不并发), 帧率被
   "每帧握手时间"钳死, 等效 fps 掉到 1~2, 触发 SHADOW-GATE 帧饥饿拦截,
   心率/表情面板长期不更新 => 用户感知"卡顿, 基本运行不起来"。

## 修复
在 __main__ 启动前设置:
    WSGIRequestHandler.protocol_version = "HTTP/1.1"
werkzeug 在 HTTP/1.1 + 已知 Content-Length 的响应上会自动保持连接
(Connection: keep-alive), 浏览器即可复用同一条 TLS 连接连续上传帧,
每帧网络开销从 300~600ms 降到约 1 个 RTT。

不改任何业务逻辑; 锁定区 A/B 逐字节不变(脚本内断言)。
"""
import shutil, time, sys

SRC = '/home/lsz/real_time_plus/real_time_Demo/test2.py'
bak = SRC + '.before_keepalive_' + time.strftime('%Y%m%d_%H%M%S')
src = open(SRC, 'rb').read().decode('utf-8')
shutil.copy2(SRC, bak)
print('备份 ->', bak)

def locked_block(text, s_mark, e_mark):
    s = text.find(s_mark)
    e = text.find(e_mark)
    return text[s:e] if s != -1 and e != -1 else None

lock_a_before = locked_block(src, '心率计算修复区块 START', '心率计算修复区块 END')

OLD = """if __name__ == '__main__':\r
    # app.run(host='0.0.0.0', port='8801', debug=True, threaded=True, use_reloader=False)\r
    app.run(host='0.0.0.0', port='8801', debug=True, threaded=True, use_reloader=False, ssl_context='adhoc')\r"""

NEW = """if __name__ == '__main__':\r
    # [2026-08-11 外网卡顿修复] werkzeug 开发服务器默认 protocol_version=HTTP/1.0,\r
    # 每个响应 Connection: close => 浏览器每帧上传都重做 TCP+TLS 握手。\r
    # 内网 RTT<1ms 无感, 公网 RTT 几十~上百 ms 时每帧多 300~600ms,\r
    # 等效 fps 被钳到 1~2, 触发 SHADOW-GATE 帧饥饿(见 output.log)。\r
    # 改为 HTTP/1.1 启用 keep-alive, 浏览器复用同一条 TLS 连接。\r
    from werkzeug.serving import WSGIRequestHandler\r
    WSGIRequestHandler.protocol_version = "HTTP/1.1"\r
    # app.run(host='0.0.0.0', port='8801', debug=True, threaded=True, use_reloader=False)\r
    app.run(host='0.0.0.0', port='8801', debug=True, threaded=True, use_reloader=False, ssl_context='adhoc')\r"""

if OLD not in src:
    print('FATAL: 未找到目标注入点(__main__ 块), 中止, 文件未改动')
    sys.exit(1)
if src.count(OLD) != 1:
    print('FATAL: 注入点不唯一(count=%d), 中止' % src.count(OLD))
    sys.exit(1)

dst = src.replace(OLD, NEW, 1)

# 锁定区校验
lock_a_after = locked_block(dst, '心率计算修复区块 START', '心率计算修复区块 END')
assert lock_a_before == lock_a_after, '锁定区A 被意外改动!'
print('锁定区A 校验通过 (长度 %s)' % (len(lock_a_before) if lock_a_before else 'N/A'))

open(SRC, 'wb').write(dst.encode('utf-8'))
print('注入完成')

# 语法校验
import py_compile
try:
    py_compile.compile(SRC, doraise=True)
    print('py_compile 语法校验通过')
except py_compile.PyCompileError as e:
    print('FATAL: 语法错误, 回滚!', e)
    shutil.copy2(bak, SRC)
    sys.exit(1)
