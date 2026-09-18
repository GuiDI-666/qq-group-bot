"""存活检测：确认机器人在线及运行状态。"""
import time

from nonebot import on_command
from nonebot.adapters.onebot.v11 import MessageEvent

from .admin import COMMON_PERM
from .common import managed_group

START_TIME = time.time()

status = on_command("存活", aliases={"ping", "状态", "在吗"}, rule=managed_group(), permission=COMMON_PERM, priority=5, block=True)


@status.handle()
async def handle_status(event: MessageEvent):
    uptime = int(time.time() - START_TIME)
    h, m, s = uptime // 3600, uptime % 3600 // 60, uptime % 60
    uptime_str = f"{h}小时{m}分{s}秒" if h else f"{m}分{s}秒"
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    await status.finish(
        f"🟢 我还活着！\n"
        f"QQ：{event.self_id}\n"
        f"已连续运行：{uptime_str}\n"
        f"当前时间：{now}"
    )
