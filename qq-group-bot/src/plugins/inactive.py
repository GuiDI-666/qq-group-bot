"""潜水成员查询：按 30 / 60 / 90 天拉取长期未发言的群成员名单。

命令（管理员/群主/超级管理员可用）：
  /未发言            —— 默认 30 天
  /未发言 60         —— 60 天内没发过言
  /未发言 90         —— 90 天内没发过言
  /未发言 30 天      —— 支持带"天"字
  私聊用法：/未发言 群号 30

统计口径：
  - 数据来自协议端 get_group_member_list 的 last_sent_time（QQ 记录的最近发言时间）
  - 入群时间不足 N 天的成员单独统计（他们没机会在此周期内发言）
  - 机器人自己不计入
  - 人数较多时用合并转发发送，并同时在 data/reports/ 存档一份完整名单
"""
import re
import time
from pathlib import Path

import nonebot
from nonebot import on_command
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    Message,
    MessageSegment,
    MessageEvent,
)
from nonebot.adapters.onebot.v11.exception import ActionFailed
from nonebot.params import CommandArg

from .admin import COMMON_PERM
from .common import managed_group

logger = nonebot.logger

BASE = Path(__file__).resolve().parents[2]
REPORT_DIR = BASE / "data" / "reports"

DEFAULT_DAYS = 30
ALLOWED_DAYS = (30, 60, 90)
FORWARD_THRESHOLD = 15  # 超过这么多人就用合并转发
NODE_CHUNK = 40         # 每个转发节点放多少条
TEXT_LIMIT = 50         # 降级为纯文本时最多列多少人

inactive = on_command(
    "未发言",
    aliases={"未发言列表", "潜水", "潜水名单", "拉取未发言"},
    rule=managed_group(),
    permission=COMMON_PERM,
    priority=5,
    block=True,
)


def _parse_target(event: MessageEvent, args: Message) -> tuple[int | None, int, str]:
    """解析参数 -> (群号, 天数, 错误提示)。"""
    text = args.extract_plain_text()
    gid = event.group_id if isinstance(event, GroupMessageEvent) else None
    days = None
    for tok in re.findall(r"\d+", text):
        if len(tok) >= 5:
            # 5 位以上视为群号：私聊里必填；群聊里忽略（防止在 A 群查 B 群）
            if gid is None:
                gid = int(tok)
        else:
            days = int(tok)

    if gid is None:
        return None, 0, "私聊用法：/未发言 群号 [天数]\n例：/未发言 123456789 30"
    if days is None:
        days = DEFAULT_DAYS
    if days not in ALLOWED_DAYS:
        return None, 0, "天数仅支持 30 / 60 / 90\n例：未发言 30"
    return gid, days, ""


def _fmt_last(ts: int) -> str:
    if not ts:
        return "从未发言"
    gap = max(0, int((time.time() - ts) // 86400))
    return f"{time.strftime('%Y-%m-%d', time.localtime(ts))}（{gap}天前）"


def _role_tag(role: str) -> str:
    return {"owner": " 群主", "admin": " 管理员"}.get(role, "")


async def _collect(bot: Bot, gid: int, days: int) -> tuple[list, list, int]:
    """返回 (长期未发言, 新人未发言, 群成员总数)。"""
    members = await bot.get_group_member_list(group_id=gid)
    cutoff = time.time() - days * 86400
    silent: list[tuple[int, str, int, str]] = []
    newcomers: list[tuple[int, str, int, str]] = []

    for m in members:
        uid = int(m.get("user_id") or 0)
        if not uid or uid == int(bot.self_id):
            continue
        last = int(m.get("last_sent_time") or 0)
        join = int(m.get("join_time") or 0)
        if last > cutoff:
            continue
        name = str(m.get("card") or m.get("nickname") or uid)
        item = (uid, name, last, str(m.get("role") or "member"))
        if join > cutoff:
            newcomers.append(item)
        else:
            silent.append(item)

    silent.sort(key=lambda x: (x[2], x[0]))
    newcomers.sort(key=lambda x: (x[2], x[0]))
    return silent, newcomers, len(members)


def _entry_lines(items: list) -> list[str]:
    lines = []
    for idx, (uid, name, last, role) in enumerate(items, start=1):
        lines.append(f"{idx}. {name}({uid}){_role_tag(role)} · 最后发言 {_fmt_last(last)}")
    return lines


def _write_report(gid: int, days: int, header: str, body: str) -> Path | None:
    try:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M")
        path = REPORT_DIR / f"未发言_{gid}_{days}天_{stamp}.txt"
        path.write_text(f"{header}\n\n{body}\n", encoding="utf-8")
        return path
    except Exception as e:  # 落盘失败不影响回复
        logger.warning(f"未发言名单落盘失败: {e}")
        return None


@inactive.handle()
async def handle_inactive(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    gid, days, err = _parse_target(event, args)
    if err:
        await inactive.finish(err)

    try:
        silent, newcomers, total = await _collect(bot, gid, days)
    except ActionFailed as e:
        await inactive.finish(f"拉取群成员失败：{e}\n（机器人是否还在群 {gid} 里？）")

    group_name = ""
    try:
        info = await bot.get_group_info(group_id=gid, no_cache=False)
        group_name = f"（{info.get('group_name', '')}）"
    except Exception:
        pass

    header = (
        f"📋 未发言名单｜群 {gid}{group_name}\n"
        f"口径：超过 {days} 天未发言（含从未发言），机器人自身不计入"
    )
    footer = f"\n共 {len(silent)} 人未发言超过 {days} 天（群成员 {total} 人）"
    if newcomers:
        footer += f"，另有 {len(newcomers)} 位入群不足 {days} 天未纳入统计"
    if any(x[3] in ("owner", "admin") for x in silent):
        footer += "\n⚠️ 名单中含管理员/群主，请确认后再处理"

    if not silent:
        await inactive.finish(f"{header}\n\n🎉 没有发现长期未发言成员{footer}")

    entries = _entry_lines(silent)
    report_path = _write_report(gid, days, header, "\n".join(entries) + footer)
    archive = f"\n完整名单已存档：{report_path}" if report_path else ""

    # 人少直接发文本；人多用合并转发，避免刷屏
    if len(silent) <= FORWARD_THRESHOLD:
        await inactive.finish(header + "\n" + "\n".join(entries) + footer + archive)

    nodes = []
    chunks = [entries[i : i + NODE_CHUNK] for i in range(0, len(entries), NODE_CHUNK)]
    for i, chunk in enumerate(chunks, start=1):
        content = f"未发言 {days} 天 名单 {i}/{len(chunks)}\n" + "\n".join(chunk)
        nodes.append(
            MessageSegment.node_custom(
                user_id=int(bot.self_id), nickname="潜水名单", content=content
            )
        )

    is_group = isinstance(event, GroupMessageEvent)
    kwargs = {"group_id": gid, "messages": nodes} if is_group else {"user_id": event.user_id, "messages": nodes}
    api = "send_group_forward_msg" if is_group else "send_private_forward_msg"
    try:
        await bot.call_api(api, **kwargs)
        await inactive.finish(header + footer + archive)
    except Exception as e:  # 转发失败则降级为纯文本
        logger.warning(f"合并转发发送失败，降级为文本: {e}")
        text = (
            header
            + "\n"
            + "\n".join(entries[:TEXT_LIMIT])
            + f"\n...（仅显示前 {TEXT_LIMIT} 人）"
            + footer
            + archive
        )
        await inactive.finish(text)
