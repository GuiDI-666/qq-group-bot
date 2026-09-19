import re

from nonebot import on_command
from nonebot.adapters.onebot.v11 import (
    GROUP_ADMIN,
    GROUP_OWNER,
    Bot,
    GroupMessageEvent,
    Message,
    MessageEvent,
)
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER

from nonebot.adapters.onebot.v11.exception import ActionFailed

from .common import managed_group

COMMON_PERM = SUPERUSER | GROUP_ADMIN | GROUP_OWNER


def _fail_reason(e: ActionFailed) -> str:
    info = getattr(e, "info", None)
    wording = ""
    if isinstance(info, dict):
        wording = info.get("wording") or info.get("message") or ""
    if not wording:
        wording = str(getattr(e, "message", e))
    hint = ""
    err = str(e)
    if "cannot ban admin" in err:
        hint = "（对方是群管理员/群主，无法禁言）"
    elif "not group admin" in err or "no right" in err:
        hint = "（机器人还不是本群管理员，请先给小号设管理）"
    elif "not member" in err or "not found" in err:
        hint = "（对方不在本群）"
    return f"操作失败：{wording}{hint}"


def _mention(target: int) -> str:
    return f"[CQ:at,qq={target}]"


def _extract_target_and_duration(
    args: Message, default_minutes: int
) -> tuple[int | None, int]:
    """从参数中提取目标用户（支持 @某人 或裸QQ号）和时长（分钟）。

    示例：禁言 @张三 30 / 禁言 1000123456 30 / 解禁 1000123456
    """
    target = None
    duration = default_minutes
    for seg in args:
        if seg.type == "at":
            target = int(seg.data.get("qq", 0)) or None
        elif seg.type == "text":
            for token in re.findall(r"\d+", seg.data.get("text", "")):
                if 5 <= len(token) <= 11 and target is None:
                    target = int(token)  # 5-11位数字视为QQ号
                elif 1 <= len(token) <= 4:
                    duration = int(token)  # 1-4位数字视为时长(分钟)
    return target, duration


mute = on_command("禁言", rule=managed_group(), permission=COMMON_PERM, priority=5, block=True)
unmute = on_command("解禁", rule=managed_group(), permission=COMMON_PERM, priority=5, block=True)
kick = on_command("踢出", rule=managed_group(), permission=COMMON_PERM, priority=5, block=True)


@mute.handle()
async def handle_mute(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    if not isinstance(event, GroupMessageEvent):
        await mute.finish("该命令仅在群聊中可用")
    target, minutes = _extract_target_and_duration(args, default_minutes=10)
    if target is None:
        await mute.finish("用法：/禁言 @某人 或 QQ号 [分钟数，默认10]\n例：/禁言 1000123456 30")
    if target == event.self_id:
        await mute.finish("不能禁言我自己🥲")
    if minutes <= 0 or minutes > 43200:
        await mute.finish("分钟数需在 1~43200 之间（最长30天）")
    try:
        await bot.set_group_ban(
            group_id=event.group_id, user_id=target, duration=minutes * 60
        )
    except ActionFailed as e:
        await mute.finish(_fail_reason(e))
    await mute.finish(f"已将 {_mention(target)} 禁言 {minutes} 分钟")


@unmute.handle()
async def handle_unmute(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    if not isinstance(event, GroupMessageEvent):
        await unmute.finish("该命令仅在群聊中可用")
    target, _ = _extract_target_and_duration(args, default_minutes=10)
    if target is None:
        await unmute.finish("用法：/解禁 @某人 或 QQ号")
    try:
        await bot.set_group_ban(group_id=event.group_id, user_id=target, duration=0)
    except ActionFailed as e:
        await unmute.finish(_fail_reason(e))
    await unmute.finish(f"已解除 {_mention(target)} 的禁言")


@kick.handle()
async def handle_kick(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    if not isinstance(event, GroupMessageEvent):
        await kick.finish("该命令仅在群聊中可用")
    # 踢人仅允许群主/超级管理员
    if not (await (SUPERUSER)(bot, event) or event.sender.role == "owner"):
        await kick.finish("只有群主可以踢人")
    target, _ = _extract_target_and_duration(args, default_minutes=10)
    if target is None:
        await kick.finish("用法：/踢出 @某人 或 QQ号")
    try:
        await bot.set_group_kick(group_id=event.group_id, user_id=target)
    except ActionFailed as e:
        await kick.finish(_fail_reason(e))
    await kick.finish("已将该用户移出群聊")
