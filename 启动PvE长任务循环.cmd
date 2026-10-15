@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ========================================================
echo        FL-Royale PvE 与 2v2 合作长任务自动循环 (1000轮)
echo ========================================================
echo.
echo 调度器: tools\pve_loop.ps1
echo 规格书: tools\pve_prompt.md
echo 轮数上限: 1000 轮
echo.
echo 正在启动 Command Code CLI 自主循环...
echo 若想在后台运行或查看进度，可运行 '查看PvE任务状态.cmd'。
echo 若想停止循环，可运行 '停止PvE长任务循环.cmd'。
echo ========================================================
echo.

powershell -ExecutionPolicy Bypass -File "tools\pve_loop.ps1" -Rounds 1000 -MaxRuntimeHours 120

pause
