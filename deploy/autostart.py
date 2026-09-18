"""开机自启管理：让『启动机器人.bat』在登录系统后自动运行（看门狗随之常驻）。

用法：
  python deploy/autostart.py install     # 开启开机自启
  python deploy/autostart.py uninstall   # 关闭开机自启
  python deploy/autostart.py status      # 查看当前状态

Windows 实现：在「启动」文件夹生成快捷方式（最小化窗口运行看门狗）。
  - 优点：不需要管理员权限；登录后自动拉起，日志在 logs/ 目录
  - 若想「开机即启（无需登录）」，用管理员身份执行（把 <项目目录> 换成实际路径）：
      schtasks /Create /TN "QQ群管机器人" /SC ONSTART /RU SYSTEM /RL HIGHEST ^
        /TR "python \"<项目目录>\\deploy\\watchdog.py\""
Linux 实现：生成 systemd 服务模板，按提示安装即可。
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
BAT = BASE / "启动机器人.bat"
IS_WIN = os.name == "nt"

STARTUP_DIR = Path(os.environ.get("APPDATA", "")) / (
    r"Microsoft\Windows\Start Menu\Programs\Startup"
)
LNK = STARTUP_DIR / "QQ群管机器人.lnk"
FALLBACK_CMD = STARTUP_DIR / "QQ群管机器人.cmd"
SERVICE = BASE / "deploy" / "qq-group-bot.service"


def info(msg: str) -> None:
    print(msg, flush=True)


# ---------------- Windows ----------------
def ps_shortcut_create() -> bool:
    """用 WScript.Shell 生成快捷方式（最小化窗口）。"""
    script = (
        "$ws = New-Object -ComObject WScript.Shell\n"
        f'$lnk = $ws.CreateShortcut("{LNK}")\n'
        f'$lnk.TargetPath = "{BAT}"\n'
        f'$lnk.WorkingDirectory = "{BASE}"\n'
        "$lnk.WindowStyle = 7\n"
        '$lnk.Description = "QQ群管机器人（看门狗守护）"\n'
        "$lnk.Save()\n"
    )
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True, text=True, timeout=60,
        )
        return r.returncode == 0 and LNK.exists()
    except Exception as e:
        info(f"  快捷方式创建失败：{e}")
        return False


def win_install() -> int:
    if not BAT.exists():
        info(f"❌ 找不到 {BAT}，请先运行『一键部署.bat』生成启动脚本")
        return 1
    STARTUP_DIR.mkdir(parents=True, exist_ok=True)

    if ps_shortcut_create():
        FALLBACK_CMD.unlink(missing_ok=True)
        info("✅ 已开启开机自启（登录后自动运行，窗口最小化）")
        info(f"   快捷方式：{LNK}")
    else:
        FALLBACK_CMD.write_text(
            "@echo off\n"
            f'start "" /min cmd /c "{BAT}"\n',
            encoding="gbk",
        )
        info("✅ 已开启开机自启（降级为 .cmd 方式）")
        info(f"   启动脚本：{FALLBACK_CMD}")
    info("   机器人会在你登录 Windows 后自动启动；日志见 logs/watchdog.log")
    return 0


def win_uninstall() -> int:
    removed = []
    for p in (LNK, FALLBACK_CMD):
        if p.exists():
            p.unlink()
            removed.append(str(p))
    info("✅ 已关闭开机自启" + (f"（已删除：{', '.join(removed)}）" if removed else "（原本就没开启）"))
    return 0


def win_status() -> int:
    found = [str(p) for p in (LNK, FALLBACK_CMD) if p.exists()]
    if found:
        info("开机自启：已开启")
        for f in found:
            info(f"  - {f}")
    else:
        info("开机自启：未开启")
        info("  开启命令：python deploy/autostart.py install")
    return 0


# ---------------- Linux（为后续部署到服务器准备） ----------------
SERVICE_TEMPLATE = """[Unit]
Description=QQ Group Bot (NapCat + NoneBot)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory={base}
ExecStart={python} {base}/deploy/watchdog.py
Restart=always
RestartSec=10
# 需要用有图形/QQ 运行库环境的用户账号，且与该用户下 NapCat 配置一致
User={user}

[Install]
WantedBy=multi-user.target
"""


def linux_install() -> int:
    SERVICE.write_text(
        SERVICE_TEMPLATE.format(base=BASE, python=sys.executable, user=os.environ.get("USER", "root")),
        encoding="utf-8",
    )
    info("✅ 已生成 systemd 服务模板：")
    info(f"   {SERVICE}")
    info("   安装步骤（需要 root）：")
    info(f"     sudo cp {SERVICE} /etc/systemd/system/qq-group-bot.service")
    info("     sudo systemctl daemon-reload")
    info("     sudo systemctl enable --now qq-group-bot")
    info("   查看状态：systemctl status qq-group-bot；日志：journalctl -u qq-group-bot -f")
    info("   注意：Linux 下需先按 NapCat 官方说明装好协议端，并确认 部署配置.json 里的 napcat_dir 正确")
    return 0


def linux_uninstall() -> int:
    SERVICE.unlink(missing_ok=True)
    info("✅ 已删除本地服务模板（若已安装到 /etc，请执行：sudo systemctl disable --now qq-group-bot）")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="QQ群管机器人 开机自启管理")
    parser.add_argument("action", choices=["install", "uninstall", "status"])
    args = parser.parse_args()

    if IS_WIN:
        return {"install": win_install, "uninstall": win_uninstall, "status": win_status}[args.action]()
    return {"install": linux_install, "uninstall": linux_uninstall, "status": linux_install}[args.action]()


if __name__ == "__main__":
    sys.exit(main())
