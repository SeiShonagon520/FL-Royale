# 构建 FL Royale

[首页](../README.zh-CN.md) · [English](build.md)

构建环境：Windows、PowerShell 7、Python 3.12、JDK 17，以及 Android SDK 的 platform 34、build-tools 36.1.0、NDK 27.3.13750724。首次构建时 Gradle 下载 Android 和 Chaquopy 依赖。App 内使用 ONNX Runtime 1.30.0、Python 3.12 和 NumPy 1.26.2。

## 1. 准备 Firstlight CR 底包

克隆 [Firstlight CR](https://github.com/Jaasssoooonnnnn/FirstLight_CR)，按它的[安装教程](https://github.com/Jaasssoooonnnnn/FirstLight_CR/blob/main/docs/getting_started.md)准备原始游戏、模拟器和工具链。建议使用本版开发时的提交 `3ade158c52bf33de3ffcea600ce380946b7de0d8`。

使用教程支持的 **Null's Royale 15.535.13 / ARM64** 和 **15.535.86** 更新资源。在 Firstlight CR 目录执行底包构建时，指定输出名称：

```powershell
./build_offline_engine.ps1 `
  -InputApk ./user-provided/nulls-royale.apk `
  -OutputApk ./build/cr-ai-offline-cache.apk `
  -EngineCount 1
```

已有 `cr-ai-offline.apk` 时，将后面的 `base_apk` 配置设为它的路径即可。

同时准备 Firstlight CR 的五个模型权重。权重位于 `checkpoints/General`、`checkpoints/IL`、`checkpoints/active IL` 和 `checkpoints/2_6hog_expert`。

## 2. 复制游戏更新资源

原始游戏完成更新并进入大厅后退出。进入 FL Royale 目录，使用自己的 ADB 路径和模拟器地址复制资源：

```powershell
$adb = 'C:/path/to/Android/Sdk/platform-tools/adb.exe'
$serial = '127.0.0.1:YOUR_PORT'
& $adb -s $serial root
& $adb -s $serial wait-for-device
New-Item -ItemType Directory -Force ./user-provided | Out-Null
& $adb -s $serial pull /data/user/0/nullsroyale.rel.free/update ./user-provided/runtime-update
```

最终 `runtime-update` 目录内应直接包含 `fingerprint.json` 和下载资源。已有本地资源副本时，将 `runtime_update` 指向它即可。如果模拟器不支持 `adb root`，使用它的 Root shell 将同一目录复制到可读取的位置后再 pull。

## 3. 配置本机路径

在 FL Royale 目录执行：

```powershell
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -r requirements-build.txt
Copy-Item build.example.json build.local.json
```

编辑 `build.local.json`：

| 字段 | 用途 |
| --- | --- |
| `firstlight_cr` | Firstlight CR 仓库路径 |
| `base_apk` | 留空时使用该仓库的 `build/cr-ai-offline-cache.apk` |
| `runtime_update` | 包含 `fingerprint.json` 的更新资源目录 |
| `java_home` | JDK 17 目录；留空则读取 `JAVA_HOME` |
| `android_sdk` | Android SDK 目录；留空依次使用 `ANDROID_HOME` 或 Windows 常用安装位置 |
| `build_tools_version`、`ndk_version` | 本机安装的 Android 工具版本 |
| `keystore`、`password_file`、`key_alias` | 可选的签名密钥、密码文本文件和别名 |

相对路径以配置文件所在目录为基准，支持带空格的路径。配置文件也可放在别处，通过 `-Config C:/path/to/build.local.json` 指定。

签名路径留空时，首次构建在 `build/signing/` 生成密钥。保留该目录，供后续版本签名和覆盖更新使用。

## 4. 构建 APK

```powershell
./tools/build_offline_app.ps1 -Python ./.venv/Scripts/python.exe
```

输出为 `dist/fl-royale-1.8-r1.apk`，旁边生成一份 `.build.json` 构建记录。

首次运行会下载 Apktool 和 Android ONNX Runtime，从 Firstlight CR 导出五个模型，打包本地资源，编译原生桥和安卓运行时，然后重新打包、签名。

后续构建复用已导出的模型。更换权重或上游模型实现后，加 `-ExportModels` 重新导出。保留多个版本时另指定输出名：

```powershell
./tools/build_offline_app.ps1 `
  -Python ./.venv/Scripts/python.exe `
  -ExportModels `
  -OutputApk ./dist/fl-royale-custom.apk
```

构建后会清理本次的 APK 解包目录，排查问题时可加 `-KeepBuildWork` 保留。下载工具、模型导出、Gradle 产物、签名资料和 APK 均被 Git 忽略。
