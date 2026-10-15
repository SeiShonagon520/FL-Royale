# 夜间自动修复任务：对局启动即报错

## 目标

当前所有游戏模式一旦**进入对局**就会弹报错，无法游玩。
最终目标：**原封不动地让现有全部模式都能正常加载并完成一整局对战**。

## 约束（重要）

- 只做**最小必要改动**去消除报错。**不得**借机重构、改玩法数值、改卡牌数据、改 UI 布局。
- 每修好一个模式，必须用**运行该模式的验证命令**证明它能进对局，然后才允许标记该模式为"已修好"。
- 验证不通过的修复视为**未完成**，必须继续修。不要靠放宽断言、改测试期望值、注释掉测试、删测试来"通过"。
- 如果某模式需要真机验证而当前无法验证，**明确记录为待验证**，不要猜测结论。

## 第一步：建立模式清单

从 `on_device/java/com/firstlight/offline/GameModePicker.java` 读出 UI 暴露的全部模式 id，
与 `on_device/python/mobile_fair.py` 的 `GAME_MODES` 对齐，输出一张清单表：

| 模式 key | gameModeId | 时间线可加载 | 已知报错 | 状态 |
|---|---|---|---|---|

这张表是本次任务的唯一进度基准，**每轮结束时必须更新它**。

## 第二步：定位根因

优先级：

1. **实机日志**。设备用 adb，端口见下方。若 `adb devices` 为空或端口不通，在报告里直接说明"本轮无法取实机日志"，并继续做静态排查，不要卡住。
2. **静态排查**。重点怀疑方向（按经验优先级）：
   - `tools/test_mode_catalog_parity.py`：Java 模式 id 与 Python 目录不一致时，`create_match` 会**静默回落到天梯节奏**。
   - `on_device/python/mobile_fair.py` 与 `build/mobile-semantics/` 下的同名文件**不一致**。构建会从 staging 树重新生成副本，工作区改了但 staging 没同步 = 修复没进包。跑前先确认 `python -c "import mobile_fair; print(mobile_fair.__file__)"` 指向的是工作区源文件。
   - 时间线/牌组数据缺失：`native_runner.timeline.load_game_mode_timeline` 对某个 mode_id 抛异常。
   - JNI 签名不匹配：Java 方法名/参数顺序与 `GetStaticMethodID` 注册的不一致，编译通过、运行期静默失败。
   - `on_device/python/team_bot.py` 等新增模块没被打包进 `assets/chaquopy/app.imy`，导致对局启动时 ImportError。

## 第三步：修复

按清单逐个模式修。每次只改一个根因，改完立刻验证该模式，再动下一个。

## 第四步：验证

Python 回归套件（改完必须全绿）：

```powershell
python -m pytest tools/ -q
```

单文件快速回归：

```powershell
python -m pytest tools/test_mode_catalog_parity.py tools/test_all_evolution.py tools/test_game_mode_timeline.py -q
```

原生代码改动后**先做语法检查**，不要直接跑完整打包（慢且容易最后才失败）：

```
aarch64-linux-android24-clang++.cmd -std=c++17 -fsyntax-only -DFIRSTLIGHT_LOCAL_APP=1 ...
```

clang 路径在 `build.local.json` 的 `ndk_version` 对应的 SDK NDK 目录下，编译 define 从 `tools/build.py` 里抄。

需要完整打包时才用：

```powershell
python -B tools/build.py --overwrite
```

这个命令会一并编译 Java，**它的退出码就是 Java 的编译检查结果**，不要再单独跑 `gradlew`（会卡在依赖解析）。

## 第五步：实机确认（仅在 adb 可用时）

```powershell
adb -s <serial> shell pm path <pkg>
adb -s <serial> shell md5sum <pkg apk 路径>
```

跟本地 APK 的 `Get-FileHash` 对比，确认装的就是刚构建的那个包——对着旧包复现出来的日志不作数。

然后清日志缓冲、复现、取栈：

```powershell
adb -s <serial> logcat -c
adb -s <serial> logcat -d -t 2000 | Select-String -Pattern "AndroidRuntime|firstlight|Traceback"
```

## 输出格式

每轮结束时输出：

1. 模式清单表（已更新）
2. 本轮改动的文件清单 + 每处改动的一句话原因
3. 执行的验证命令**及其完整输出**（贴原文，不要只写"通过"）
4. 仍未修好的模式，及已排除的假设
5. 下一轮要做的第一件事

## adb 端口提示

设备掉线时先 `adb disconnect` 再 `adb connect`。端口拒连但模拟器进程还活着，说明实例被停过，端口不会自己回来——用 `Test-NetConnection` 扫 7555 / 5555 / 16384，并列出 `MuMu*` / `Nemu*` 进程来区分"模拟器关了"和"端口变了"。这属于环境阻塞，直接记录并继续别的模式。
