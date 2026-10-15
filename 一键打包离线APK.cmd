@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ========================================================
echo        FL-Royale 离线单机 APK 一键打包工具
echo ========================================================
echo.

if not exist "user-provided\runtime-update\fingerprint.json" (
    echo [提示] 尚未检测到游戏更新资源文件夹：user-provided\runtime-update\fingerprint.json
    echo.
    echo 若您的模拟器已下载完更新，可运行以下命令自动提取：
    echo   adb pull /data/user/0/nullsroyale.rel.free/update ./user-provided/runtime-update
    echo.
    pause
    exit /b 1
)

echo [1/3] 同步移动端玩法规则与语义模块...
".venv\Scripts\python.exe" -B tools\stage_mobile_semantics.py
if %ERRORLEVEL% neq 0 (
    echo [错误] 语义同步失败
    pause
    exit /b %ERRORLEVEL%
)

echo [2/3] 编译 Android 运行时组件...
cd android_runtime_smoke
call gradlew.bat :localapp:assembleDebug --console=plain
cd ..
if %ERRORLEVEL% neq 0 (
    echo [错误] Gradle 编译失败
    pause
    exit /b %ERRORLEVEL%
)

echo [3/3] 打包资源、整合神经网络并进行签名...
powershell -ExecutionPolicy Bypass -File tools\build_offline_app.ps1 -Python .\.venv\Scripts\python.exe -Overwrite
if %ERRORLEVEL% neq 0 (
    echo [错误] APK 打包流程异常
    pause
    exit /b %ERRORLEVEL%
)

echo.
echo ========================================================
echo [成功] 完整离线安装包已输出至：dist\fl-royale-1.8-r1.apk
echo 可直接安装至 Android 手机，或拖入 MuMu 模拟器畅玩！
echo ========================================================
pause
