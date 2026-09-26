# Build FL Royale

[Home](../README.md) · [简体中文](build.zh-CN.md)

The build runs on Windows with PowerShell 7, Python 3.12, JDK 17 and the Android SDK. Install Android platform 34, build-tools 36.1.0 and NDK 27.3.13750724. Gradle downloads the Android/Chaquopy dependencies on the first build. The project uses ONNX Runtime 1.30.0; Gradle supplies Python 3.12 and NumPy 1.26.2 inside the APK.

## 1. Prepare Firstlight CR

Clone [Firstlight CR](https://github.com/Jaasssoooonnnnn/FirstLight_CR) and follow its [setup guide](https://github.com/Jaasssoooonnnnn/FirstLight_CR/blob/main/docs/getting_started.md). The recommended revision is `3ade158c52bf33de3ffcea600ce380946b7de0d8`, which was used to develop this version.

Use its supported **Null's Royale 15.535.13 / ARM64** APK and **15.535.86** resource update. When the guide reaches the offline APK build, name the output as follows, from the Firstlight CR directory:

```powershell
./build_offline_engine.ps1 `
  -InputApk ./user-provided/nulls-royale.apk `
  -OutputApk ./build/cr-ai-offline-cache.apk `
  -EngineCount 1
```

If you already have `cr-ai-offline.apk`, set `base_apk` to its path.

Keep Firstlight CR's five checkpoint files available. They live under `checkpoints/General`, `checkpoints/IL`, `checkpoints/active IL` and `checkpoints/2_6hog_expert`.

## 2. Copy the game update

After the supported original game has downloaded its update and reached the lobby, close it and copy its update directory from the rooted build emulator. Use your own ADB path and the instance's address:

```powershell
$adb = 'C:/path/to/Android/Sdk/platform-tools/adb.exe'
$serial = '127.0.0.1:YOUR_PORT'
& $adb -s $serial root
& $adb -s $serial wait-for-device
New-Item -ItemType Directory -Force ./user-provided | Out-Null
& $adb -s $serial pull /data/user/0/nullsroyale.rel.free/update ./user-provided/runtime-update
```

Run this from the FL Royale directory. The resulting folder must contain `fingerprint.json` directly, along with the downloaded resources. If you already have a local copy, point `runtime_update` at that directory. If ADB root is unavailable, use the emulator's rooted shell to copy the same directory to a readable location before pulling it.

## 3. Configure the build

From FL Royale:

```powershell
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -r requirements-build.txt
Copy-Item build.example.json build.local.json
```

Edit `build.local.json`:

| Field | Meaning |
| --- | --- |
| `firstlight_cr` | Path to the Firstlight CR checkout |
| `base_apk` | Blank uses `<firstlight_cr>/build/cr-ai-offline-cache.apk` |
| `runtime_update` | Directory containing the downloaded update and `fingerprint.json` |
| `java_home` | JDK 17 directory; blank uses `JAVA_HOME` |
| `android_sdk` | Android SDK directory; blank uses `ANDROID_HOME`, then the usual Windows SDK location |
| `build_tools_version`, `ndk_version` | Installed Android tool versions |
| `keystore`, `password_file`, `key_alias` | Optional signing key, password text file and alias |

Relative paths are resolved against the configuration file's directory. Paths with spaces are supported. You can keep your config elsewhere and pass `-Config C:/path/to/build.local.json`.

With blank signing paths, the first build creates a key under `build/signing/`. Keep that directory to sign future builds and update your installation.

## 4. Build

```powershell
./tools/build_offline_app.ps1 -Python ./.venv/Scripts/python.exe
```

Output: `dist/fl-royale-1.8-r1.apk`, with a small `.build.json` record beside it.

The first run downloads Apktool and the Android ONNX runtime, exports the five models from Firstlight CR, packages your resources, compiles the native bridge and Android runtime, then rebuilds and signs the APK.

Subsequent builds reuse the exported models. After changing checkpoint weights or the upstream model implementation, pass `-ExportModels` to export them again. Use a new output filename when keeping multiple builds:

```powershell
./tools/build_offline_app.ps1 `
  -Python ./.venv/Scripts/python.exe `
  -ExportModels `
  -OutputApk ./dist/fl-royale-custom.apk
```

Intermediate APK decode directories are removed after the build; `-KeepBuildWork` retains them for debugging. Tool downloads, model exports, Gradle outputs, local signing material and final APKs are ignored by Git.
