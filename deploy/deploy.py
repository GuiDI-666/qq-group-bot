"""QQ群管机器人一键部署脚本。

读取『部署配置.json』，自动完成：
  1. 安装 Python 依赖（完整锁定版本清单 deps/requirements-full.txt）
  2. 检查/安装 VC++ 运行库（msvcp140.dll）
  3. 解压 NapCat 到配置的目录
  4. 补齐 crypto.dll / ssl.dll（QQ内核依赖）
  5. 写入 NapCat 反向 WebSocket 配置
  6. 写入机器人 .env（超级管理员/端口）
  7. 生成启动脚本『启动机器人.bat』
"""
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent  # QQ群管机器人/
DEPS = BASE / "deps"
CONFIG_PATH = BASE / "部署配置.json"
CONFIG_EXAMPLE = BASE / "部署配置.example.json"
SELF_CHECK = Path(__file__).resolve().parent / "self_check.py"


def log(step: str, msg: str):
    print(f"[{step}] {msg}")


def load_config() -> dict:
    # 仓库不包含真实配置；首次运行从模板生成一份，交给用户填写
    if not CONFIG_PATH.exists():
        if CONFIG_EXAMPLE.exists():
            shutil.copyfile(CONFIG_EXAMPLE, CONFIG_PATH)
            print("⚠ 未找到『部署配置.json』，已根据模板生成一份。")
            print(f"  请先编辑 {CONFIG_PATH}")
            print("  填入：机器人QQ（bot_qq）、超级管理员QQ（superusers）、NapCat目录（napcat_dir）")
            print("  保存后重新双击『一键部署.bat』即可。")
        else:
            print(f"❌ 缺少配置文件：{CONFIG_PATH}")
            print("  请从『部署配置.example.json』复制一份并改名为『部署配置.json』")
        input("按回车键退出...")
        sys.exit(1)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def napcat_root_ok(raw) -> bool:
    """配置里的 napcat_dir 在本机是否可用（父目录存在即可自动创建）。"""
    raw = (raw or "").strip()
    if not raw:
        return False
    try:
        p = Path(raw)
        return p.is_absolute() and p.parent.exists()
    except OSError:
        return False


def default_napcat_root() -> Path:
    """新机器上的默认协议端目录：放盘符根目录，避开中文/空格路径给 QQ 内核添麻烦。"""
    if os.name == "nt":
        return Path("C:/NapCat")
    return Path.home() / "NapCat"


def save_config(cfg: dict) -> None:
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
            f.write("\n")
    except Exception as e:
        print(f"⚠ 回写 部署配置.json 失败：{e}")


def ensure_project_config(project_dir: Path) -> None:
    """确保机器人热配置存在：仓库只带 config.example.json，首次运行复制一份。"""
    target = project_dir / "config.json"
    example = project_dir / "config.example.json"
    if target.exists() or not example.exists():
        return
    shutil.copyfile(example, target)
    log("配置", "已生成机器人热配置 config.json（默认规则，可用命令修改）")


def check_python() -> None:
    if sys.version_info < (3, 9):
        print("❌ 需要 Python 3.9 及以上版本，请先安装：https://www.python.org/downloads/")
        print("   安装时务必勾选 'Add Python to PATH'")
        input("按回车键退出...")
        sys.exit(1)
    log("1/7", f"Python 环境OK（{sys.version.split()[0]}）")


def pip_indexes() -> list:
    """依赖源候选：环境变量 QQBOT_PIP_INDEX → 阿里云镜像 → 官方 PyPI。

    国内服务器直连 pypi.org 常超时，阿里云镜像稳定得多；镜像缺包时自动回落官方源。
    """
    tries = []
    env = str(os.environ.get("QQBOT_PIP_INDEX") or "").strip()
    if env:
        tries.append(["-i", env])
    tries.append(["-i", "https://mirrors.aliyun.com/pypi/simple/",
                  "--trusted-host", "mirrors.aliyun.com"])
    tries.append(["-i", "https://pypi.org/simple"])
    return tries


def install_deps() -> None:
    req = DEPS / "requirements-full.txt"
    log("2/7", "安装 Python 依赖（首次安装约1-2分钟）...")
    for extra in pip_indexes():
        r = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", str(req), "-q",
             "--disable-pip-version-check", *extra]
        )
        if r.returncode == 0:
            log("2/7", "依赖安装完成")
            return
        print(f"⚠ 依赖安装失败（源：{extra[1]}），换一个源重试…")
    print("❌ 依赖安装失败，请检查网络后重试")
    input("按回车键退出...")
    sys.exit(1)


def check_vcredist(cfg: dict) -> None:
    import ctypes
    try:
        ctypes.WinDLL("msvcp140.dll")
        log("3/7", "VC++ 运行库已存在，跳过")
        return
    except OSError:
        pass
    if not cfg.get("auto_install_vcredist", True):
        print("⚠️ 缺少 VC++ 运行库且配置为不自动安装，请手动运行 deps/vc_redist.x64.exe")
        return
    exe = DEPS / "vc_redist.x64.exe"
    if not exe.exists():
        print("⚠️ 缺少 deps/vc_redist.x64.exe，请手动安装 VC++ 运行库")
        return
    log("3/7", "检测到缺少 VC++ 运行库，正在安装（需要管理员权限，请在弹窗中允许）...")
    r = subprocess.run(
        [str(exe), "/install", "/passive", "/norestart"], capture_output=True
    )
    log("3/7", f"VC++ 运行库安装完成（退出码 {r.returncode}，0/1638=正常，3010=需重启）")


def zip_top_layout(zip_path: Path) -> str:
    """判断离线包的结构：'prefixed'（自带 NapCat/ 层）还是 'flat'（根目录就是本体）。

    两种包都出现过，解压位置不同：
      prefixed -> 解压到 <napcat_root>，其下才会有 NapCat/
      flat     -> 解压到 <napcat_root>/NapCat 里
    新机器上若不区分，协议端会落在错误层级、node.exe 找不到，机器人永远起不来。
    """
    with zipfile.ZipFile(zip_path) as z:
        for name in z.namelist():
            head = name.replace("\\", "/").split("/", 1)[0]
            if head not in ("", "."):
                return "prefixed" if head == "NapCat" else "flat"
    return "flat"


def deploy_napcat(cfg: dict) -> Path:
    napcat_root = Path(cfg["napcat_dir"])
    napcat_dir = napcat_root / "NapCat"
    zip_path = DEPS / "NapCat.Shell.Windows.Node.zip"
    if (napcat_dir / "index.js").exists():
        log("4/7", f"NapCat 已存在（{napcat_dir}），跳过解压")
    else:
        if not zip_path.exists():
            print(f"❌ 缺少 {zip_path}，无法部署 NapCat")
            input("按回车键退出...")
            sys.exit(1)
        layout = zip_top_layout(zip_path)
        target = napcat_root if layout == "prefixed" else napcat_dir
        log("4/7", f"解压 NapCat 到 {target} （约30秒，包结构 {layout}）...")
        target.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(target)
        if not (napcat_dir / "index.js").exists():
            print(f"❌ 解压后仍找不到 {napcat_dir / 'index.js'}")
            print("   离线包结构异常，请重新获取 deps/NapCat.Shell.Windows.Node.zip")
            input("按回车键退出...")
            sys.exit(1)

    # 补齐 QQ 内核依赖的 OpenSSL DLL
    for dll in ("crypto.dll", "ssl.dll"):
        src = DEPS / dll
        dst = napcat_dir / dll
        if not dst.exists() and src.exists():
            shutil.copy2(src, dst)
            log("4/7", f"已补齐缺失依赖 {dll}")
    log("4/7", "NapCat 部署完成")
    return napcat_dir


def write_napcat_config(cfg: dict, napcat_dir: Path) -> None:
    cfg_dir = napcat_dir / "napcat" / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    path = cfg_dir / f"onebot11_{cfg['bot_qq']}.json"
    if path.exists():
        log("5/7", f"NapCat 网络配置已存在（{path.name}），跳过（如需重置请删除该文件）")
        return
    network = {
        "network": {
            "websocketServers": [],
            # 注意：NapCat 的"反向WS"客户端叫 websocketClients（不是 reverseWebSocketClients）
            "websocketClients": [
                {
                    "enable": True,
                    "name": "group-bot",
                    "url": cfg["ws_url"],
                    "reconnectInterval": 5000,
                    "messagePostFormat": "array",
                    "reportSelfMessage": False,
                    "token": "",
                    "debug": False,
                    "heartInterval": 30000,
                }
            ],
            "httpServers": [
                {
                    "enable": True,
                    "name": "watchdog-http",
                    "host": "127.0.0.1",
                    "port": int(cfg.get("napcat_http_port", 3000)),
                    "enableCors": False,
                    "enableWebsocket": False,
                    "messagePostFormat": "array",
                    "token": str(cfg.get("napcat_http_token", "napcat-http-token")),
                    "debug": False,
                    "heartInterval": 30000,
                }
            ],
            "httpClients": [],
        },
        "musicSignUrl": "",
        "enableLocalFile2Url": False,
        "parseMultMsg": False,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(network, f, ensure_ascii=False, indent=2)
    log("5/7", f"已写入 NapCat 反向 WebSocket 配置 → {path.name}（{cfg['ws_url']}）")


def write_bot_env(cfg: dict, project_dir: Path) -> None:
    env_path = project_dir / ".env"
    host = cfg["ws_url"].split("//")[1].split(":")[0]

    # 保留已有 .env 中除本次托管键以外的自定义项（避免覆盖用户额外配置）
    managed = {"HOST", "PORT", "LOG_LEVEL", "SUPERUSERS", "COMMAND_START"}
    keep: list[str] = []
    if env_path.exists():
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.split("=", 1)[0].strip() in managed:
                continue
            keep.append(line)

    env = (
        f"HOST={host}\n"
        f"PORT={cfg['bot_port']}\n"
        f"LOG_LEVEL=INFO\n"
        f"SUPERUSERS={json.dumps(cfg['superusers'])}\n"
        "# 命令前缀：只认「/命令」形式（如 /禁言、/ping）；普通聊天文字不会误触发\n"
        'COMMAND_START=["/"]\n'
    )
    if keep:
        env += "\n# ---- 以下为保留的自定义配置 ----\n" + "\n".join(keep) + "\n"
    with open(env_path, "w", encoding="utf-8") as f:
        f.write(env)
    log("6/7", f"已写入机器人配置 {env_path}（SUPERUSERS={cfg['superusers']}）")


def write_start_bat(cfg: dict, project_dir: Path, napcat_dir: Path) -> None:
    bat = BASE / "启动机器人.bat"
    # 用 %~dp0 自动定位脚本所在目录，避免把本机绝对路径写进仓库。
    #
    # 两个坑必须避开：
    #   1) 文件必须是 GBK + chcp 936。UTF-8 中文 bat 配 chcp 65001 时 cmd 会按字节
    #      偏移错位重读文件，把 echo 文本当命令执行，双击直接闪退。
    #   2) 必须自己挑「装了 nonebot」的解释器。电脑上可能装了多个 Python，
    #      裸 python 可能落到没装 nonebot 的那个，导致机器人服务反复秒退。
    content = """@echo off
chcp 936 >nul
title QQ群管机器人 一键启动（看门狗模式）
cd /d "%~dp0"
setlocal enabledelayedexpansion

echo ==========================================
echo   QQ群管机器人 一键启动
echo   看门狗守护：协议端(NapCat) + 机器人(NoneBot)
echo   登录失效会自动重新登录，进程掉线会自动拉起
echo ==========================================
echo.
echo 机器人账号 / 端口 / 管理群：见 部署配置.json 与 qq-group-bot\\config.json
echo 关闭本窗口即停止守护（协议端与机器人会一并退出）
echo.

rem ---------- 先挑一个"装了 nonebot"的 Python ----------
set "PY="
if exist "%~dp0venv\\Scripts\\python.exe" set "PY=%~dp0venv\\Scripts\\python.exe"
if not defined PY if exist "%~dp0.venv\\Scripts\\python.exe" set "PY=%~dp0.venv\\Scripts\\python.exe"

if not defined PY (
  for /f "delims=" %%P in ('where python 2^>nul ^| findstr /i /v "WindowsApps"') do (
    if not defined PY (
      "%%P" -c "import nonebot" >nul 2>nul
      if !errorlevel! equ 0 set "PY=%%P"
    )
  )
)

if not defined PY (
  echo [错误] 没有找到"已安装 nonebot"的 Python 解释器。
  echo.
  echo   三种解决办法（任选其一）：
  echo     1. 双击『一键部署.bat』，让脚本自动装依赖
  echo     2. 双击『服务器部署.bat』（服务器/新机器一键准备）
  echo     3. 在 部署配置.json 里写死解释器路径，例如：
  echo          "python_path": "C:\\\\Python313\\\\python.exe"
  echo.
  pause
  exit /b 1
)

echo 使用 Python：!PY!
echo.
"!PY!" "%~dp0deploy\\watchdog.py"

echo.
echo 看门狗已退出。按任意键关闭窗口。
pause >nul
"""
    with open(bat, "w", encoding="gbk", newline="\r\n") as f:
        f.write(content)
    log("7/7", f"已生成启动脚本 {bat}（看门狗守护模式，GBK 编码）")


def main():
    print("=" * 50)
    print("  QQ群管机器人 · 一键部署")
    print("=" * 50)
    cfg = load_config()
    project_dir = Path(cfg.get("project_dir") or BASE / "qq-group-bot")
    if not (project_dir / "bot.py").exists():
        print(f"❌ 在 {project_dir} 找不到 bot.py，请检查 部署配置.json 的 project_dir")
        input("按回车键退出...")
        sys.exit(1)

    # 换机器（例如从本机搬到服务器）时，旧的 napcat_dir 往往指向别的机器的用户目录，
    # 不纠正的话协议端会被解压到一个莫名其妙的位置，看门狗也找不到它。
    if not napcat_root_ok(cfg.get("napcat_dir")):
        old = cfg.get("napcat_dir")
        cfg["napcat_dir"] = str(default_napcat_root())
        print(f"⚠ 配置里的 napcat_dir 在本机不可用：{old}")
        print(f"  已自动改用：{cfg['napcat_dir']}（并写回 部署配置.json）")
        save_config(cfg)

    check_python()
    install_deps()
    check_vcredist(cfg)
    napcat_dir = deploy_napcat(cfg)
    write_napcat_config(cfg, napcat_dir)
    ensure_project_config(project_dir)
    write_bot_env(cfg, project_dir)
    write_start_bat(cfg, project_dir, napcat_dir)

    print()
    # 部署结束自动跑一遍自检
    log("自检", "部署完成，自动运行自检验证...")
    subprocess.run([sys.executable, str(SELF_CHECK)])
    print()
    print("=" * 50)
    print("✅ 部署完成！接下来：")
    print(f"  1. 双击『启动机器人.bat』")
    print(f"  2. 机器人QQ（{cfg['bot_qq']}）首次登录需扫码，之后免扫码")
    print(f"  3. 把机器人拉进群并设为管理员，发『菜单』开始使用")
    print("=" * 50)
    input("按回车键退出...")


if __name__ == "__main__":
    main()
