#!/bin/bash

# 重启Python服务脚本

# 1. 杀死test2进程
pkill -9 -f "test2"

cd /home/ppg/

# 2. 检查并杀死可能残留的python your_script.py进程
pkill -f "python test2.py"

# 3. 等待确保进程已停止
sleep 2

# 4. 激活Python虚拟环境（添加这部分）
. /home/ppg/myenv/bin/activate


# 6. 重启Python服务
nohup python test2.py > output.log 2>&1 &

# 7. 记录重启日志
echo "$(date '+%Y-%m-%d %H:%M:%S') - 服务已重启（使用虚拟环境）" >> /home/ppg/log/python_service_restart.log