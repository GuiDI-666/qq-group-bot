"""QQ群管机器人部署自检脚本。

逐项检查 部署完整性，全部通过才判定可以运行。
用法：python self_check.py  （退出码 0=全部通过，1=存在问题）
"""
import json
import socket
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent  # QQ群管机器人/
DEPS = BASE / "deps"

results: list[tuple[bool, str, str]] = []  # (ok, name, detail)


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((ok, name, detail))
    mark = "✅" if ok else "❌"
    print(f" {mark} {name}" + (f" —— {detail}" if detail else ""))
    return ok


def main() -> int:
    print("=" * 56)
    print("  QQ群管机器人 · 部署自检")
    print("=" * 56)

    # ---- 1. Python 版本 ----
    v = sys.version_info
    check(
        "Python 版本 >= 3.9",
        v >= (3, 9),
        f"当前 {v.major}.{v.minor}.{v.micro}",
    )

    # ---- 2. 核心依赖可导入 ----
    missing = []
    for mod in ("nonebot", "nonebot.adapters.onebot.v11", "uvicorn", "fastapi"):
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod.split(".")[0])
    check(
        "Python 依赖安装完整",
        not missing,
        f"缺少: {', '.join(missing)}（运行 一键部署.bat 可补装）" if missing else "",
    )

    # ---- 3. 项目文件完整 ----
    required_files = [
        "bot.py",
        "config.json",
        ".env",
        "requirements.txt",
        "src/plugins/admin.py",
        "src/plugins/blacklist.py",
        "src/plugins/approval.py",
        "src/plugins/guard.py",
        "src/plugins/welcome.py",
        "src/plugins/settings.py",
        "src/plugins/status.py",
        "src/plugins/help.py",
        "src/plugins/common.py",
    ]
    proj = BASE / "qq-group-bot"
    absent = [f for f in required_files if not (proj / f).exists()]
    check(
        "项目文件完整（13个必需文件）",
        not absent,
        f"缺失: {', '.join(absent)}" if absent else "",
    )

    # ---- 4. config.json 可解析 ----
    cfg_ok = True
    detail = ""
    try:
        with open(proj / "config.json", "r", encoding="utf-8") as f:
            cfg = json.load(f)
        for key in ("welcome", "farewell", "auto_approve", "ad_patterns"):
            if key not in cfg:
                cfg_ok = False
                detail = f"缺少配置项 {key}"
    except Exception as e:
        cfg_ok = False
        detail = f"解析失败: {e}"
    check("config.json 格式正确", cfg_ok, detail)

    # ---- 5. .env 配置 ----
    env_ok, detail = False, ""
    env_path = proj / ".env"
    if env_path.exists():
        content = env_path.read_text(encoding="utf-8")
        if "SUPERUSERS" in content and "10000" not in content:
            env_ok = True
        else:
            detail = "SUPERUSERS 未配置（还是占位值或缺失）"
    else:
        detail = ".env 文件不存在"
    check("机器人 .env 配置（超级管理员已设置）", env_ok, detail)

    # ---- 6. 插件可加载 ----
    r = subprocess.run(
        [sys.executable, "-c", "import bot"],
        cwd=proj,
        capture_output=True,
        text=True,
        timeout=120,
    )
    fails = [ln for ln in (r.stderr or "").splitlines() if "Failed to import" in ln]
    check(
        "机器人插件全部可加载",
        r.returncode == 0 and not fails,
        fails[0] if fails else (r.stderr.strip().splitlines()[-1] if r.returncode else ""),
    )

    # ---- 7. NapCat 部署 ----
    dep_cfg = {}
    try:
        with open(BASE / "部署配置.json", "r", encoding="utf-8") as f:
            dep_cfg = json.load(f)
    except Exception:
        pass
    napcat_root = dep_cfg.get("napcat_dir") or (Path.home() / "NapCat")
    napcat_dir = Path(napcat_root) / "NapCat"
    nap_files = ["index.js", "node.exe", "wrapper.node", "crypto.dll", "ssl.dll"]
    absent = [f for f in nap_files if not (napcat_dir / f).exists()]
    check(
        "NapCat 部署完整（含QQ内核依赖DLL）",
        not absent,
        f"缺失: {', '.join(absent)}" if absent else str(napcat_dir),
    )

    # ---- 8. NapCat 网络配置 ----
    bot_qq = str(dep_cfg.get("bot_qq", ""))
    onebot_cfg = napcat_dir / "napcat" / "config" / f"onebot11_{bot_qq}.json"
    ws_ok, detail = False, ""
    if onebot_cfg.exists():
        try:
            data = json.loads(onebot_cfg.read_text(encoding="utf-8"))
            clients = data.get("network", {}).get("websocketClients", []) or \
                data.get("network", {}).get("reverseWebSocketClients", [])
            ws_url = dep_cfg.get("ws_url", "ws://127.0.0.1:8080/onebot/v11/ws")
            ws_ok = any(c.get("enable") and c.get("url") == ws_url for c in clients)
            detail = "" if ws_ok else f"未发现指向 {ws_url} 的反向WS配置"
        except Exception as e:
            detail = f"解析失败: {e}"
    else:
        detail = f"配置文件不存在（{onebot_cfg.name}），运行一键部署生成"
    check("NapCat 反向 WebSocket 配置", ws_ok, detail)

    # ---- 9. 运行状态（非必需，仅提示） ----
    port = int(dep_cfg.get("bot_port", 8080))
    running = False
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            running = True
    except OSError:
        pass
    check(
        f"机器人服务运行中（端口 {port}）",
        True,
        "🟢 正在运行" if running else "⚪ 未运行（部署类自检不要求，双击 启动机器人.bat 启动）",
    )

    # ---- 汇总 ----
    failed = [r for r in results if not r[0]]
    print("-" * 56)
    if not failed:
        print("✅ 自检通过：部署完整，可以运行（启动 = 双击 启动机器人.bat）")
        return 0
    print(f"❌ {len(failed)} 项未通过，请按上方提示处理后重新自检：")
    for _, name, detail in failed:
        print(f"   - {name}" + (f"（{detail}）" if detail else ""))
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(1)
