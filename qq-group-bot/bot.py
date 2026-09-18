import nonebot
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter

# 命令前缀在代码里兜底声明："" = 允许直接发"禁言 @某人"，"/" = 也支持"/禁言"。
# 这样即使 .env 被部署脚本覆盖，裸命令也不会失效。
nonebot.init(command_start={"", "/"})

driver = nonebot.get_driver()
driver.register_adapter(OneBotV11Adapter)

nonebot.load_plugins("src/plugins")

if __name__ == "__main__":
    nonebot.run()
