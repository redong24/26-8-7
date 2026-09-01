# 服务启停与定时重启（8801 / 5002 / 5003）

一键启停三个服务，外加每天早上 05:00 自动重启。

## 快速开始

```bash
bash /home/lsz/webapp/ops/install.sh     # 安装（幂等，可重复执行）
export PATH="$HOME/bin:$PATH"            # 让 svc 命令在当前终端生效（仅首次）
```

装完就能用：

```bash
svc status              # 看状态（端口 / PID / 健康 / 运行时长）
svc restart             # 一键重启全部
svc stop                # 一键全停
svc start               # 一键全起
svc restart audio       # 只重启某一个
svc restart audio main  # 重启多个
svc health              # 只做健康检查（返回码 0 = 全好，可接监控）
svc logs main 100       # 看日志最后 100 行
svc tail audio          # 实时跟踪日志
```

## ⚠️ 端口订正：是 5003，不是 5001

你提到的是「8801/5001/5002」，但核对线上实际监听后，**5001 上没有任何服务**，
音频服务实际跑在 **5003**：

- `audio_service.py` 里 `PORT = int(os.environ.get("AUDIO_SERVICE_PORT", "5003"))`
- 调用方 `audio_client.py` 也是 `base_url="http://127.0.0.1:5003"`

配置里按**实际值 5003** 固化了。如果写成 5001，停服会停不掉（端口对不上）、
健康检查会永远失败。若你确实另有一个 5001 的服务，告诉我，我加进配置即可。

三个服务的实际情况：

| 名字 | 端口 | 作用 | conda 环境 | 工作目录 |
|---|---|---|---|---|
| `main` | 8801 | rPPG + 心理评估主应用（HTTPS/gunicorn） | `rrpg_plus` | `real_time_plus/real_time_Demo` |
| `openface` | 5002 | OpenFace 3.0 面部 AU / 情绪 | `openface_linux` | `openface_service` |
| `audio` | 5003 | SenseVoice 语音情绪 / 声学特征 | `audio_linux` | `audio_service` |

## 🔴 还需要你做一步：启动 cron 守护进程

定时任务已经写进 crontab，但**本机 cron 守护进程没在运行**，所以 05:00 还不会真正触发。
当前用户没有免密 sudo，这一步我无法代做，需要你执行一次（要输密码）：

```bash
sudo service cron start
```

想让它以后随 WSL 自动起来（推荐，配合下面的登录钩子）：

```bash
echo "$USER ALL=(root) NOPASSWD: /usr/sbin/service cron start" \
  | sudo tee /etc/sudoers.d/psy-svc-cron
```

加了免密后，`ops/autostart.sh` 会在你登录时自动把 cron 拉起来，不用再手工执行。

执行完确认一下：

```bash
bash /home/lsz/webapp/ops/install.sh --status
```

看到「cron 守护进程在运行」就成了。

## 关于「自启动」在本机的实现方式

本机是 **WSL2，且 systemd 并未生效**（PID 1 是 `/init`，`systemctl` 报
"System has not been booted with systemd"）。所以：

- ❌ 用不了 `systemd service` / `timer`
- ✅ 定时重启用 **cron**（`CRON_TZ=Asia/Shanghai`，每天 `0 5 * * *`）
- ✅ 自启动用 **`~/.profile` 登录钩子**

WSL 实例是随 Windows 端按需启停的，没有传统意义的开机流程，登录 shell 是最可靠的
触发点。钩子（`ops/autostart.sh`）做两件事：

1. 尽力拉起 cron 守护进程（有免密权限时）
2. 发现服务没在跑就补起来

三条自我约束，保证它不成为负担：

- **节流**：6 小时内只跑一次，开十个终端也不会重复折腾
- **静默**：输出全进日志，不打扰终端，不拖慢 shell 启动
- **幂等**：只用 `start` 不用 `restart`，**绝不打断正在跑的服务**，
  也就不会把正在做检测的用户踢下线

## 每天 05:00 都做了什么

`ops/daily_restart.sh`：

1. 按顺序重启：停止是「主应用 → 微服务」，启动是「微服务 → 主应用」
   （主应用启动时微服务已就绪，能少一轮重试日志）
2. 失败自动补救：等 20s 复检 → 仍失败则补一次 `start` → 再复检
3. 顺手清理日志归档，每个目录只留最近 10 个

日志：

- `logs/daily_restart.log` — 每天定时重启的完整过程
- `logs/svc_actions.log` — 所有启停操作的审计记录（区分 `manual` / `cron-daily` / `autostart`）
- `logs/autostart.log` — 登录钩子的执行记录
- `logs/cron.log` — cron 自身的输出

## 改配置

改端口、换环境、加服务，**只改 `ops/services.conf`**，不用动脚本。
字段含义在文件头部有注释。加完直接 `svc status` 就能看到。

## 卸载

```bash
bash /home/lsz/webapp/ops/install.sh --remove
```

撤掉定时任务、登录钩子、快捷命令。**正在运行的服务不受影响。**

## 实现上避开的几个坑

1. **gunicorn 的 master/worker 陷阱**
   `ss` 报出的监听 PID 是 **worker**，不是 master。杀 worker，master 会立刻
   补一个新的，端口永不释放 → 新进程启动就撞 `Errno 98`；而「进程没了」这个
   判据会**误报成功**。脚本从监听 PID 沿 PPID 上溯找到真正的 master 再杀，
   并且**以端口是否释放为判据**，不以进程是否消失为判据。

2. **锁的文件描述符泄漏**（自测时真踩到了）
   脚本用 `flock` 做单实例互斥。子进程默认继承父进程所有 fd，包括那把锁。
   服务是常驻进程，一旦继承就等于**永久持锁**，之后每次 `svc` 调用都会在
   flock 上干等 300 秒。启动时用 `9>&-` 显式关闭才解决。

3. **就绪判据必须是 HTTP 真的应答**
   只看「端口有人监听」不够 —— 那可能是没死透的旧进程占着的。

4. **cron 的环境极度贫瘠**
   只有 `/usr/bin:/bin`，`ss`/`curl`/`flock` 可能都找不到。所以在
   `daily_restart.sh` 里显式补齐 `PATH` 和 `LANG`，避免「手工能跑、定时跑不了」。
   已用 `env -i` 模拟裸环境实测通过。

5. **日志膨胀**
   主应用的 `output.log` 曾涨到 **217GB**（归档 40 个共 2GB）。
   现在超过 200MB 自动归档，并且每天只保留最近 10 份。

## 验证记录（2026-08-21）

- `status` / `health` / 非法参数守卫 —— 通过
- `start` 幂等性（对健康服务不重复拉起，PID 不变）—— 通过
- 假服务的启动 / 停止 / 重启 / **崩溃诊断**（快速失败 + 打日志尾部）—— 通过
- 锁 fd 不再泄漏（`/proc/<pid>/fd` 中计数为 0）—— 通过
- autostart 节流 —— 通过
- install 幂等（重复安装后 crontab / 钩子 / PATH 各只有 1 条）—— 通过
- **真实服务重启**：audio（900MB 模型重载上 RTX 5090，16s）—— 通过
- **真实服务重启**：main（正确杀 master、无孤儿 worker、HTTP 200/7ms、217GB 日志归档）—— 通过
- **完整 05:00 任务在 `env -i` 裸环境下全程演练**：三服务 21s 内全部就绪 —— 通过
