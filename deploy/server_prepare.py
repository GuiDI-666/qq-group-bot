"""把项目调整成「新机器 / 服务器」可运行的形态。

由项目根目录的『服务器部署.bat』调用，也可以单独运行：

    python deploy\\server_prepare.py                  # 体检 + 自动修正
    python deploy\\server_prepare.py --dry-run        # 只体检，不改任何文件
    python deploy\\server_prepare.py --encoding-only  # 只修 .bat 编码
    python deploy\\server_prepare.py D:\\path\\proj   # 指定项目根目录

做四件事
  1. 环境体检：Python / nonebot / 离线安装包 / 磁盘
  2. 修正 部署配置.json 里「属于别的机器」的路径（NapCat 目录、解释器）
  3. 处理从别的机器拷来的虚拟环境（venv 绑死了原机绝对路径，换机器即失效）
  4. 把 UTF-8 编码的 .bat 转成 GBK —— 否则 cmd 解析字节错位，双击直接闪退

完整报告会写到 <项目根目录>/deploy-report.txt（UTF-8），方便贴给维护者。
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

IS_WIN = os.name == "nt"
DEFAULT_ROOT = Path(__file__).resolve().parent.parent
SEP = "-" * 64

_LINES: list[str] = []
_REPORT_PATH: Path | None = None

# 允许的 Python 版本区间：3.9 ~ 3.13
# 不用 3.14：依赖清单里的 pydantic-core 等锁定版本还没齐全的 cp314 轮子，
# 硬装会退化成源码编译（需要 Rust），在服务器上基本必失败。
PY_OK_MIN, PY_OK_MAX = 9, 13


def say(msg: str = "") -> None:
    print(msg, flush=True)
    _LINES.append(msg)


def banner(title: str) -> None:
    say()
    say(SEP)
    say(f"  {title}")
    say(SEP)


def save_report() -> None:
    if _REPORT_PATH is None:
        return
    try:
        _REPORT_PATH.write_text("\n".join(_LINES) + "\n", encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        print(f"（写报告失败：{e}）")


# --------------------------------------------------------------------------- 1
def report_environment(root: Path) -> None:
    banner("1. 环境体检")
    say(f"  系统      : {platform.platform()}")
    say(f"  计算机名  : {platform.node()}")
    say(f"  Python    : {sys.version.split()[0]}    {sys.executable}")
    if IS_WIN:
        try:
            import ctypes

            admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:  # noqa: BLE001
            admin = False
        say(f"  管理员    : {'是' if admin else '否（通常不需要）'}")
    try:
        free = shutil.disk_usage(root).free / (1024**3)
        say(f"  磁盘剩余  : {free:.1f} GB")
    except Exception:  # noqa: BLE001
        pass
    say(f"  项目目录  : {root}")

    try:
        import nonebot

        say(f"  nonebot   : {nonebot.__version__} ✅")
    except Exception as e:  # noqa: BLE001
        say(f"  nonebot   : ❌ 未安装（{e}）")
    try:
        import nonebot.adapters.onebot.v11  # noqa: F401

        say("  OneBot适配: 已就绪 ✅")
    except Exception as e:  # noqa: BLE001
        say(f"  OneBot适配: ❌ {e}")


OFFLINE_ITEMS = [
    ("deps/NapCat.Shell.Windows.Node.zip", "NapCat 协议端离线包"),
    ("deps/requirements-full.txt", "依赖清单"),
    ("deps/crypto.dll", "crypto.dll"),
    ("deps/ssl.dll", "ssl.dll"),
    ("deps/vc_redist.x64.exe", "VC++ 运行库"),
]


def check_offline(root: Path) -> None:
    banner("2. 离线安装包检查")
    missing = []
    for rel, desc in OFFLINE_ITEMS:
        p = root / rel
        if p.exists():
            say(f"  ✅ {desc:20} {p.stat().st_size / 1048576:6.1f} MB")
        else:
            say(f"  ❌ {desc:20} 缺失：{rel}")
            missing.append(rel)
    if missing:
        say("  → 上面这些文件要从源机器一起拷过来，否则部署会中途失败")


def check_scripts(root: Path) -> None:
    banner("3. 部署脚本版本检查")
    deploy_py = root / "deploy" / "deploy.py"
    text = deploy_py.read_text(encoding="utf-8", errors="replace") if deploy_py.exists() else ""
    if "zip_top_layout" in text:
        say("  ✅ deploy.py 含 NapCat 布局修正（新机器可用）")
    else:
        say("  ❌ deploy.py 是旧版本，缺少 NapCat 布局修正")
        say("     新机器上协议端会被解压到错误层级，看门狗找不到 node.exe 就起不来")
        say("     → 请把源机器上的整个 deploy 文件夹拷过来覆盖")

    wd = root / "deploy" / "watchdog.py"
    wtext = wd.read_text(encoding="utf-8", errors="replace") if wd.exists() else ""
    if "def resolve_python" in wtext:
        say("  ✅ watchdog.py 含解释器自动探测")
    else:
        say("  ⚠ watchdog.py 是旧版本（不影响本次部署，但建议一并更新）")


# --------------------------------------------------------------------------- 4
def find_config(root: Path) -> Path | None:
    """定位 部署配置.json。仓库里同时有 .example 模板，用内容特征区分。"""
    for f in sorted(root.glob("*.json")):
        if "example" in f.name.lower():
            continue
        try:
            text = f.read_text(encoding="utf-8-sig")
        except Exception:  # noqa: BLE001
            continue
        if '"napcat_dir"' in text and '"bot_qq"' in text:
            return f
    return None


def patch_line(text: str, key: str, value) -> tuple[str, bool]:
    """按行替换 JSON 中某个键的值，尽量保留原文件的排版和说明性键。"""
    pattern = re.compile(
        r'^([ \t]*"' + re.escape(key) + r'"[ \t]*:[ \t]*)(.+?)([ \t]*,?)[ \t]*\r?$',
        re.MULTILINE,
    )
    m = pattern.search(text)
    if not m:
        return text, False
    new_line = m.group(1) + json.dumps(value, ensure_ascii=False) + m.group(3)
    if new_line == m.group(0):
        return text, False
    return text[: m.start()] + new_line + text[m.end():], True


def default_napcat_root() -> Path:
    """协议端默认目录：放盘符根目录，避开中文/空格路径给 QQ 内核添麻烦。"""
    if IS_WIN:
        return Path("C:/NapCat")
    return Path.home() / "NapCat"


def path_usable(raw) -> bool:
    raw = (raw or "").strip()
    if not raw:
        return False
    try:
        p = Path(raw)
        return p.is_absolute() and p.parent.exists()
    except OSError:
        return False


def fix_config(root: Path, dry: bool) -> None:
    banner("4. 修正机器相关配置（部署配置.json）")
    cfg_file = find_config(root)
    if cfg_file is None:
        say("  ⚠ 没找到 部署配置.json（也没有可用模板）")
        say("    → 部署脚本会从 example 自动生成一份，之后请填好再跑一次")
        return
    say(f"  配置文件   : {cfg_file.name}")

    text = cfg_file.read_text(encoding="utf-8-sig")
    try:
        cfg = json.loads(text)
    except Exception as e:  # noqa: BLE001
        say(f"  ❌ 解析失败，跳过：{e}")
        return

    wanted: dict = {}

    raw = cfg.get("napcat_dir")
    if path_usable(raw):
        say(f"  ✅ napcat_dir 在本机可用：{raw}")
    else:
        wanted["napcat_dir"] = str(default_napcat_root())
        say(f"  ✏ napcat_dir 指向别的机器：{raw!r}")
        say(f"    → 改用本机默认：{wanted['napcat_dir']}")

    proj = (cfg.get("project_dir") or "").strip()
    if proj and not (Path(proj) / "bot.py").exists():
        wanted["project_dir"] = ""
        say(f"  ✏ project_dir 在本机不存在：{proj!r} → 改回项目内的 qq-group-bot")

    py = (cfg.get("python_path") or "").strip()
    if (not py) or (not Path(py).exists()):
        wanted["python_path"] = sys.executable
        say(f"  ✏ python_path → {sys.executable}")

    if not wanted:
        say("  无需修改")
        return

    patched = text
    failed = []
    for key, value in wanted.items():
        patched, ok = patch_line(patched, key, value)
        if not ok:
            failed.append(key)
    if failed:
        cfg.update(wanted)
        patched = json.dumps(cfg, ensure_ascii=False, indent=2) + "\n"
        say(f"  （{', '.join(failed)} 使用整文件重写）")

    if dry:
        say("  [dry-run] 未写入文件")
        return
    cfg_file.write_text(patched, encoding="utf-8")
    say(f"  ✅ 已写回 {cfg_file.name}")
    for key, value in wanted.items():
        say(f"       {key} = {value}")


# --------------------------------------------------------------------------- 5
def _runs(exe: Path) -> bool:
    try:
        r = subprocess.run([str(exe), "-c", "print(1)"], capture_output=True, timeout=40)
        return r.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def handle_venv(root: Path, dry: bool) -> None:
    banner("5. 检查虚拟环境是否属于本机")
    found = False
    for name in ("venv", ".venv"):
        vdir = root / name
        exe = vdir / "Scripts" / "python.exe" if IS_WIN else vdir / "bin" / "python"
        if not exe.exists():
            continue
        found = True
        if _runs(exe):
            say(f"  ✅ {name}/ 在本机可用，保持不动")
            continue
        say(f"  ✏ {name}/ 绑定的还是原机器的 Python 路径，本机跑不起来")
        if dry:
            say(f"    [dry-run] 将重命名为 {name}.machine-bound")
            continue
        target = root / f"{name}.machine-bound"
        try:
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
            vdir.rename(target)
            say(f"    → 已重命名为 {target.name}（纯缓存，可随时删除）")
        except Exception as e:  # noqa: BLE001
            say(f"    ⚠ 重命名失败：{e}（不影响部署）")
    if not found:
        say("  （没有虚拟环境，跳过）")


# --------------------------------------------------------------------------- 6
def fix_bat_encoding(root: Path, dry: bool) -> list[str]:
    """UTF-8 中文 .bat + chcp 65001 → cmd 解析错位 → 双击闪退，这里统一转 GBK。"""
    changed: list[str] = []
    for f in sorted(root.glob("*.bat")):
        raw = f.read_bytes()
        if not any(b > 127 for b in raw):
            continue  # 纯 ASCII，不会触发错位
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue  # 已经是 GBK/ANSI
        fixed = text.replace("chcp 65001", "chcp 936").replace("\r\n", "\n")
        data = fixed.replace("\n", "\r\n").encode("gbk", errors="replace")
        if data == raw:
            continue
        changed.append(f.name)
        if not dry:
            f.write_bytes(data)
    return changed


def report_bat_encoding(root: Path, dry: bool) -> None:
    banner("6. 修正 .bat 编码（防双击闪退）")
    changed = fix_bat_encoding(root, dry)
    if changed:
        for n in changed:
            say(f"  ✏ 已转为 GBK：{n}")
        say("  这些文件原本是 UTF-8 + chcp 65001，cmd 会解析字节错位，双击一闪就没")
    else:
        say("  ✅ 所有 .bat 编码正常，无需处理")


def summary(root: Path) -> None:
    banner("准备完成 · 下一步")
    say("  1. 上面若有 ❌，先按提示补齐文件再继续")
    say("  2. 双击『一键部署.bat』完成部署（解压协议端 + 写配置 + 自检）")
    say("  3. 双击『启动机器人.bat』常驻运行；首次登录需要用手机 QQ 扫码")
    say()
    say(f"  详细报告：{root / 'deploy-report.txt'}")


def main() -> int:
    global _REPORT_PATH

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:  # noqa: BLE001
            pass

    ap = argparse.ArgumentParser(description="服务器/新机器部署准备")
    ap.add_argument("root", nargs="?", default=None, help="项目根目录（默认 = deploy 的上一级）")
    ap.add_argument("--dry-run", action="store_true", help="只体检，不改任何文件")
    ap.add_argument("--encoding-only", action="store_true", help="只修正 .bat 编码")
    ap.add_argument("--no-report", action="store_true", help="不写 deploy-report.txt")
    args = ap.parse_args()

    root = Path(args.root).expanduser().resolve() if args.root else DEFAULT_ROOT
    # dry-run 承诺"不改任何文件"，所以连报告也不落盘
    if not args.no_report and not args.encoding_only and not args.dry_run:
        _REPORT_PATH = root / "deploy-report.txt"

    if args.encoding_only:
        report_bat_encoding(root, args.dry_run)
        _LINES.clear()
        return 0

    say("=" * 64)
    say("  QQ群管机器人 · 服务器 / 新机器部署准备")
    say("=" * 64)
    report_environment(root)
    check_offline(root)
    check_scripts(root)
    fix_config(root, args.dry_run)
    handle_venv(root, args.dry_run)
    report_bat_encoding(root, args.dry_run)
    summary(root)
    save_report()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
