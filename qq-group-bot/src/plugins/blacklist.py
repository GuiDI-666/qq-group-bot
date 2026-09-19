"""群黑名单：拉黑后自动踢出、拒绝其入群申请、进群即踢。

命令：
  拉黑 @某人 或 QQ号 [原因]  —— 加入黑名单并踢出本群
  解除拉黑 @某人 或 QQ号      —— 移出黑名单
  黑名单                      —— 查看当前黑名单
"""
import json
import re
from pathlib import Path

from nonebot import on_command, on_notice
from nonebot.adapters.onebot.v11 import (
    GROUP_ADMIN,
    GROUP_OWNER,
    Bot,
    GroupIncreaseNoticeEvent,
    GroupMessageEvent,
    Message,
    MessageEvent,
)
from nonebot.adapters.onebot.v11.exception import ActionFailed
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER

from .admin import COMMON_PERM, _extract_target_and_duration, _fail_reason, _mention
from .common import managed_group

BLACKLIST_PATH = Path(__file__).resolve().parents[2] / "data" / "blacklist.json"


def _load() -> dict:
    """返回 {str(qq): reason}"""
    if not BLACKLIST_PATH.exists():
        return {}
    try:
        with open(BLACKLIST_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save(data: dict) -> None:
    BLACKLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(BLACKLIST_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def is_blacklisted(user_id: int) -> bool:
    return str(user_id) in _load()


async def _kick_if_member(bot: Bot, group_id: int, user_id: int) -> str:
    """若目标在本群且非管理员/群主，则踢出。返回动作结果描述。"""
    try:
        info = await bot.get_group_member_info(
            group_id=group_id, user_id=user_id, no_cache=True
        )
    except ActionFailed:
        return "不在本群"
    if info.get("role") in ("admin", "owner"):
        return "在群内但是管理员，未踢出"
    try:
        await bot.set_group_kick(
            group_id=group_id, user_id=user_id, reject_add_request=True
        )
        return "已踢出"
    except ActionFailed as e:
        return f"踢出失败：{_fail_reason(e)}"


def _extract_target(args: Message) -> int | None:
    target, _ = _extract_target_and_duration(args, default_minutes=10)
    return target


# ---------------- 拉黑 ----------------
black = on_command("拉黑", rule=managed_group(), permission=COMMON_PERM, priority=5, block=True)


@black.handle()
async def handle_black(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    if not isinstance(event, GroupMessageEvent):
        await black.finish("该命令仅在群聊中可用")
    target = _extract_target(args)
    if target is None:
        await black.finish("用法：/拉黑 @某人 或 QQ号 [原因]\n例：/拉黑 123456 发广告")
    if target == event.self_id:
        await black.finish("不能拉黑我自己🥲")
    # 原因 = 去掉目标数字/@后剩下的文本
    reason = re.sub(r"\[CQ:at[^\]]*\]", "", str(args)).strip()
    reason = re.sub(r"\d{5,11}", "", reason).strip() or "未填写"

    data = _load()
    already = str(target) in data
    data[str(target)] = reason
    _save(data)

    result = await _kick_if_member(bot, event.group_id, target)
    tip = "（已在黑名单中，原因已更新）" if already else ""
    await black.finish(
        f"已将 {_mention(target)} 加入黑名单{tip}\n原因：{reason}\n本群状态：{result}"
    )


# ---------------- 解除拉黑 ----------------
unblack = on_command("解除拉黑", aliases={"解黑", "移出黑名单"}, rule=managed_group(), permission=COMMON_PERM, priority=5, block=True)


@unblack.handle()
async def handle_unblack(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    if not isinstance(event, GroupMessageEvent):
        await unblack.finish("该命令仅在群聊中可用")
    target = _extract_target(args)
    if target is None:
        await unblack.finish("用法：/解除拉黑 @某人 或 QQ号")
    data = _load()
    if str(target) not in data:
        await unblack.finish(f"{_mention(target)} 不在黑名单中")
    del data[str(target)]
    _save(data)
    await unblack.finish(f"已将 {_mention(target)} 移出黑名单")


# ---------------- 查看黑名单 ----------------
showblack = on_command("黑名单", aliases={"拉黑列表"}, rule=managed_group(), permission=COMMON_PERM, priority=5, block=True)


@showblack.handle()
async def handle_showblack(bot: Bot, event: MessageEvent):
    data = _load()
    if not data:
        await showblack.finish("黑名单目前是空的")
    lines = [f"黑名单共 {len(data)} 人："]
    for qq, reason in list(data.items())[:30]:
        lines.append(f"- {qq}（{reason}）")
    if len(data) > 30:
        lines.append(f"...等共 {len(data)} 人")
    await showblack.finish("\n".join(lines))


# ---------------- 黑名单用户进群自动踢出 ----------------
group_increase = on_notice(priority=2, block=False, rule=managed_group())


@group_increase.handle()
async def handle_group_increase(bot: Bot, event: GroupIncreaseNoticeEvent):
    if not is_blacklisted(event.user_id):
        return
    try:
        await bot.set_group_kick(
            group_id=event.group_id, user_id=event.user_id, reject_add_request=True
        )
        await bot.send_group_msg(
            group_id=event.group_id,
            message=f"[CQ:at,qq={event.user_id}] 在黑名单中，已自动移出群聊",
        )
    except ActionFailed as e:
        from nonebot import logger

        logger.warning(f"黑名单用户 {event.user_id} 踢出失败: {_fail_reason(e)}")
