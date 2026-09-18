"""通用工具函数。"""
import json
import time
from pathlib import Path

from nonebot.adapters.onebot.v11 import Bot, Event, GroupMessageEvent
from nonebot.rule import Rule

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.json"


def is_group_admin(event: GroupMessageEvent) -> bool:
    return event.sender.role in ("admin", "owner")


def _load_config() -> dict:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def is_managed_group(group_id: int) -> bool:
    """判断群是否在管理名单内。

    规则：config.json 的 managed_groups 为空 = 所有群生效（默认）；
    非空 = 仅名单内的群运行，其余群机器人完全不反应。
    """
    groups = _load_config().get("managed_groups", [])
    return True if not groups else group_id in groups


# ---------------- 机器人自身管理员身份检测 ----------------
# 缓存 5 分钟：避免每条消息都查一次 API；撤销/授予管理后最迟 5 分钟生效
_bot_admin_cache: dict[int, tuple[bool, float]] = {}
_ADMIN_CACHE_TTL = 300


async def bot_is_admin(bot: Bot, group_id: int) -> bool:
    """查询机器人在指定群是否拥有管理员/群主身份（带缓存）。"""
    now = time.time()
    cached = _bot_admin_cache.get(group_id)
    if cached and now - cached[1] < _ADMIN_CACHE_TTL:
        return cached[0]
    try:
        info = await bot.get_group_member_info(
            group_id=group_id, user_id=bot.self_id, no_cache=True
        )
        ok = info.get("role") in ("admin", "owner")
    except Exception:
        ok = False  # 查不到（如机器人已退群）按无权限处理
    _bot_admin_cache[group_id] = (ok, now)
    return ok


def in_managed_group() -> Rule:
    """事件规则：仅在管理群内放行（非群事件如私聊不受此限制）。"""

    async def _rule(event: Event) -> bool:
        gid = getattr(event, "group_id", None)
        if gid is None:
            return True
        return is_managed_group(gid)

    return Rule(_rule)


def managed_group() -> Rule:
    """事件规则（推荐）：私聊直接放行；群聊需同时满足：
    1. 在管理群名单内（或名单为空）
    2. 机器人在该群拥有管理员/群主身份
    机器人不是管理员的群，对所有事件完全静默。
    """

    async def _rule(bot: Bot, event: Event) -> bool:
        gid = getattr(event, "group_id", None)
        if gid is None:
            return True  # 私聊不受群规则限制
        if not is_managed_group(gid):
            return False
        return await bot_is_admin(bot, gid)

    return Rule(_rule)
