@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 正在向 PvE 任务循环发送停止信号...
if not exist ".pve" mkdir ".pve"
echo STOP > ".pve\STOP"
echo [成功] 已创建 .pve\STOP 标记文件。
echo 正在运行的循环将在当前轮次结束后优雅退出。
pause
