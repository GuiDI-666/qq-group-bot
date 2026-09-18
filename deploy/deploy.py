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


def install_deps() -> None:
    req = DEPS / "requirements-full.txt"
    log("2/7", "安装 Python 依赖（首次安装约1-2分钟）...")
    r = subprocess.run([sys.executable, "-m", "pip", "install", "-r", str(req), "-q"])
    if r.returncode != 0:
        print("❌ 依赖安装失败，请检查网络后重试")
        input("按回车键退出...")
        sys.exit(1)
    log("2/7", "依赖安装完成")


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
        log("4/7", f"解压 NapCat 到 {napcat_root} （约30秒）...")
        napcat_root.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(napcat_root)

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
        "# 命令前缀：允许直接发裸命令（如「禁言 @某人」），也支持「/禁言」。删除会导致裸命令失效\n"
        'COMMAND_START=["", "/"]\n'
    )
    if keep:
        env += "\n# ---- 以下为保留的自定义配置 ----\n" + "\n".join(keep) + "\n"
    with open(env_path, "w", encoding="utf-8") as f:
        f.write(env)
    log("6/7", f"已写入机器人配置 {env_path}（SUPERUSERS={cfg['superusers']}）")


def write_start_bat(cfg: dict, project_dir: Path, napcat_dir: Path) -> None:
    bat = BASE / "启动机器人.bat"
    # 用 %~dp0 自动定位脚本所在目录，避免把本机绝对路径写进仓库
    content = """@echo off
chcp 65001 >nul
title QQ群管机器人 一键启动（看门狗模式）
cd /d "%~dp0"
echo ==========================================
echo   QQ群管机器人 一键启动
echo   看门狗守护：协议端(NapCat) + 机器人(NoneBot)
echo   登录失效会自动重新登录，进程掉线会自动拉起
echo ==========================================
echo.
echo 机器人账号 / 端口 / 管理群：见 部署配置.json 与 qq-group-bot\\config.json
echo 关闭本窗口即停止守护（协议端与机器人会一并退出）
echo.

python "%~dp0deploy\\watchdog.py"

echo.
echo 看门狗已退出。按任意键关闭窗口。
pause >nul
"""
    with open(bat, "w", encoding="utf-8") as f:
        f.write(content)
    log("7/7", f"已生成启动脚本 {bat}（看门狗守护模式）")


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
