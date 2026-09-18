@echo off
chcp 65001 >nul
title QQ群管机器人 重新登录
cd /d "%~dp0"
echo ==========================================
echo   重新登录协议端（NapCat）
echo   会重启 QQ 协议端并尝试免扫码快速登录
echo ==========================================
echo.
echo 如果免扫码失败，会自动生成二维码：
echo   %~dp0需要扫码登录.png
echo （用手机 QQ 扫描该图片，选择机器人小号授权）
echo.
pause >nul

python "%~dp0deploy\watchdog.py" --relogin

echo.
echo 完成。如仍需扫码，请看上面的图片路径。
pause >nul
