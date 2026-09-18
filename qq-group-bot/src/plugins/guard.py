"""违规内容防护：违禁词 + 广告正则识别。

普通成员：命中 -> 撤回 + 禁言
管理员/群主：命中 -> 不撤回不禁言，仅提示发言不规范
"""
import json
import re
from pathlib import Path

import nonebot
from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent

from .common import managed_group

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.json"
logger = nonebot.logger


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


guard = on_message(priority=10, block=False, rule=managed_group())


@guard.handle()
async def handle_guard(bot: Bot, event: GroupMessageEvent):
    if event.user_id == event.self_id:
        return
    text = event.get_plaintext().strip()
    if not text:
        return

    cfg = load_config()

    hit = None
    for word in cfg.get("banned_words", []):
        if word and word in text:
            hit = f"违禁词「{word}」"
            break
    if hit is None:
        for pattern in cfg.get("ad_patterns", []):
            try:
                if re.search(pattern, text):
                    hit = f"广告规则「{pattern}」"
                    break
            except re.error as e:
                logger.warning(f"无效的广告正则 {pattern}: {e}")

    if hit is None:
        return

    is_staff = event.sender.role in ("admin", "owner")

    if is_staff:
        # 管理员/群主：不撤回、不禁言，仅提醒
        await guard.finish(
            f"⚠️ [CQ:at,qq={event.user_id}] 管理员的发言疑似不规范（{hit}），请注意措辞～（已豁免撤回/禁言）"
        )

    # 普通成员：撤回 + 禁言（只捕获API失败，不能吞掉 finish 的结束信号）
    from nonebot.adapters.onebot.v11.exception import ActionFailed

    try:
        await bot.delete_msg(message_id=event.message_id)
        seconds = int(cfg.get("ad_mute_seconds", cfg.get("banned_word_mute_seconds", 600)))
        await bot.set_group_ban(
            group_id=event.group_id, user_id=event.user_id, duration=seconds
        )
    except ActionFailed as e:
        logger.warning(f"违规处理失败({hit}): {e}")
        return
    alert = cfg.get("ad_alert", "检测到违规内容，已撤回并禁言")
    msg = alert.replace("{mention}", f"[CQ:at,qq={event.user_id}]")
    await guard.finish(msg)
