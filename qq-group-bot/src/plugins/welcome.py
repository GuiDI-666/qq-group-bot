import json
from pathlib import Path

from nonebot import logger, on_notice
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupDecreaseNoticeEvent,
    GroupIncreaseNoticeEvent,
)

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.json"

from .blacklist import is_blacklisted
from .common import managed_group

notice = on_notice(priority=5, rule=managed_group())


@notice.handle()
async def handle_group_increase(bot: Bot, event: GroupIncreaseNoticeEvent):
    if is_blacklisted(event.user_id):
        return  # 黑名单用户进群即被踢，不发欢迎语
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    welcome = cfg.get("welcome", "欢迎加入本群～")
    msg = welcome.replace("{at}", f"[CQ:at,qq={event.user_id}]")
    await bot.send_group_msg(group_id=event.group_id, message=msg)


@notice.handle()
async def handle_group_decrease(bot: Bot, event: GroupDecreaseNoticeEvent):
    if event.user_id == event.self_id:
        return  # 机器人自己退群不播报
    if event.operator_id == event.self_id:
        return  # 机器人主动踢出的（广告/拉黑），已有对应提示，不重复播报
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    farewell = cfg.get("farewell", "有成员离开了本群")
    # GroupDecreaseNoticeEvent 没有 nickname 字段，尝试查询昵称，失败则用QQ号
    name = str(event.user_id)
    try:
        info = await bot.get_stranger_info(user_id=event.user_id)
        name = info.get("nickname") or name
    except Exception as e:
        logger.warning(f"查询退群成员昵称失败: {e}")
    msg = farewell.replace("{nickname}", name)
    await bot.send_group_msg(group_id=event.group_id, message=msg)
