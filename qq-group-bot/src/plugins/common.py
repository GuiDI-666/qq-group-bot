"""通用工具函数。"""
import json
import time
from pathlib import Path

from nonebot.adapters.onebot.v11 import Bot, Event, GroupMessageEvent
from nonebot.rule import Rule

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.json"
SILENCE_PATH = Path(__file__).resolve().parents[2] / "data" / "silence.json"


def is_group_admin(event: GroupMessageEvent) -> bool:
    return event.sender.role in ("admin", "owner")


def _load_config() -> dict:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


# ---------------- 静默期（降低风控/临时停摆） ----------------
# 私聊命令『/静默 2h』设置：静默期内对所有群事件完全静默（不响应任何命令、
# 不做审批/拦截/欢迎），私聊管理命令仍可用。状态落盘 data/silence.json，
# 重启后依然有效；看门狗也会读取同一个文件来暂停心跳自检。
def silence_until() -> float:
    """静默截止时间戳（秒）；已过期或未设置返回 0。"""
    try:
        with open(SILENCE_PATH, "r", encoding="utf-8") as f:
            return float(json.load(f).get("until") or 0)
    except Exception:
        return 0.0


def is_silenced() -> bool:
    return silence_until() > time.time()


def silence_remaining() -> int:
    """剩余静默秒数（未静默返回 0）。"""
    return max(0, int(silence_until() - time.time()))


def set_silence(minutes: float, by: int = 0) -> float:
    """进入静默期，返回截止时间戳。"""
    until = time.time() + minutes * 60
    SILENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SILENCE_PATH, "w", encoding="utf-8") as f:
        json.dump(
            {
                "until": until,
                "minutes": minutes,
                "by": by,
                "set_at": time.time(),
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    return until


def clear_silence() -> None:
    """取消静默（写 until=0，保留文件便于排查）。"""
    SILENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(SILENCE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}
    data["until"] = 0
    data["cleared_at"] = time.time()
    with open(SILENCE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def format_duration(seconds: int) -> str:
    """把秒数格式化为「X小时Y分钟」这样的中文描述。"""
    seconds = max(0, int(seconds))
    h, m = divmod(seconds // 60, 60)
    if h and m:
        return f"{h}小时{m}分钟"
    if h:
        return f"{h}小时"
    if m:
        return f"{m}分钟"
    return f"{seconds}秒"



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
        if is_silenced():
            return False
        return is_managed_group(gid)

    return Rule(_rule)


def managed_group() -> Rule:
    """事件规则（推荐）：私聊直接放行；群聊需同时满足：
    1. 不在静默期（私聊命令『/静默 2h』可临时全局停摆）
    2. 在管理群名单内（或名单为空）
    3. 机器人在该群拥有管理员/群主身份
    机器人不是管理员、或处于静默期的群，对所有事件完全静默。
    """

    async def _rule(bot: Bot, event: Event) -> bool:
        gid = getattr(event, "group_id", None)
        if gid is None:
            return True  # 私聊不受群规则限制
        if is_silenced():
            return False  # 静默期：所有群事件不响应
        if not is_managed_group(gid):
            return False
        return await bot_is_admin(bot, gid)

    return Rule(_rule)
