"""帮助菜单：列出机器人所有可用命令。"""
from nonebot import on_command
from nonebot.adapters.onebot.v11 import GROUP_ADMIN, GROUP_OWNER, MessageEvent
from nonebot.permission import SUPERUSER

from .common import managed_group

menu = on_command(
    "菜单",
    aliases={"help", "帮助", "功能"},
    rule=managed_group(),
    permission=SUPERUSER | GROUP_ADMIN | GROUP_OWNER,
    priority=5,
    block=True,
)


@menu.handle()
async def handle_menu(event: MessageEvent):
    lines = [
        "🤖 群管机器人指令菜单",
        "",
        "⚠ 所有命令都以 / 开头（如 /禁言），直接发文字不会触发",
        "",
        "【群管理】（管理员/群主可用）",
        "/禁言 @某人/QQ号 [分钟] —— 禁言，默认10分钟，如：/禁言 1000123456 30",
        "/解禁 @某人/QQ号 —— 解除禁言",
        "/踢出 @某人/QQ号 —— 移出群聊（仅群主）",
        "",
        "【黑名单】（管理员/群主可用）",
        "/拉黑 @某人/QQ号 [原因] —— 拉黑并踢出，拒绝其再入群",
        "/解除拉黑 @某人/QQ号 —— 移出黑名单（别名：/解黑）",
        "/黑名单 —— 查看黑名单列表",
        "",
        "【自动功能】",
        "入群审批 —— QQ等级≥30自动通过，否则拒绝",
        "广告拦截 —— 命中广告关键词自动撤回+禁言1小时",
        "欢迎/退群提示 —— 新人欢迎、退群通知",
        "",
        "【设置】（管理员/群主可用）",
        "/设置等级 30 —— 修改入群审批QQ等级门槛",
        "/添加广告 / /删除广告 关键词 —— 管理广告拦截规则",
        "/广告列表 —— 查看广告规则和违禁词",
        "/设置欢迎 / /设置退群 文本 —— 修改欢迎语/退群提示",
        "/添加管理群 群号 / /移除管理群 群号 —— 设置机器人生效群（仅私聊，超级管理员）",
        "/管理群列表 —— 查看管理群名单",
        "/静默 2h —— 静默指定时长（支持 h/小时、m/分钟、s/秒），期间机器人对所有群完全静默（仅私聊，管理员）",
        "/静默 状态 / /静默 off —— 查看剩余时间 / 立即解除静默（仅私聊）",
        "",
        "【其他】",
        "/存活 /ping /状态 —— 检查机器人在线和运行时长",
        "/菜单 /help —— 显示本菜单",
        "",
        "【成员活跃】",
        "/未发言 30/60/90 —— 拉取超过N天未发言的成员名单（默认30天；私聊用法：/未发言 群号 30）",
    ]
    await menu.finish("\n".join(lines))
