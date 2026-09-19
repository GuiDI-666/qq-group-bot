"""静默模式（仅私聊，管理员可用）：让机器人临时全局停摆。

用途：
  - 账号被腾讯风控、需要"静置"降权时，让机器人一段时间内不产生任何群行为
  - 维护/调试期间临时关掉所有群功能，不必退出进程

静默期内：所有群事件（命令、入群审批、广告拦截、欢迎/退群提示、黑名单自动处理）
         全部不响应；私聊管理命令仍可用（否则无法解除静默）。
         看门狗也会读取同一状态，暂停心跳自检。

命令（私聊，超级管理员或 config.json 的 admin_users）：
  静默 2h           —— 静默 2 小时（支持 h/小时、m/分/分钟、s/秒，默认单位=分钟）
  静默 90           —— 静默 90 分钟
  静默 状态          —— 查看当前静默状态与剩余时间
  静默 off / 取消静默 —— 立即解除静默
"""
import re

from nonebot import on_command
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageEvent
from nonebot.params import CommandArg

from .common import (
    clear_silence,
    format_duration,
    is_silenced,
    set_silence,
    silence_remaining,
)

MAX_MINUTES = 72 * 60  # 上限 72 小时，防止误操作长期静默

_UNIT_MINUTES = {
    "h": 60, "hr": 60, "hrs": 60, "hour": 60, "hours": 60, "小时": 60, "时": 60,
    "m": 1, "min": 1, "mins": 1, "minute": 1, "minutes": 1, "分": 1, "分钟": 1,
    "s": 1 / 60, "sec": 1 / 60, "secs": 1 / 60, "秒": 1 / 60,
}


def _is_authorized(user_id: int) -> bool:
    """授权范围：.env 超级管理员 + config.json 的 admin_users。"""
    from .settings import _is_authorized as settings_auth

    return settings_auth(user_id)


def _parse_minutes(text: str) -> float | None:
    """解析时长文本，如 '2h' '90' '1小时30分' '30秒'；解析不出返回 None。"""
    text = text.strip().lower().replace(" ", "")
    if not text:
        return None
    total = 0.0
    matched = False
    for num, unit in re.findall(r"(\d+(?:\.\d+)?)([a-z\u4e00-\u9fa5]*)", text):
        matched = True
        minutes = _UNIT_MINUTES.get(unit)
        if minutes is None:
            minutes = 1 if unit == "" else None  # 无单位按分钟
        if minutes is None:
            return None
        total += float(num) * minutes
    if not matched or total <= 0:
        return None
    return total


silence = on_command("静默", aliases={"silence"}, priority=5, block=True)
unsilence = on_command("取消静默", aliases={"解除静默"}, priority=5, block=True)


@silence.handle()
async def handle_silence(event: MessageEvent, args: Message = CommandArg()):
    if isinstance(event, GroupMessageEvent):
        return  # 仅私聊可用，群聊内不响应
    if not _is_authorized(event.user_id):
        return  # 未授权私聊，静默

    text = args.extract_plain_text().strip()
    if text in ("状态", "status", "查询", ""):
        if not is_silenced():
            await silence.finish("当前未静默。\n用法：/静默 2h（支持 h/小时、m/分钟、s/秒，默认分钟）")
        await silence.finish(
            f"🔇 静默中，剩余 {format_duration(silence_remaining())}\n"
            "解除方式：私聊发『/静默 off』或『/取消静默』"
        )
    if text in ("off", "关闭", "停止", "解除", "取消"):
        clear_silence()
        await silence.finish("🔊 已解除静默，群功能恢复正常")
        return

    minutes = _parse_minutes(text)
    if minutes is None:
        await silence.finish(
            "用法：/静默 <时长>\n"
            "  静默 2h      —— 静默 2 小时\n"
            "  静默 90      —— 静默 90 分钟（无单位默认分钟）\n"
            "  静默 1小时30分 —— 组合写法\n"
            "  静默 状态     —— 查看剩余时间\n"
            "  静默 off     —— 立即解除"
        )
    if minutes > MAX_MINUTES:
        await silence.finish(f"时长过长，最多 {MAX_MINUTES // 60} 小时（发『/静默 off』可随时解除）")

    set_silence(minutes, by=event.user_id)
    await silence.finish(
        f"🔇 已进入静默：{format_duration(int(minutes * 60))}\n"
        "静默期内机器人不响应任何群消息、不做入群审批/广告拦截/欢迎，"
        "心跳自检也会暂停（利于账号降风控）。\n"
        "私聊发『/静默 off』可随时解除。"
    )


@unsilence.handle()
async def handle_unsilence(event: MessageEvent):
    if isinstance(event, GroupMessageEvent):
        return
    if not _is_authorized(event.user_id):
        return
    if not is_silenced():
        await unsilence.finish("当前未静默，无需解除")
    clear_silence()
    await unsilence.finish("🔊 已解除静默，群功能恢复正常")
