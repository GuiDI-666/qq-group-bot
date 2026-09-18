@echo off
chcp 65001 >nul
title QQ群管机器人 一键启动（看门狗模式）
cd /d "%~dp0"
echo ==========================================
echo   QQ群管机器人 一键启动
echo   看门狗守护：协议端(NapCat) + 机器人(NoneBot)
echo   登录失效会自动重新登录，进程掉线会自动拉起
echo ==========================================
echo.
echo 机器人账号 / 端口 / 管理群：见 部署配置.json 与 qq-group-bot\config.json
echo 关闭本窗口即停止守护（协议端与机器人会一并退出）
echo.

python "%~dp0deploy\watchdog.py"

echo.
echo 看门狗已退出。按任意键关闭窗口。
pause >nul
