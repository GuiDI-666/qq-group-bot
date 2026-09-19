"""运行时设置命令：修改 config.json 里的等级门槛、广告关键词、欢迎/退群词。

命令（管理员/群主/超级管理员可用）：
  设置等级 30            —— 修改入群自动审批的QQ等级门槛
  添加广告 关键词或正则   —— 追加广告拦截规则
  删除广告 关键词或正则   —— 删除广告拦截规则
  广告列表               —— 查看当前广告规则
  设置欢迎 欢迎词        —— 修改入群欢迎语（支持 {at}）
  设置退群 退群词        —— 修改退群提示（支持 {nickname}）
"""
import json
import re
from pathlib import Path

from nonebot import on_command
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    Message,
    MessageEvent,
)
from nonebot.params import CommandArg

from .admin import COMMON_PERM
from .common import managed_group

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.json"


def _load() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(cfg: dict) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def _plain(args: Message) -> str:
    return args.extract_plain_text().strip()


# ---------------- 设置等级 ----------------
set_level = on_command("设置等级", rule=managed_group(), permission=COMMON_PERM, priority=5, block=True)


@set_level.handle()
async def handle_set_level(event: MessageEvent, args: Message = CommandArg()):
    if not isinstance(event, GroupMessageEvent):
        await set_level.finish("该命令仅在群聊中可用")
    text = _plain(args)
    if not text.isdigit() or not (1 <= int(text) <= 100):
        await set_level.finish("用法：/设置等级 数字（1~100）\n例：/设置等级 30")
    cfg = _load()
    cfg.setdefault("auto_approve", {})["min_level"] = int(text)
    _save(cfg)
    await set_level.finish(f"✅ 入群自动审批等级门槛已设为 {text} 级（低于该等级将自动拒绝）")


# ---------------- 添加/删除广告关键词 ----------------
add_ad = on_command("添加广告", rule=managed_group(), permission=COMMON_PERM, priority=5, block=True)


@add_ad.handle()
async def handle_add_ad(event: MessageEvent, args: Message = CommandArg()):
    if not isinstance(event, GroupMessageEvent):
        await add_ad.finish("该命令仅在群聊中可用")
    text = _plain(args)
    if not text:
        await add_ad.finish("用法：/添加广告 关键词或正则\n例：/添加广告 代肝|低价代练")
    try:
        re.compile(text)
    except re.error as e:
        await add_ad.finish(f"❌ 不是有效的正则表达式：{e}\n普通关键词直接发就行，无需符号")
    cfg = _load()
    patterns = cfg.setdefault("ad_patterns", [])
    if text in patterns:
        await add_ad.finish("该关键词已存在")
    patterns.append(text)
    _save(cfg)
    await add_ad.finish(f"✅ 已添加广告规则：{text}\n当前共 {len(patterns)} 条规则")


del_ad = on_command("删除广告", rule=managed_group(), permission=COMMON_PERM, priority=5, block=True)


@del_ad.handle()
async def handle_del_ad(event: MessageEvent, args: Message = CommandArg()):
    if not isinstance(event, GroupMessageEvent):
        await del_ad.finish("该命令仅在群聊中可用")
    text = _plain(args)
    if not text:
        await del_ad.finish("用法：/删除广告 关键词或正则（先发 /广告列表 查看可删项）")
    cfg = _load()
    patterns = cfg.get("ad_patterns", [])
    if text not in patterns:
        await del_ad.finish("未找到该规则，发 广告列表 查看现有规则")
    patterns.remove(text)
    _save(cfg)
    await del_ad.finish(f"✅ 已删除广告规则：{text}")


list_ad = on_command("广告列表", aliases={"广告规则"}, rule=managed_group(), permission=COMMON_PERM, priority=5, block=True)


@list_ad.handle()
async def handle_list_ad(event: MessageEvent):
    cfg = _load()
    patterns = cfg.get("ad_patterns", [])
    banned = cfg.get("banned_words", [])
    lines = [f"广告规则（{len(patterns)} 条，正则）："]
    lines += [f"{i+1}. {p}" for i, p in enumerate(patterns[:20])]
    if banned:
        lines.append(f"\n违禁词（{len(banned)} 个，精确匹配）：{'、'.join(banned[:20])}")
    await list_ad.finish("\n".join(lines))


# ---------------- 设置欢迎/退群词 ----------------
set_welcome = on_command("设置欢迎", rule=managed_group(), permission=COMMON_PERM, priority=5, block=True)


@set_welcome.handle()
async def handle_set_welcome(event: MessageEvent, args: Message = CommandArg()):
    if not isinstance(event, GroupMessageEvent):
        await set_welcome.finish("该命令仅在群聊中可用")
    text = _plain(args)
    if not text:
        await set_welcome.finish(
            "用法：/设置欢迎 欢迎词\n{at} 会被替换为 @新人\n例：/设置欢迎 欢迎 {at} 加入～"
        )
    cfg = _load()
    cfg["welcome"] = text
    _save(cfg)
    preview = text.replace("{at}", "@新人")
    await set_welcome.finish(f"✅ 欢迎语已更新，效果预览：{preview}")


set_farewell = on_command("设置退群", rule=managed_group(), permission=COMMON_PERM, priority=5, block=True)


@set_farewell.handle()
async def handle_set_farewell(event: MessageEvent, args: Message = CommandArg()):
    if not isinstance(event, GroupMessageEvent):
        await set_farewell.finish("该命令仅在群聊中可用")
    text = _plain(args)
    if not text:
        await set_farewell.finish(
            "用法：/设置退群 退群提示语\n{nickname} 会被替换为退群者昵称\n例：/设置退群 {nickname} 悄悄地走了"
        )
    cfg = _load()
    cfg["farewell"] = text
    _save(cfg)
    preview = text.replace("{nickname}", "某人")
    await set_farewell.finish(f"✅ 退群提示已更新，效果预览：{preview}")


# ---------------- 管理群设置（仅私聊可用） ----------------
# 白名单启用后，名单外的群机器人完全静默，群内命令全部失效，
# 因此管理群名单的操作只保留私聊入口。
# 授权范围：.env 超级管理员 + config.json 的 admin_users 私聊管理员。
def _is_authorized(user_id: int) -> bool:
    from nonebot import get_driver

    superusers = {int(u) for u in (get_driver().config.superusers or set())}
    admins = {int(u) for u in _load().get("admin_users", [])}
    return user_id in superusers | admins


add_managed = on_command("添加管理群", priority=5, block=True)


@add_managed.handle()
async def handle_add_managed(event: MessageEvent, args: Message = CommandArg()):
    if isinstance(event, GroupMessageEvent):
        return  # 群聊内不响应，仅私聊可用
    if not _is_authorized(event.user_id):
        return  # 未授权私聊，静默
    text = _plain(args)
    if not text.isdigit():
        await add_managed.finish("私聊用法：/添加管理群 群号\n例：/添加管理群 1007680907")
    gid = int(text)
    cfg = _load()
    groups = cfg.setdefault("managed_groups", [])
    if gid in groups:
        await add_managed.finish(f"群 {gid} 已在管理群名单中")
    groups.append(gid)
    _save(cfg)
    await add_managed.finish(
        f"✅ 群 {gid} 已加入管理群名单\n"
        f"当前管理群共 {len(groups)} 个，机器人仅在这些群里运行"
    )


del_managed = on_command("移除管理群", priority=5, block=True)


@del_managed.handle()
async def handle_del_managed(event: MessageEvent, args: Message = CommandArg()):
    if isinstance(event, GroupMessageEvent):
        return
    if not _is_authorized(event.user_id):
        return
    text = _plain(args)
    if not text.isdigit():
        await del_managed.finish("私聊用法：/移除管理群 群号\n例：/移除管理群 1007680907")
    gid = int(text)
    cfg = _load()
    groups = cfg.get("managed_groups", [])
    if gid not in groups:
        await del_managed.finish(f"群 {gid} 不在管理群名单中")
    groups.remove(gid)
    _save(cfg)
    if groups:
        await del_managed.finish(f"✅ 群 {gid} 已移出管理群名单，剩余 {len(groups)} 个管理群")
    await del_managed.finish("✅ 已移出管理群名单（名单已空，当前对所有群生效）")


list_managed = on_command("管理群列表", priority=5, block=True)


@list_managed.handle()
async def handle_list_managed(event: MessageEvent):
    if isinstance(event, GroupMessageEvent):
        return
    if not _is_authorized(event.user_id):
        return
    cfg = _load()
    groups = cfg.get("managed_groups", [])
    if not groups:
        await list_managed.finish(
            "管理群名单为空 = 机器人当前对所有群生效\n"
            "私聊发『/添加管理群 群号』可切换为白名单模式（仅名单内群运行）"
        )
    lines = [f"白名单模式（仅以下群运行），共 {len(groups)} 个："]
    lines += [f"- {g}" for g in groups]
    await list_managed.finish("\n".join(lines))
