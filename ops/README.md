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

---

# boot_all —— 全站服务统一拉起（2026-09-03 新增）

## 解决什么问题

`svc.sh` 只管 real_time 的三个服务（8801/5002/5003）。但这台机器上实际还跑着：

- **nginx** 入口：80 / 443 / 8080 / 8443 / 9443
- **asthmaguard**：8000 产品后端、8010 算法服务
- **SPT**：8850 风团测量

WSL 实例重启后，除 Docker 容器（`unless-stopped` 会自愈）外，**上面这些全是停的**，
每次都要人工逐个敲命令拉起。2026-09-03 排查时现场就是这个状态：
只有 4 个容器在跑，nginx 和全部应用层进程都没起来。

`boot_all.sh` 把那套手工流程固化成一条命令。

## 用法

```bash
ops/boot_all.sh              # 拉起所有未在跑的服务（幂等）
ops/boot_all.sh status       # 只看状态，不做任何改动
ops/boot_all.sh --only spt   # 只处理指定服务，可重复
```

服务名：`docker` / `nginx` / `realtime` / `algo` / `backend` / `spt`

## 自启链路

三层兜底，任何一层单独失效都不至于让服务长时间躺平：

| 触发点 | 时机 | 说明 |
|---|---|---|
| `@reboot`（cron） | WSL 实例重启 | `sleep 30` 等 Docker daemon 和网络就绪 |
| `*/10 * * * *`（cron） | 每 10 分钟 | 自愈巡检，健康时不到 1 秒且不碰任何进程 |
| `~/.profile` 钩子 | 交互式登录 | 兜底，带 6 小时节流 |

**cron 自身也需要被拉起** —— WSL 下 cron 默认不随实例启动（本机 systemd 未生效，
PID1 是 `init`）。`autostart.sh` 里检查 cron 的那段**刻意放在节流判断之前**：
cron 是整条自愈链的根，它没起来则 `@reboot` 和 `*/10` 全部失效；
若被 6 小时节流拦住，实例重启后会有整整 6 小时无人值守。

## 踩坑记录

### 1) 锁的 fd 泄漏 —— 同一个坑摔了第二次

本文档上面第 2 条已经写过这个坑（`svc.sh` 用 `9>&-` 解决），
但写 `boot_all.sh` 时仍然漏了一处，卡死 300 秒才发现。

`boot_all.sh` 用 fd 8 做单实例锁。直接 `setsid` 启动的地方我都记得加 `8>&-`，
**唯独调用外部脚本 `SPT/svc.sh` 时漏了** —— 因为那是间接调用：
SPT 的 `svc.sh` 内部用 `nohup python app.py &` 起常驻进程，
锁就顺着继承链传给了那个 python，被它永久持有。
后果是此后每次 `boot_all` 都在 flock 上干等，watchdog 每 10 分钟堆积一个僵死进程。

诊断方法（值得记住）：

```bash
sudo fuser -v logs/.boot_all.lock   # 直接列出谁在持锁
ls -l /proc/<pid>/fd/8              # 确认某进程是否扣着锁
```

**教训**：不只是自己 `setsid` 的地方要关 fd，
**任何会 fork 出常驻进程的外部脚本调用都要关**，哪怕隔了两层。

顺带把 `flock -w 600` 改成 `flock -n`：上一轮没跑完，要么正在启动服务
（这一轮本来也没事可做），要么已经卡死（干等只会堆更多进程），两种情况都该立刻退出。

### 2) `/etc/wsl.conf` 首行有个游离的 `X`

文件第一行是孤零零一个 `X`（`0x58 0x0a`），这在 INI 语法里非法，
导致**整个文件解析失败**，`[boot] systemd=true` 从未生效 ——
这才是「本机 systemd 未生效」的真正原因，此前一直被当作 WSL 的固有限制绕开。

已删除该字符并用 `configparser` 校验通过。但**要等 `wsl --shutdown` 完整重启才会生效**，
那会中断所有服务，所以没有立即执行。自启方案也**刻意不依赖 systemd**，
仍走 cron + 登录钩子，即使将来 systemd 起来了也不冲突。

### 3) `ALGO_SERVICE_PORT` 指向了错误的端口

`.env` 和 `configs/config.py:69` 的默认值都写着 `8000`，
但算法服务实际监听 **8010**（`start_algo.sh` 硬编码），8000 是产品后端。
当前 nginx 直接代理到 8010，所以线上没暴露问题，
但任何读 `ALGO_SERVICE` 这个配置的代码都会把算法请求打到后端上，
报错信息还与算法无关，非常难定位。三处（`.env` / `.env.example` / 代码默认值）已一并改为 8010。

### 4) `.env` 里的 MySQL 端口容易误读

宿主机上**没有 3306**。产品库是 **3308**、科研库是 **3307**（容器端口映射），
容器内部才是 3306。已在 `.env` 加注说明。

## 验证记录（2026-09-03）

- `status` 子命令（只读，不改状态）—— 通过
- 幂等性：连跑 3 轮，每轮 0 秒 / 7 项跳过，**6 个服务 PID 全程未变** —— 通过
- 故障恢复：停掉 SPT 后自动补起，且校验 `sam_loaded=true` —— 通过
- 锁 fd 泄漏修复：`fuser` 查锁文件持有者为空 —— 通过
- `autostart.sh --force` 全链路 0.7 秒完成（修复前 300 秒超时）—— 通过
- cron watchdog 真实触发（10:20:01，`SVC_INVOKER=cron-watchdog`）—— 通过
- 全部 13 个端口 HTTP 健康检查 —— 通过
