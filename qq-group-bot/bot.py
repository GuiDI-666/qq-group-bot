import asyncio
import json
import random
from pathlib import Path

import nonebot
from nonebot.adapters.onebot.v11 import (
    Adapter as OneBotV11Adapter,
    GroupMessageEvent,
    MessageEvent,
)
from nonebot.message import run_preprocessor

# 命令前缀在代码里兜底声明：只认 "/命令" 形式（如 /禁言、/ping）。
# 普通聊天里说"在吗""状态"等词不会误触发机器人。
# 这样即使 .env 被部署脚本覆盖，前缀设置也不会失效。
nonebot.init(command_start={"/"})

driver = nonebot.get_driver()
driver.register_adapter(OneBotV11Adapter)

nonebot.load_plugins("src/plugins")

# ---------------- 回复随机延迟（拟人化） ----------------
# 机器人回复前随机等待一段时间，避免"秒回"显得机械。
# 范围可在 config.json 里热调：reply_delay: [最小秒, 最大秒]，默认 [0, 2]。
# 广告/违禁词拦截（guard）不延迟，保证撤回与禁言及时。
_CONFIG_PATH = Path(__file__).resolve().parent / "config.json"
_FAST_MODULES = {"src.plugins.guard"}


def _delay_range() -> tuple[float, float]:
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            lo, hi = (json.load(f).get("reply_delay") or [0, 2])[:2]
        return float(lo), float(hi)
    except Exception:
        return 0.0, 2.0


@run_preprocessor
async def _human_reply_delay(matcher, event):
    if not isinstance(event, MessageEvent):
        return
    if getattr(getattr(matcher, "module", None), "__name__", "") in _FAST_MODULES:
        return
    lo, hi = _delay_range()
    if hi <= 0:
        return
    await asyncio.sleep(random.uniform(lo, hi))


@run_preprocessor
async def _silence_hint(matcher, event):
    """静默期内：管理员私聊发其它命令时，回一条明确的状态提示。

    静默的本意是"群内完全停摆"（避免风控），但私聊管理命令若一点回音都没有，
    会让人误以为机器人挂了。这里补一句反馈，同时避免命令被静默地执行。
    """
    if not isinstance(event, MessageEvent) or isinstance(event, GroupMessageEvent):
        return  # 只管私聊；群消息由 common.managed_group 统一拦截
    module = getattr(getattr(matcher, "module", None), "__name__", "")
    if module.endswith(".silence"):
        return  # 静默插件自身的命令要正常放行，否则无法查询/解除
    try:
        from src.plugins.common import format_duration, is_silenced, silence_remaining
        from src.plugins.settings import _is_authorized
    except Exception:
        return
    if not is_silenced() or not _is_authorized(event.user_id):
        return
    await matcher.finish(
        f"🔇 机器人正在静默中，剩余 {format_duration(silence_remaining())}。\n"
        "这条命令没有执行。发『/静默 off』可立即解除静默。"
    )


if __name__ == "__main__":
    nonebot.run()
