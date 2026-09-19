"""QQ群管机器人 看门狗（watchdog）

职责：
  1. 进程守护：NapCat（协议端）与 NoneBot（机器人服务）掉线/崩溃时自动拉起
  2. 登录失效自动重登：检测到「账号登录已失效」时自动重启协议端快速登录；
     最多自动尝试 2 次（``max_relogin_attempts`` 可调），仍失败即停止重试、
     停掉协议端与机器人服务并退出，待管理员手动运行『启动机器人.bat』恢复——
     避免账号反复触发自动重登加重风控、也让"需要人工"这件事不被无限重试掩盖
  3. 需要扫码时主动提醒：把最新二维码复制到项目根目录（需要扫码登录.png），
     并写提示文件；二维码有效期内只等待不重启，避免把正在扫的码弄失效
  4. 睡眠/休眠唤醒自愈：发现巡检间隔异常拉长（系统刚唤醒）时，
     立刻做一次完整体检——登录失效就立即重登，协议端在线就重启机器人服务刷新本地连接
  5. 僵尸会话检测（假在线）：登录接口可能谎报在线、实际收发全断（错误 1006514），
     定期实测发一条私聊消息，连续失败即强制重登自愈
  6. 防双开：``--relogin``（独立进程）与常驻主循环通过协调文件互斥——重登空窗期
     或协议端刚启动未就绪时，主循环不会误判「未运行」而重复拉起第二个实例
  7. 大量人工干预都不需要：日常只需双击『启动机器人.bat』，它会一直守护

用法：
  python deploy/watchdog.py            # 常驻守护（推荐，启动机器人.bat 调用它）
  python deploy/watchdog.py --relogin  # 立即重启协议端并尝试重新登录
  python deploy/watchdog.py --status   # 打印当前状态后退出
"""
import argparse
import json
import os
import shutil
import smtplib
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from email.message import EmailMessage
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
MAX_RELOGIN_ATTEMPTS = 2    # 自动重登最多尝试次数，超过即放弃并停机待人工处理
QR_FRESH = 300              # 二维码"新鲜期"：期间只等待扫码，不重启进程
ZOMBIE_FAILS = 2            # 心跳自检连续失败多少次判定为僵尸会话
ZOMBIE_RETRY_FAST = 60      # 已有失败记录后，下次自检的等待时间（秒）
ZOMBIE_LOGIN_GRACE = 120    # 登录成功后先给会话这么久的稳定期再开始自检（秒）
PROBE_NONE_LIMIT = 10       # 心跳自检连续多少次"无响应"判定协议端整体僵死（约 10 分钟）
RELOGIN_GRACE = 150         # ``--relogin`` 期间：主循环在这段时间内不碰协议端（跨进程防双开）
NAPCAT_START_GRACE = 75     # 协议端刚启动后的观察期：期间主循环不重复拉起（防双开）

OK_PATTERNS = ("已通知主进程登录成功", "Worker进程已登录成功", "切换到正常重试策略")
FAIL_PATTERNS = ("[Login] Login Error", "登录已失效", "Login failed")
QR_PATTERNS = ("请扫描下面的二维码", "二维码已保存到")

ALERT_IMG = BASE / "需要扫码登录.png"
ALERT_TXT = BASE / "需要扫码登录.txt"
# 跨进程协调文件：``--relogin`` 是独立进程，与常驻主循环之间靠它交换"协议端正在启动/重登"的标记，
# 否则主循环会在重登的空窗期误判"协议端未运行"而再拉起一个实例 → 双开
STATE_FILE = LOGS / "watchdog.state.json"

state = {
    "logged_in": False,
    "fail_since": None,
    "last_attempt": 0.0,
    "last_qr": 0.0,
    "attempts": 0,
    "alerted": False,
    "log_pos": 0,
    "last_probe": 0.0,   # 上次心跳自检时间
    "probe_fails": 0,    # 心跳自检连续失败次数
    "probe_none": 0,     # 心跳自检连续"无响应"次数（接口不通/挂起，无法判断）
}


def log(msg: str) -> None:
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    try:
        with open(LOGS / "watchdog.log", "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        # 主日志写入失败（如被安全软件拦截新建文件）时降级到备用文件，黑匣子不能丢
        try:
            with open(LOGS / "watchdog.log.alt", "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass


# ---------------- 控制台防护 ----------------
def disable_quickedit() -> None:
    """关闭控制台"快速编辑模式"。

    Windows 控制台默认开启 QuickEdit：用户在窗口里用鼠标选中文字（哪怕无意点到）
    会让所有 print 无限期阻塞，看门狗主循环整个挂起——表现为"窗口没输出、
    探针也不发了，但进程还活着"。关掉它从根上杜绝这类人手误触的自愈停摆。
    """
    if not IS_WIN:
        return
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.GetStdHandle(-10)  # STD_INPUT_HANDLE
        mode = ctypes.c_uint()
        if k32.GetConsoleMode(h, ctypes.byref(mode)):
            # ENABLE_EXTENDED_FLAGS(0x80) 必须置位 QUICK_EDIT(0x40) 的修改才生效
            k32.SetConsoleMode(h, (int(mode.value) | 0x0080) & ~0x0040)
    except Exception:
        pass


# ---------------- 跨进程协调（防双开） ----------------
def wd_state_read() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def wd_state_write(**kw) -> None:
    """合并写入协调标记。多个进程同时写概率极低，读-改-写足够。"""
    d = wd_state_read()
    d.update(kw)
    try:
        STATE_FILE.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def napcat_busy() -> str:
    """协议端是否处于「刚启动 / 正在重登」的宽限期内。

    返回非空字符串 = 正在忙（含原因），此时主循环不应插手协议端，
    以免与 ``--relogin`` 抢着启动造成双开；返回空串 = 可以正常巡检。
    """
    d = wd_state_read()
    now = time.time()
    ts = float(d.get("relogin_at") or 0)
    if ts and now - ts < RELOGIN_GRACE:
        return f"正在重登（{d.get('relogin_reason') or '未注明'}）"
    ts = float(d.get("napcat_start_at") or 0)
    if ts and now - ts < NAPCAT_START_GRACE:
        return "协议端刚启动，等待就绪"
    return ""


def load_cfg() -> dict:
    p = BASE / "部署配置.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            log(f"部署配置.json 解析失败，使用默认值：{e}")
    return {}


# ---------------- 邮件告警（可选） ----------------
NOTIFY_COOLDOWN = 1800  # 同类通知的最小间隔（秒）


def notify(cfg: dict, subject: str, body: str, key: str = "default") -> None:
    """邮件告警：在 部署配置.json 里配置 SMTP 后启用（默认关闭）。

    主要用途——机器人掉线需要人工扫码时，你不在电脑边也能收到提醒；
    另外自动重登、僵尸会话强制重登也会发一封，便于事后追溯。
    """
    if not cfg.get("alert_email_enabled"):
        return
    host = str(cfg.get("smtp_host") or "").strip()
    to = str(cfg.get("alert_email_to") or "").strip()
    user = str(cfg.get("smtp_user") or "").strip()
    pwd = str(cfg.get("smtp_pass") or "").strip()
    if not (host and to and user):
        log("邮件告警已启用但配置不完整：需要 smtp_host / smtp_user / smtp_pass / alert_email_to")
        return
    now = time.time()
    marks = state.setdefault("notify_at", {})
    if now - marks.get(key, 0) < NOTIFY_COOLDOWN:
        return
    marks[key] = now

    msg = EmailMessage()
    msg["Subject"] = f"[QQ群管机器人] {subject}"
    msg["From"] = user
    msg["To"] = to
    msg.set_content(
        f"{body}\n\n"
        f"时间：{datetime.now():%Y-%m-%d %H:%M:%S}\n"
        f"项目目录：{BASE}\n"
        f"处理办法见 README / 维护文档，或直接问 AI 助理。"
    )
    port = int(cfg.get("smtp_port") or 465)
    try:
        if cfg.get("smtp_ssl", True):
            with smtplib.SMTP_SSL(host, port, timeout=15) as s:
                s.login(user, pwd)
                s.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=15) as s:
                s.starttls()
                s.login(user, pwd)
                s.send_message(msg)
        log(f"已发送邮件告警：{subject}")
    except Exception as e:
        log(f"邮件告警发送失败：{e}")


def napcat_paths(cfg: dict):
    napcat_root = Path(cfg.get("napcat_dir") or (Path.home() / "NapCat"))
    napcat_dir = napcat_root / "NapCat"
    # 兼容两种布局：<root>/NapCat/node.exe（项目约定）与 <root>/node.exe（离线包直接解压）
    if not (napcat_dir / "index.js").exists() and (napcat_root / "index.js").exists():
        napcat_dir = napcat_root
    return napcat_dir, napcat_dir / "node.exe", napcat_dir / "napcat" / "cache" / "qrcode.png"


def project_dir(cfg: dict) -> Path:
    p = (cfg.get("project_dir") or "").strip()
    return Path(p) if p else BASE / "qq-group-bot"


def bot_is_silenced(cfg: dict) -> bool:
    """读取机器人的静默状态（与 qq-group-bot/data/silence.json 共用）。

    静默期内不发送心跳自检消息，避免打扰用户、也避免额外增加风控风险。
    """
    try:
        with open(project_dir(cfg) / "data" / "silence.json", "r", encoding="utf-8") as f:
            return float(json.load(f).get("until") or 0) > time.time()
    except Exception:
        return False


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
            capture_output=True, text=True, timeout=12,
        ).stdout
        return [int(x) for x in out.split() if x.strip().isdigit()]
    except subprocess.TimeoutExpired:
        log("查询 NapCat 进程超时（PowerShell 无响应），本次改用端口探测")
        return []
    except Exception:
        return []


def napcat_running(cfg: dict) -> bool:
    """协议端是否在运行。

    优先按进程判定；进程查询失败（被安全策略拦截 / PowerShell 超时）时回退到端口探测——
    NapCat 的 WebUI 与 HTTP 端口只要有一个在监听，就说明协议端活着。
    这个回退很关键：进程探测一旦失灵，主循环会误判"未运行"并重复拉起 → 双开。
    """
    if napcat_pids():
        return True
    for key, default in (("napcat_webui_port", 6099), ("napcat_http_port", 3000)):
        try:
            if port_in_use(int(cfg.get(key) or default)):
                return True
        except Exception:
            continue
    return False


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

    def start(self, args, cwd, env=None) -> None:
        try:
            f = open(self.logfile, "a", encoding="utf-8", errors="replace")
            f.write(f"\n===== {datetime.now():%Y-%m-%d %H:%M:%S} 启动 {self.name} =====\n")
            f.flush()
            self.popen = subprocess.Popen(
                args, cwd=str(cwd), stdout=f, stderr=subprocess.STDOUT,
                creationflags=CREATE_NO_WINDOW, env=env,
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


def napcat_extra_env(cfg: dict):
    """密码回退登录（NapCat 4.16+ 支持）：配置了 napcat_password 时注入环境变量，
    快速登录(-q)失效时 NapCat 会自动改用密码登录，避免人工扫码。
    注意：机房 IP 上密码登录可能触发验证码/新设备验证，届时仍需人工在 WebUI 处理。"""
    pwd = str(cfg.get("napcat_password") or "").strip()
    if not pwd:
        return None
    env = os.environ.copy()
    env["NAPCAT_QUICK_PASSWORD"] = pwd
    log("已注入密码回退环境变量（快速登录失效时自动改用密码登录，不打印密码）")
    return env


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
        wd_state_write(napcat_start_at=time.time())
        return

    if not exe.exists():
        log(f"找不到 {exe}，请先运行『一键部署.bat』")
        return
    args = [str(exe), "index.js"]
    if qq:
        args += ["-q", qq]
    napcat.start(args, napcat_dir, env=napcat_extra_env(cfg))
    # 记录启动时刻：进程从 launcher 到 worker 就绪有几秒真空，主循环若在此刻巡检
    # 会误判"未运行"而重复拉起 → 双开
    wd_state_write(napcat_start_at=time.time())


_PY_CACHE: dict = {}


def _can_import_nonebot(exe: str) -> bool:
    """探测某个解释器是否能导入 nonebot。"""
    try:
        r = subprocess.run(
            [exe, "-c", "import nonebot"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW if IS_WIN else 0,
        )
        return r.returncode == 0
    except Exception:
        return False


def resolve_python(cfg: dict) -> str:
    """挑选可用来运行 bot.py 的 Python 解释器。

    背景：电脑上常装了多个 Python，看门狗自身只用标准库（哪个都能跑），
    但机器人服务需要 nonebot。若直接沿用 sys.executable，
    在"看门狗被另一个没装 nonebot 的 Python 启动"时，
    机器人服务会每轮巡检拉起一次、每次秒退，日志刷满 ModuleNotFoundError。

    顺序：部署配置.json 的 python_path → 当前解释器 → 项目内 venv → PATH。
    探测结果在当前进程内缓存，避免每次重拉都跑一遍。
    """
    override = str(cfg.get("python_path") or "").strip()
    if override in _PY_CACHE:
        return _PY_CACHE[override]

    candidates: list = []
    if override:
        candidates.append(override)
    candidates.append(sys.executable)
    for rel in ("venv/Scripts/python.exe", ".venv/Scripts/python.exe",
                "venv/bin/python", ".venv/bin/python"):
        candidates.append(str(BASE / rel))
    for name in ("python3", "python"):
        found = shutil.which(name)
        if found:
            candidates.append(found)
    # 最后兜底：WorkBuddy 内置 Python（本机 nonebot 目前装在这里；别处一般用不到）
    wb_root = Path.home() / ".workbuddy" / "binaries" / "python" / "versions"
    if wb_root.is_dir():
        for d in sorted(wb_root.iterdir()):
            exe = d / ("python.exe" if IS_WIN else "bin/python")
            if exe.exists():
                candidates.append(str(exe))

    tried = set()
    for exe in candidates:
        if not exe or exe in tried:
            continue
        tried.add(exe)
        if not Path(exe).exists():
            continue
        if _can_import_nonebot(exe):
            if exe != sys.executable:
                log(f"机器人服务将使用解释器：{exe}")
            _PY_CACHE[override] = exe
            return exe

    log("⚠ 找不到已安装 nonebot 的 Python 解释器，机器人服务无法启动")
    log("  → 请先双击『一键部署.bat』，或在 部署配置.json 里设置 python_path")
    _PY_CACHE[override] = sys.executable
    return sys.executable


def start_bot(cfg: dict) -> None:
    pdir = project_dir(cfg)
    if not (pdir / "bot.py").exists():
        log(f"找不到 {pdir / 'bot.py'}，跳过机器人服务启动")
        return
    bot.start([resolve_python(cfg), "bot.py"], pdir)


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
                # 仅在“掉线→恢复”跳变时清零计数；若每轮日志扫描都清零，
                # 假在线期间心跳失败记录永远攒不到阈值（复测被反复重置）
                state.update(logged_in=True, fail_since=None, attempts=0,
                             last_attempt=0.0, alerted=False, probe_fails=0)
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
            # 登录刚恢复：给会话一个稳定期，稳定期过后开始心跳自检
            interval = int(cfg.get("zombie_check_interval") or 3600)
            state["last_probe"] = time.time() - max(interval - ZOMBIE_LOGIN_GRACE, 0)
            # 仅在“掉线→恢复”跳变时清零计数。假在线时 get_login_info 会谎报“已登录”，
            # 若每轮都清零，心跳失败记录永远攒不到 2 次（60 秒快速复测被反复重置为 600 秒）
            state.update(logged_in=True, fail_since=None, attempts=0,
                         last_attempt=0.0, alerted=False, probe_fails=0)
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
    # 先立旗：告诉可能同时运行的看门狗主循环"协议端正由我重启"，别插手
    wd_state_write(relogin_at=time.time(), relogin_reason=reason, relogin_pid=os.getpid())
    notify(
        cfg,
        f"协议端重新登录（{reason}）",
        f"看门狗已重启协议端尝试重新登录。\n原因：{reason}\n"
        "若随后需要人工扫码，会再收到一封提醒邮件。",
        key="relogin",
    )
    napcat.stop()
    # 清理残留（含外部启动的）实例，确认真没了再启动，避免端口冲突导致拉起即崩
    for _ in range(5):
        if not napcat_pids():
            break
        kill_napcat_processes()
        time.sleep(1.5)
    time.sleep(1)
    start_napcat(cfg)
    state["fail_since"] = time.time()
    state["external_note"] = False


def max_relogin_attempts(cfg: dict) -> int:
    try:
        return max(1, int(cfg.get("max_relogin_attempts") or MAX_RELOGIN_ATTEMPTS))
    except Exception:
        return MAX_RELOGIN_ATTEMPTS


def give_up(cfg: dict, attempts: int) -> None:
    """自动重登连续失败达上限：停止重试，停掉两个服务后退出看门狗。

    设计意图：账号被风控踢下线后，反复自动重登只会加重风控、且掩盖"需要人工"。
    放弃时留下最终提示文件，恢复完全交给管理员手动执行。
    """
    log(f"⚠ 自动重登已连续失败 {attempts} 次，按策略放弃自动重试")
    log("→ 即将停止协议端与机器人服务并退出看门狗")
    log("→ 恢复方法：管理员双击『启动机器人.bat』，如提示扫码请用机器人小号扫码")
    try:
        ALERT_TXT.write_text(
            "【QQ群管机器人】自动重登失败，已停止运行，需要人工恢复\n"
            f"时间：{datetime.now():%Y-%m-%d %H:%M:%S}\n"
            f"看门狗已连续自动重登 {attempts} 次均未成功，按策略放弃并停机。\n\n"
            "恢复步骤：\n"
            "  1. 双击『启动机器人.bat』重新启动\n"
            "  2. 若出现『需要扫码登录.png』，用手机 QQ（机器人小号）扫码授权\n"
            "  3. 登录成功后本文件会自动消失\n\n"
            "提示：机房 IP / 异地登录更容易被风控踢下线；若频繁被踢，建议换小号。\n",
            encoding="utf-8",
        )
    except Exception:
        pass
    notify(
        cfg,
        "自动重登失败已达上限，看门狗已停止",
        f"看门狗已连续自动重登 {attempts} 次未成功，按策略放弃并停止了协议端与机器人服务。\n"
        "请人工处理：双击『启动机器人.bat』，需要时扫码登录。",
        key="giveup",
    )
    # 停干净：看门狗子进程 + 可能的外部残留实例，避免管理员重启时端口被占
    napcat.stop()
    bot.stop()
    for _ in range(5):
        if not napcat_pids():
            break
        kill_napcat_processes()
        time.sleep(1.5)
    wd_state_write(relogin_at=0, napcat_start_at=0)  # 清掉互斥旗，别挡下次手动启动
    log("看门狗已停止。下次启动：双击『启动机器人.bat』")
    raise SystemExit(0)  # SystemExit 不被 supervise 的 except Exception 捕获，可直接退出主循环


def handle_not_logged_in(cfg: dict) -> None:
    """未登录时的处理策略：二维码新鲜→只提醒等待；否则→尝试重启重登；
    自动重登达到上限仍失败→放弃并停机，待管理员手动恢复。
    """
    now = time.time()
    qr_fresh = state["last_qr"] and (now - state["last_qr"] < QR_FRESH)

    if qr_fresh:
        # 二维码还有效：刷新提醒文件，安静等待用户扫码
        if sync_alert(cfg) and not state["alerted"]:
            state["alerted"] = True
            log("等待人工扫码：二维码已放到项目根目录『需要扫码登录.png』")
            print("\a", end="", flush=True)
            notify(
                cfg,
                "需要人工扫码登录",
                "机器人账号被踢下线，且无法免扫码自动重登，需要你扫码。\n"
                f"二维码：{ALERT_IMG}\n"
                "用手机 QQ（机器人小号）扫描该图片完成登录。\n"
                "二维码约 2 分钟过期，图片会自动刷新为最新二维码。",
                key="qr",
            )
        return

    # 走到这里说明当前没有新鲜二维码在等人扫。若自动重登次数已用满仍未恢复，
    # 不再无限重试——放弃并停机，把恢复权交还管理员
    attempts = state["attempts"]
    if attempts >= max_relogin_attempts(cfg):
        give_up(cfg, attempts)
        return

    waited = now - (state["fail_since"] or now)
    since_attempt = now - state["last_attempt"]
    if waited >= FAIL_BEFORE_RELOGIN and since_attempt >= RELOGIN_COOLDOWN:
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


# ---------------- 僵尸会话检测（假在线自愈） ----------------
def probe_send(cfg: dict):
    """实测发一条私聊消息，验证会话真正可用（get_status 会谎报在线）。

    返回 True=发送成功，False=服务端拒绝（会话僵死），None=无法判断。
    """
    port = int(cfg.get("napcat_http_port") or 3000)
    token = str(cfg.get("napcat_http_token") or "napcat-http-token")
    target = str(cfg.get("zombie_check_target") or "").strip()
    if not target:
        supers = cfg.get("superusers") or []
        target = str(supers[0]).strip() if supers else ""
    if not target:
        return None  # 没配置自检对象，跳过
    payload = json.dumps({
        "user_id": int(target),
        "message": f"🔧 看门狗心跳自检 {datetime.now():%H:%M}，可忽略",
    }).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/send_private_msg",
        data=payload,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        return data.get("status") == "ok" and data.get("retcode") == 0
    except urllib.error.HTTPError:
        return None  # token/接口问题，交给日志判断，不计入失败
    except Exception:
        return None  # HTTP 接口本身不通，交给进程守护处理


def check_zombie(cfg: dict) -> None:
    """僵尸会话检测：登录态显示在线，但实测发消息被拒 → 假在线，强制重登。

    - 仅在已登录、且心跳自检开启时执行
    - 连续失败 ZOMBIE_FAILS 次判定为僵尸会话（首次失败后快速复测，避免误判）
    - 发送失败的探针消息不会送达对方，因此失败时不会打扰任何人
    """
    if not cfg.get("zombie_check", True):
        return
    if not state["logged_in"]:
        return
    if bot_is_silenced(cfg):
        return  # 静默期不发自检消息
    interval = int(cfg.get("zombie_check_interval") or 3600)
    now = time.time()
    # 正常时按 interval 自检；一旦有失败或无响应记录则快速复测，缩短确认时间
    due = ZOMBIE_RETRY_FAST if (state["probe_fails"] or state["probe_none"]) else interval
    if now - state["last_probe"] < due:
        return
    state["last_probe"] = now
    ok = probe_send(cfg)
    if ok is True:
        if state["probe_fails"] or state["probe_none"]:
            log("心跳自检恢复正常 ✅")
        state["probe_fails"] = 0
        state["probe_none"] = 0
        return
    if ok is None:
        # 接口无响应：可能是 NapCat 的 sendMsg 内部通道挂起（假在线加深的表现）。
        # 不能一直静默——连续多次无响应视为协议端整体僵死，兜底强制重登
        state["probe_none"] += 1
        if state["probe_none"] == 1:
            log("⚠ 心跳自检接口无响应（第 1 次），将持续复测…")
        elif state["probe_none"] % 5 == 0:
            log(f"⚠ 心跳自检接口已连续 {state['probe_none']} 次无响应")
        if state["probe_none"] >= PROBE_NONE_LIMIT:
            log("判定协议端整体僵死（接口持续无响应），强制重新登录")
            state["probe_none"] = 0
            state["probe_fails"] = 0
            state["logged_in"] = False
            state["fail_since"] = time.time()
            state["last_attempt"] = 0.0  # 跳过冷却，立即重登
            relogin(cfg, "协议端接口持续无响应（整体僵死）强制重登")
        return
    state["probe_none"] = 0
    state["probe_fails"] += 1
    log(f"⚠️ 心跳自检失败（{state['probe_fails']}/{ZOMBIE_FAILS}）："
        f"实测发消息被拒，会话可能已僵死（假在线）")
    if state["probe_fails"] >= ZOMBIE_FAILS:
        log("判定为僵尸会话（假在线），强制重新登录")
        state["probe_fails"] = 0
        state["logged_in"] = False
        state["fail_since"] = time.time()
        state["last_attempt"] = 0.0  # 跳过冷却，立即重登
        relogin(cfg, "僵尸会话（假在线）强制重登")


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
        busy = napcat_busy()
        if busy:
            # 关键防双开：另一进程正在重登，或协议端刚启动还没就绪（launcher→worker 有几秒真空），
            # 此时贸然"自动拉起"就会起出第二个实例 —— 正是之前双开竞态的成因
            if state.get("busy_note") != busy:
                state["busy_note"] = busy
                log(f"暂不启动协议端：{busy}")
        elif napcat_running(cfg):
            # 已有外部启动的协议端（例如上次异常退出残留），只监控不重复启动
            state["busy_note"] = None
            if not state.get("external_note"):
                state["external_note"] = True
                log("检测到协议端已在运行（非看门狗启动），本次只监控、不重复启动")
        else:
            state["busy_note"] = None
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
    # 启动时直接从日志末尾开始监听：napcat.log 里的历史掉线/登录记录不属于本次会话，
    # 从头扫描会把旧记录逐条重报一遍（“登录成功/失效”交替刷屏），严重时还会误触发重登
    naplog = LOGS / "napcat.log"
    if naplog.exists():
        try:
            state["log_pos"] = naplog.stat().st_size
        except OSError:
            pass
    log("看门狗已启动：守护协议端 + 机器人服务，登录失效将自动重登，睡眠唤醒自动自愈")
    if not (LOGS / "watchdog.log").exists():
        log("⚠ watchdog.log 无法写入（可能被安全软件拦截），日志只在窗口显示，排障会受影响")
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
            else:
                check_zombie(cfg)
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
    if state["logged_in"]:
        if state["probe_fails"]:
            print(f"心跳自检：异常（连续失败 {state['probe_fails']} 次）")
        else:
            print("心跳自检：正常")
    if bot_is_silenced(cfg):
        print("静默模式：开启中（群功能停摆、心跳自检暂停）")
    if ALERT_IMG.exists():
        print(f"需要人工扫码：是（二维码见 {ALERT_IMG}）")
    print(f"NapCat 二维码：{qr}")


def main() -> None:
    disable_quickedit()
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
