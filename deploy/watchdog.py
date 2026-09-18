"""QQ群管机器人 看门狗（watchdog）

职责：
  1. 进程守护：NapCat（协议端）与 NoneBot（机器人服务）掉线/崩溃时自动拉起
  2. 登录失效自动重登：检测到「账号登录已失效」时自动重启协议端快速登录
  3. 需要扫码时主动提醒：把最新二维码复制到项目根目录（需要扫码登录.png），
     并写提示文件；二维码有效期内只等待不重启，避免把正在扫的码弄失效
  4. 睡眠/休眠唤醒自愈：发现巡检间隔异常拉长（系统刚唤醒）时，
     立刻做一次完整体检——登录失效就立即重登，协议端在线就重启机器人服务刷新本地连接
  5. 大量人工干预都不需要：日常只需双击『启动机器人.bat』，它会一直守护

用法：
  python deploy/watchdog.py            # 常驻守护（推荐，启动机器人.bat 调用它）
  python deploy/watchdog.py --relogin  # 立即重启协议端并尝试重新登录
  python deploy/watchdog.py --status   # 打印当前状态后退出
"""
import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

IS_WIN = os.name == "nt"

BASE = Path(__file__).resolve().parent.parent
LOGS = BASE / "logs"
LOGS.mkdir(exist_ok=True)

CREATE_NO_WINDOW = 0x08000000 if IS_WIN else 0
TICK = 15                   # 巡检间隔（秒）
WAKE_GAP = 60               # 巡检间隔超过它 → 判定系统刚从睡眠/休眠中唤醒（秒）
FAIL_BEFORE_RELOGIN = 120   # 失效持续多久后触发重登（秒）
RELOGIN_COOLDOWN = 180      # 两次重登尝试的最小间隔（秒）
SLOW_RETRY = 900            # 连续失败后的慢速重试间隔（秒）
QR_FRESH = 300              # 二维码"新鲜期"：期间只等待扫码，不重启进程

OK_PATTERNS = ("已通知主进程登录成功", "Worker进程已登录成功", "切换到正常重试策略")
FAIL_PATTERNS = ("[Login] Login Error", "登录已失效", "Login failed")
QR_PATTERNS = ("请扫描下面的二维码", "二维码已保存到")

ALERT_IMG = BASE / "需要扫码登录.png"
ALERT_TXT = BASE / "需要扫码登录.txt"

state = {
    "logged_in": False,
    "fail_since": None,
    "last_attempt": 0.0,
    "last_qr": 0.0,
    "attempts": 0,
    "alerted": False,
    "log_pos": 0,
}


def log(msg: str) -> None:
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    try:
        with open(LOGS / "watchdog.log", "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def load_cfg() -> dict:
    p = BASE / "部署配置.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            log(f"部署配置.json 解析失败，使用默认值：{e}")
    return {}


def napcat_paths(cfg: dict):
    napcat_root = Path(cfg.get("napcat_dir") or (Path.home() / "NapCat"))
    napcat_dir = napcat_root / "NapCat"
    return napcat_dir, napcat_dir / "node.exe", napcat_dir / "napcat" / "cache" / "qrcode.png"


def project_dir(cfg: dict) -> Path:
    p = (cfg.get("project_dir") or "").strip()
    return Path(p) if p else BASE / "qq-group-bot"


def port_in_use(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", int(port))) == 0


def napcat_pids() -> list:
    """列出系统中正在运行的 NapCat 进程 PID。"""
    if not IS_WIN:
        try:
            out = subprocess.run(
                ["pgrep", "-f", "napcat"],
                capture_output=True, text=True, timeout=10,
            ).stdout
            return [int(x) for x in out.split() if x.strip().isdigit()]
        except Exception:
            return []

    cmd = (
        "Get-CimInstance Win32_Process -Filter \"Name='node.exe'\" | "
        "Where-Object { $_.CommandLine -like '*index.js*' -and "
        "$_.ExecutablePath -like '*NapCat*' } | "
        "Select-Object -ExpandProperty ProcessId"
    )
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", cmd],
            capture_output=True, text=True, timeout=30,
        ).stdout
        return [int(x) for x in out.split() if x.strip().isdigit()]
    except Exception:
        return []


def kill_napcat_processes() -> None:
    pids = napcat_pids()
    for pid in pids:
        try:
            if IS_WIN:
                subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                               capture_output=True, timeout=15)
            else:
                os.kill(pid, 9)
            log(f"已结束 NapCat 进程 PID {pid}")
        except Exception:
            pass


class Proc:
    """被守护的子进程。"""

    def __init__(self, name: str, logfile: str):
        self.name = name
        self.logfile = LOGS / logfile
        self.popen = None

    def alive(self) -> bool:
        return self.popen is not None and self.popen.poll() is None

    def start(self, args, cwd) -> None:
        try:
            f = open(self.logfile, "a", encoding="utf-8", errors="replace")
            f.write(f"\n===== {datetime.now():%Y-%m-%d %H:%M:%S} 启动 {self.name} =====\n")
            f.flush()
            self.popen = subprocess.Popen(
                args, cwd=str(cwd), stdout=f, stderr=subprocess.STDOUT,
                creationflags=CREATE_NO_WINDOW,
            )
            log(f"已启动 {self.name}（PID {self.popen.pid}）")
        except FileNotFoundError as e:
            log(f"启动 {self.name} 失败，找不到可执行文件：{e}")
        except Exception as e:
            log(f"启动 {self.name} 失败：{e}")

    def stop(self) -> None:
        if self.alive():
            try:
                self.popen.terminate()
                self.popen.wait(timeout=10)
            except Exception:
                try:
                    self.popen.kill()
                except Exception:
                    pass
            log(f"已停止 {self.name}")
        self.popen = None


napcat = Proc("NapCat协议端", "napcat.log")
bot = Proc("机器人服务", "bot.log")


def start_napcat(cfg: dict) -> None:
    napcat_dir, exe, _ = napcat_paths(cfg)
    qq = str(cfg.get("bot_qq") or "").strip()

    if not IS_WIN:
        # Linux 下 NapCat 用自带的启动脚本（首次仍需扫码；-q 快速登录参数按版本可能不同，
        # 若你的 NapCat 支持，可自行在这里补 ["-q", qq]）
        sh = napcat_dir / "napcat.sh"
        if not sh.exists():
            log(f"找不到 {sh}，请检查 部署配置.json 里的 napcat_dir")
            return
        napcat.start(["bash", str(sh)], napcat_dir)
        return

    if not exe.exists():
        log(f"找不到 {exe}，请先运行『一键部署.bat』")
        return
    args = [str(exe), "index.js"]
    if qq:
        args += ["-q", qq]
    napcat.start(args, napcat_dir)


def start_bot(cfg: dict) -> None:
    pdir = project_dir(cfg)
    if not (pdir / "bot.py").exists():
        log(f"找不到 {pdir / 'bot.py'}，跳过机器人服务启动")
        return
    bot.start([sys.executable, "bot.py"], pdir)


# ---------------- 登录状态检测 ----------------
def check_login(cfg: dict):
    """通过 NapCat 的 HTTP 接口查询登录状态。

    返回 True=已登录，False=未登录/登录失效，None=无法判断（接口不可用）。
    """
    port = int(cfg.get("napcat_http_port") or 3000)
    token = str(cfg.get("napcat_http_token") or "napcat-http-token")
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/get_login_info",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        return bool(data.get("retcode") == 0 and data.get("data", {}).get("user_id"))
    except urllib.error.HTTPError:
        return None  # 401/404：接口未开启或 token 不对，交给日志判断
    except Exception:
        return None  # 进程还没起来/端口未开


def scan_log() -> None:
    """增量扫描 napcat.log：识别登录成功、失效与二维码生成。"""
    path = LOGS / "napcat.log"
    if not path.exists():
        return
    pos = state.get("log_pos", 0)
    size = path.stat().st_size
    if size < pos:  # 日志被截断，从头读
        pos = 0
    if size == pos:
        return
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        f.seek(pos)
        chunk = f.read()
        state["log_pos"] = f.tell()

    for line in chunk.splitlines():
        if any(p in line for p in OK_PATTERNS):
            if not state["logged_in"]:
                log("检测到协议端登录成功 ✅")
            state.update(logged_in=True, fail_since=None, attempts=0,
                         last_attempt=0.0, alerted=False)
            clear_alert()
        elif any(p in line for p in FAIL_PATTERNS):
            if state["logged_in"]:
                log("检测到登录失效（账号被下线），准备自动重新登录…")
            state["logged_in"] = False
            if state["fail_since"] is None:
                state["fail_since"] = time.time()
        if any(p in line for p in QR_PATTERNS):
            state["last_qr"] = time.time()


def refresh_login_state(cfg: dict) -> None:
    """组合检测：HTTP 接口优先，日志兜底。"""
    scan_log()
    http_state = check_login(cfg)
    if http_state is True:
        if not state["logged_in"]:
            log("HTTP 检测：协议端已登录 ✅")
        state.update(logged_in=True, fail_since=None, attempts=0,
                     last_attempt=0.0, alerted=False)
        clear_alert()
    elif http_state is False:
        if state["logged_in"]:
            log("HTTP 检测：登录已失效")
        state["logged_in"] = False
        if state["fail_since"] is None:
            state["fail_since"] = time.time()


# ---------------- 扫码提醒 ----------------
def clear_alert() -> None:
    for p in (ALERT_IMG, ALERT_TXT):
        if p.exists():
            try:
                p.unlink()
            except Exception:
                pass


def sync_alert(cfg: dict) -> bool:
    """把最新二维码同步到项目根目录并写提示文件。返回是否已完成同步。"""
    _, _, qr = napcat_paths(cfg)
    if not qr.exists():
        return False
    try:
        if not ALERT_IMG.exists() or ALERT_IMG.stat().st_mtime < qr.stat().st_mtime:
            shutil.copy(qr, ALERT_IMG)
            ALERT_TXT.write_text(
                "【QQ群管机器人】需要人工扫码登录\n"
                f"更新时间：{datetime.now():%Y-%m-%d %H:%M:%S}\n\n"
                "处理办法：\n"
                "  1. 用手机 QQ 扫描同目录下的『需要扫码登录.png』\n"
                "  2. 选择机器人小号（见 部署配置.json 的 bot_qq）授权登录\n"
                "  3. 登录成功后这两个文件会自动消失\n\n"
                "说明：二维码约 2 分钟过期，本文件会自动更新为最新二维码，\n"
                "      请以最新图片为准；也可打开 NapCat 面板 http://127.0.0.1:6099/webui\n",
                encoding="utf-8",
            )
            log(f"已更新待扫码二维码 → {ALERT_IMG}")
        return True
    except Exception as e:
        log(f"同步二维码失败：{e}")
        return False


def relogin(cfg: dict, reason: str) -> None:
    state["attempts"] += 1
    state["last_attempt"] = time.time()
    log(f"执行第 {state['attempts']} 次自动重登（{reason}）")
    napcat.stop()
    if napcat_pids():  # 清理外部残留进程，避免多开冲突
        kill_napcat_processes()
        time.sleep(2)
    time.sleep(2)
    start_napcat(cfg)
    state["fail_since"] = time.time()
    state["external_note"] = False


def handle_not_logged_in(cfg: dict) -> None:
    """未登录时的处理策略：二维码新鲜→只提醒等待；否则→尝试重启重登。"""
    now = time.time()
    qr_fresh = state["last_qr"] and (now - state["last_qr"] < QR_FRESH)

    if qr_fresh:
        # 二维码还有效：刷新提醒文件，安静等待用户扫码
        if sync_alert(cfg) and not state["alerted"]:
            state["alerted"] = True
            log("等待人工扫码：二维码已放到项目根目录『需要扫码登录.png』")
            print("\a", end="", flush=True)
        return

    since_attempt = now - state["last_attempt"]
    waited = now - (state["fail_since"] or now)
    if state["attempts"] >= 3:
        if since_attempt >= SLOW_RETRY:
            relogin(cfg, "慢速重试")
    elif waited >= FAIL_BEFORE_RELOGIN and since_attempt >= RELOGIN_COOLDOWN:
        relogin(cfg, "登录失效自动重登")


# ---------------- 睡眠/休眠唤醒自愈 ----------------
def check_online(cfg: dict):
    """查询协议端自身是否在线（/get_status 的 online 字段）。

    返回 True=在线，False=离线，None=接口不可用（进程没起来/端口未开）。
    """
    port = int(cfg.get("napcat_http_port") or 3000)
    token = str(cfg.get("napcat_http_token") or "napcat-http-token")
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/get_status",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        if data.get("retcode") != 0:
            return None
        return bool(data.get("data", {}).get("online"))
    except Exception:
        return None


def restart_bot(cfg: dict) -> None:
    """重启机器人服务（NoneBot），用于刷新与协议端之间的本地连接。"""
    port = int(cfg.get("bot_port") or 8080)
    if not bot.alive() and port_in_use(port):
        log(f"端口 {port} 已被外部实例占用，跳过机器人服务重启")
        return
    bot.stop()
    time.sleep(1)
    start_bot(cfg)


def handle_wake(cfg: dict, gap: float) -> None:
    """系统刚睡醒：立刻做一次完整体检并自愈。

    睡眠期间进程被冻结、网络全断，醒来后常见三种情况：
      1. 协议端进程还在、登录仍有效 → 重启机器人服务，刷新本地 WS 连接
      2. 协议端进程还在但 QQ 登录已失效 → 立即重新登录（不等冷却）
      3. 协议端进程已不在（休眠/重启导致） → 交给常规巡检自动拉起
    """
    log(f"🛌 检测到系统睡眠/休眠唤醒（巡检间隔 {gap:.0f} 秒），执行唤醒自愈…")
    time.sleep(5)  # 给网卡和网络一点恢复时间

    online = check_online(cfg)
    if online is None:
        log("唤醒后协议端暂无响应（进程可能已消失），交给常规巡检自动拉起")
        state["logged_in"] = False
        if state["fail_since"] is None:
            state["fail_since"] = time.time()
        return

    if online is False:
        log("唤醒后检测到 QQ 连接已断开，立即重新登录")
        state["logged_in"] = False
        state["fail_since"] = time.time()
        state["last_attempt"] = 0.0  # 允许立即重登，跳过冷却
        relogin(cfg, "睡眠唤醒后连接断开")
        return

    log("唤醒后协议端仍在线，重启机器人服务以刷新本地连接…")
    restart_bot(cfg)
    log("唤醒自愈完成 ✅")


# ---------------- 主循环 ----------------
def ensure_processes(cfg: dict) -> None:
    if not napcat.alive():
        if napcat_pids():
            # 已有外部启动的协议端（例如上次异常退出残留），只监控不重复启动
            if not state.get("external_note"):
                state["external_note"] = True
                log("检测到协议端已在运行（非看门狗启动），本次只监控、不重复启动")
        else:
            state["external_note"] = False
            log("协议端未在运行，自动拉起…")
            start_napcat(cfg)
            if state["fail_since"] is None:
                state["fail_since"] = time.time()

    if not bot.alive():
        port = int(cfg.get("bot_port") or 8080)
        if port_in_use(port):
            # 端口有人监听 = 机器人服务在跑（可能是外部启动的实例），只监控端口
            if not state.get("external_bot_note"):
                state["external_bot_note"] = True
                log(f"机器人端口 {port} 已被外部实例占用，本次只监控、不重复启动")
        else:
            state["external_bot_note"] = False
            log("机器人服务未在运行，自动拉起…")
            start_bot(cfg)


def supervise(cfg: dict) -> None:
    log("看门狗已启动：守护协议端 + 机器人服务，登录失效将自动重登，睡眠唤醒自动自愈")
    last_tick = time.time()
    while True:
        now = time.time()
        gap = now - last_tick
        if gap > WAKE_GAP:
            try:
                handle_wake(cfg, gap)
            except KeyboardInterrupt:
                raise
            except Exception as e:
                log(f"唤醒自愈失败（已忽略，继续守护）：{e}")
        last_tick = time.time()
        try:
            ensure_processes(cfg)
            refresh_login_state(cfg)
            if not state["logged_in"]:
                handle_not_logged_in(cfg)
        except KeyboardInterrupt:
            log("收到退出信号，停止看门狗")
            napcat.stop()
            bot.stop()
            return
        except Exception as e:
            log(f"看门狗异常（已忽略，继续守护）：{e}")
        time.sleep(TICK)


def print_status(cfg: dict) -> None:
    _, _, qr = napcat_paths(cfg)
    refresh_login_state(cfg)
    print(f"协议端进程：{'运行中' if napcat.alive() else '未运行'}")
    print(f"机器人进程：{'运行中' if bot.alive() else '未运行'}")
    print(f"登录状态：{'已登录' if state['logged_in'] else '未登录/已失效'}")
    if ALERT_IMG.exists():
        print(f"需要人工扫码：是（二维码见 {ALERT_IMG}）")
    print(f"NapCat 二维码：{qr}")


def main() -> None:
    parser = argparse.ArgumentParser(description="QQ群管机器人看门狗")
    parser.add_argument("--relogin", action="store_true", help="立即重启协议端并重新登录")
    parser.add_argument("--status", action="store_true", help="打印状态后退出")
    parser.add_argument("--simulate-wake", action="store_true", help="模拟一次睡眠唤醒自愈（测试用）")
    args = parser.parse_args()

    cfg = load_cfg()
    if args.status:
        print_status(cfg)
        return
    if args.simulate_wake:
        log("手动触发唤醒自愈测试")
        handle_wake(cfg, WAKE_GAP + 1)
        print("已执行一次唤醒自愈流程，详细结果见 logs/watchdog.log")
        return
    if args.relogin:
        log("手动触发重新登录")
        relogin(cfg, "手动 --relogin")
        print("已重启协议端，二维码见 NapCat 控制台或 napcat/cache/qrcode.png")
        return
    supervise(cfg)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    main()
