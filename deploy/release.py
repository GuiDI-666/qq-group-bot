"""版本发布工具：一条命令完成版本号更新、CHANGELOG 记录、提交打标（可选推送）。

用法：
  python deploy/release.py v1.3.0 "新增了xxx功能"
  python deploy/release.py v1.3.0 "说明" --no-push   # 只本地提交，不推远程

会自动：
  1. 更新 VERSION 文件
  2. 在 CHANGELOG.md 顶部插入带日期的模板条目（含你的说明）
  3. 仓库 commit 并打同名 tag（v1.6.0 起源码已并入本仓库，故单仓提交）
  4. 若配置了远程 origin，自动 push（含标签）
"""
import subprocess
import sys
from datetime import date
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
VERSION_FILE = BASE / "VERSION"
CHANGELOG = BASE / "CHANGELOG.md"
# 单一仓库：套件与机器人源码都在本目录下（QQ群管机器人/qq-group-bot/）
REPOS = [BASE]


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=check,
    )


def main() -> int:
    if len(sys.argv) < 3:
        print('用法: python deploy/release.py v1.x.x "版本说明"')
        return 1
    tag, message = sys.argv[1], sys.argv[2]
    no_push = "--no-push" in sys.argv[3:]
    if not tag.startswith("v"):
        print("tag 请以 v 开头，如 v1.3.0")
        return 1
    version = tag[1:]

    # 1. 更新 VERSION
    VERSION_FILE.write_text(version + "\n", encoding="utf-8")
    print(f"[1/4] VERSION -> {version}")

    # 2. CHANGELOG 顶部插入条目
    entry = (
        f"## [{tag}] - {date.today().isoformat()}\n\n"
        f"### 变更\n- {message}\n\n"
    )
    text = CHANGELOG.read_text(encoding="utf-8")
    marker = "---\n\n## ["
    if marker in text:
        idx = text.index(marker) + len("---\n\n")
        text = text[:idx] + entry + text[idx:]
    else:
        # 没有 v1.2.1 之前条目时，插在头部说明之后
        idx = text.index("---\n") + 4
        text = text[:idx] + "\n" + entry + text[idx:]
    CHANGELOG.write_text(text, encoding="utf-8")
    print(f"[2/4] CHANGELOG.md 已插入 {tag} 条目")

    # 3&4. 双仓提交 + 打标
    for repo in REPOS:
        if not (repo / ".git").exists():
            print(f"[3/4] 跳过 {repo}（不是 git 仓库）")
            continue
        git(repo, "add", "-A", check=False)
        r = git(repo, "commit", "-q", "-m", f"{tag}: {message}", check=False)
        if r.returncode != 0 and "nothing to commit" not in (r.stdout or ""):
            print(f"[3/4] {repo.name} 提交失败:\n{r.stdout}{r.stderr}")
            return 1
        # tag 已存在则先删除重建
        git(repo, "tag", "-d", tag, check=False)
        git(repo, "tag", "-a", tag, "-m", message)
        where = "源码仓库" if repo != BASE else "套件仓库"
        print(f"[3/4] {where} 已提交并打标 {tag}")

    print(f"[4/4] ✅ 版本 {tag} 发布完成！")
    if not no_push:
        remotes = (git(BASE, "remote", check=False).stdout or "").split()
        if "origin" in remotes:
            r = git(BASE, "push", "origin", "HEAD", "--follow-tags", check=False)
            if r.returncode == 0:
                print(f"[4/4] 已推送到远程 origin")
            else:
                print(f"[4/4] ⚠ 推送失败（本地提交已完成，可稍后手动 git push）：\n{(r.stdout or '')}{(r.stderr or '')}")
        else:
            print("  提示：未配置远程仓库，本次仅本地提交；配置后可用 git push 上传 GitHub")
    print("  提示：若改动涉及机器人代码，需重启『启动机器人.bat』生效")
    return 0


if __name__ == "__main__":
    sys.exit(main())
