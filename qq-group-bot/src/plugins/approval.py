"""入群申请自动审批：QQ等级达到要求的自动通过，否则拒绝/忽略。"""
import json
from pathlib import Path

import nonebot
from nonebot import on_request
from nonebot.adapters.onebot.v11 import Bot, GroupRequestEvent

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.json"
logger = nonebot.logger

from .blacklist import is_blacklisted
from .common import managed_group


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


approval = on_request(priority=1, block=False, rule=managed_group())


@approval.handle()
async def handle_group_request(bot: Bot, event: GroupRequestEvent):
    if event.request_type != "group" or event.sub_type != "add":
        return

    # 黑名单用户直接拒绝
    if is_blacklisted(event.user_id):
        logger.info(f"{event.user_id} 在黑名单中，拒绝入群申请")
        try:
            await event.reject(bot, reason="您已被加入本群黑名单，无法入群")
        except Exception as e:
            logger.warning(f"拒绝黑名单用户失败: {e}")
        return

    cfg = load_config().get("auto_approve", {})
    if not cfg.get("enabled", False):
        return

    groups = cfg.get("groups", [])
    if groups and event.group_id not in groups:
        return

    min_level = int(cfg.get("min_level", 30))

    # 查询申请者QQ等级
    # 注意：NapCat 返回的字段名不统一——4.18.28 实测为 qqLevel（驼峰），
    # 老版本 / 其他实现可能是 level；两个都试，避免因字段名不匹配
    # 而误判"拿不到等级"从而放过申请（2026-09-20 实测修复）
    level = None
    try:
        info = await bot.get_stranger_info(user_id=event.user_id, no_cache=True)
        for key in ("level", "qqLevel", "qq_level"):
            v = info.get(key)
            if v is not None:
                level = v
                logger.debug(f"{event.user_id} 的等级字段命中 {key}={v}")
                break
        if level is None:
            logger.info(
                f"{event.user_id} 返回数据中无等级字段（现有键："
                f"{[k for k in info.keys() if 'evel' in k or 'level' in k.lower()]}）"
            )
    except Exception as e:
        logger.warning(f"查询申请者信息失败: {e}")

    if level is None:
        # 拿不到等级时按 fallback 策略处理：approve / reject / ignore
        fallback = str(cfg.get("fallback", "ignore")).lower()
        logger.info(f"无法获取 {event.user_id} 的等级，按 fallback={fallback} 处理")
        if fallback == "approve":
            await event.approve(bot)
        elif fallback == "reject":
            await event.reject(bot, reason="无法核实QQ等级，请手动联系管理员")
        return

    if int(level) >= min_level:
        await event.approve(bot)
        notice = cfg.get("notice", "")
        if notice:
            msg = (
                notice.replace("{at}", f"[CQ:at,qq={event.user_id}]")
                .replace("{level}", str(level))
            )
            try:
                await bot.send_group_msg(group_id=event.group_id, message=msg)
            except Exception as e:
                logger.warning(f"发送审批通知失败: {e}")
    else:
        reason = cfg.get("reject_reason", "等级不足").replace(
            "{min_level}", str(min_level)
        )
        await event.reject(bot, reason=reason)
