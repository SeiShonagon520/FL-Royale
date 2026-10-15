<#
.SYNOPSIS
    PvE 复刻还原任务循环：反复以 headless 模式跑 cmd，按 tools/pve_prompt.md
    推进波次制 PvE 的七个阶段，直到完成或达到上限。

.DESCRIPTION
    与 tools/nightly_fix.ps1 同一套机制，但任务与日志完全独立，互不干扰。

    轮次续跑规则基于退出码：
      8  = EXIT_MAX_TURNS_REACHED，被轮数上限截断 -> 继续下一轮（接着上一轮上下文）
      0  = 正常收尾（agent 认为七个阶段都完成了）-> 停止
      其他 = 真出错（鉴权、模型 id 不存在、effort 不支持）-> 停止并记录

    提示词不内联在命令行，而是让 agent 自己读 tools/pve_prompt.md —— 避免
    Windows 参数引号/换行把长 prompt 传坏。

.PARAMETER Rounds
    最多跑几轮。默认 40。

.PARAMETER MaxTurns
    单轮的对话轮数上限。CLI 文档明确说明该值没有上界。默认 100000。

.PARAMETER MaxRuntimeHours
    墙钟时间上限（小时），默认 10。到点即停，防止天亮后还在跑。

.PARAMETER Effort
    推理强度。默认 high。可选值取决于模型：Space Bunny Alpha 是
    low/medium/high，Pixel Canary 是 low/medium/xhigh —— 传该模型不支持的值
    会被 CLI 拒绝并以 exit 1 退出。

.PARAMETER Model
    模型 id。默认 stealth/space-bunny-alpha（1M 上下文，适合长夜任务）。

.PARAMETER NoContinuation
    不用 -c 续跑上一轮会话，默认每轮都用 -c 接着上一轮的上下文往下做。

.PARAMETER ForkSession
    续跑时加 --fork-session 保留旧会话历史，便于事后回看。

.PARAMETER DryRun
    只打印将要执行的命令，不真正运行。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools\pve_loop.ps1

.EXAMPLE
    # 跑到今晚 12 点半（从现在起约 9.5 小时）
    powershell -ExecutionPolicy Bypass -File tools\pve_loop.ps1 -MaxRuntimeHours 9.5

.EXAMPLE
    # 先只跑一轮看看
    powershell -ExecutionPolicy Bypass -File tools\pve_loop.ps1 -Rounds 1 -MaxRuntimeHours 1
#>
[CmdletBinding()]
param(
    [int]    $Rounds          = 1000,
    [int]    $MaxTurns        = 100000,
    [double] $MaxRuntimeHours = 120,
    [string] $Effort          = "high",
    [string] $Model           = "stealth/space-bunny-alpha",
    [switch] $NoContinuation,
    [switch] $ForkSession,
    [switch] $DryRun
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot   = Split-Path -Parent $PSScriptRoot
$LogDir     = Join-Path $RepoRoot ".pve"
$PromptRel  = "tools/pve_prompt.md"
$PromptFile = Join-Path $RepoRoot $PromptRel
if (-not (Test-Path $PromptFile)) { throw "找不到提示词文件: $PromptFile" }

# 全局安装的 cmdc / commandcode 是 npm 生成的 .ps1 包装脚本，不能直接喂给
# 子进程。解析成真实的 node.exe + dist/index.mjs。
$cli = Get-Command cmdc -ErrorAction SilentlyContinue
if (-not $cli) { $cli = Get-Command commandcode -ErrorAction SilentlyContinue }
if (-not $cli) { throw "找不到 cmdc / commandcode，请确认已全局安装。" }

$entry = Join-Path (Split-Path -Parent $cli.Source) "node_modules/command-code/dist/index.mjs"
if (-not (Test-Path $entry)) { throw "找不到 CLI 入口脚本: $entry" }
$node = Get-Command node -ErrorAction SilentlyContinue
if (-not $node) { throw "找不到 node.exe，cmdc 依赖它运行。" }
$cliExe  = $node.Source
$cliArgs = @($entry)

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$stamp      = Get-Date -Format "yyyyMMdd-HHmmss"
$SummaryLog = Join-Path $LogDir "summary-$stamp.txt"
$StopFile   = Join-Path $LogDir "STOP"
$StatusLog  = Join-Path $LogDir "git-status-$stamp.txt"
$DeadlineAt = (Get-Date).AddHours($MaxRuntimeHours)

# 指令保持很短，任务定义全在提示词文件里。
$Bootstrap = "Read $PromptRel and follow it exactly - that file IS the task definition. " +
             "Work through its stages autonomously, in order. Do not stop at the " +
             "first blocker: record it, move on to the stages you can still advance, " +
             "and keep the stage table in your final report up to date."

function Write-Log {
    param([string]$Message, [string]$Level = "INFO")
    $line = "{0} [{1}] {2}" -f (Get-Date -Format "HH:mm:ss"), $Level, $Message
    Write-Host $line
    Add-Content -Encoding UTF8 -Path $SummaryLog -Value $line
}

if (Test-Path $StopFile) { Remove-Item $StopFile -Force }

$git = Get-Command git -ErrorAction SilentlyContinue
if ($git) {
    & $git.Source -C $RepoRoot status --short *>&1 |
        Out-File -Encoding UTF8 -FilePath $StatusLog
}

Write-Log "=== PvE 复刻还原循环启动 ==="
Write-Log "仓库:   $RepoRoot"
Write-Log "CLI:    node $($cliArgs -join ' ')"
Write-Log "模型:   $Model / effort $Effort"
Write-Log "提示词: $PromptRel"
Write-Log "日志:   $LogDir"
Write-Log "上限:   $Rounds 轮 x $MaxTurns 轮"
Write-Log "停止时间: $($DeadlineAt.ToString('yyyy-MM-dd HH:mm'))"

$exitCode = 0
$round    = 0
$finals   = @()

while ($round -lt $Rounds) {

    if ((Get-Date) -ge $DeadlineAt) {
        Write-Log "已达到墙钟时间上限 $MaxRuntimeHours 小时，停止。" "WARN"
        break
    }
    if (Test-Path $StopFile) {
        Write-Log "发现 STOP 文件，优雅停止。" "WARN"
        Remove-Item $StopFile -Force
        break
    }

    $round++
    $startedAt = Get-Date
    $RoundLog  = Join-Path $LogDir ("round-{0}-{1:d2}.ndjson" -f $stamp, $round)
    $ErrLog    = Join-Path $LogDir ("round-{0}-{1:d2}.stderr" -f $stamp, $round)
    $FinalFile = Join-Path $LogDir ("round-{0}-{1:d2}-final.txt" -f $stamp, $round)
    Write-Log "--- 第 $round / $Rounds 轮开始 ---"

    # Start-Process -ArgumentList 是用空格把数组拼成一条命令行，不会替你加引号，
    # 含空格的参数（提示词）必须自己包上，否则会被拆成几十个参数而报
    # "too many arguments"。
    $argsList = @($cliArgs) +
                @("-p", ('"{0}"' -f $Bootstrap),
                  "--max-turns", $MaxTurns,
                  "--effort", $Effort,
                  "--output-format", "json",
                  "--skip-onboarding",
                  "--tools-enable", "todo_write",
                  "-t",
                  "--yolo")

    if ($Model) { $argsList += @("-m", $Model) }
    if ($round -gt 1) {
        if (-not $NoContinuation) { $argsList += "-c" }
        if ($ForkSession) { $argsList += "--fork-session" }
    }

    if ($DryRun) {
        Write-Log ("DRYRUN: " + ($argsList -join " "))
        break
    }

    # 用 Start-Process -RedirectStandardOutput 而不是
    # `& $exe ... | Tee-Object`：后者在 Windows PowerShell 5.1 里按系统默认代码页
    # （这台机器是 GB2312）把管道内容写盘，NDJSON 会被写成 UTF-16LE，agent 下一次
    # 读回自己的提示词时中文全是乱码。Start-Process 在 OS 句柄层重定向，
    # 字节原样落盘。
    $proc = Start-Process -FilePath $cliExe -ArgumentList $argsList `
        -WorkingDirectory $RepoRoot -NoNewWindow -PassThru `
        -RedirectStandardOutput $RoundLog `
        -RedirectStandardError  $ErrLog
    # 必须先碰一下 .Handle 让 .NET 把进程句柄缓存下来，否则 Start-Process -PassThru
    # 返回的对象在进程退出后读 ExitCode 会拿到 $null，循环的退出码判断就全废了。
    $null = $proc.Handle
    $proc.WaitForExit()
    $exitCode = $proc.ExitCode
    if ($null -eq $exitCode) { $exitCode = 0 }
    $elapsed  = [int]((Get-Date) - $startedAt).TotalMinutes

    # 从 NDJSON 里抽最终答复单独存档。
    if (Test-Path $RoundLog) {
        $result = Get-Content $RoundLog -Encoding UTF8 |
                  Where-Object { $_ -like '*{"type":"result"*' } |
                  Select-Object -Last 1
        if ($result) {
            $stopReason = "unparsed"
            $finalText  = $result
            try {
                $obj        = $result | ConvertFrom-Json
                $finalText  = $obj.finalText
                $stopReason = $obj.stopReason
            } catch { }
            Set-Content -Encoding UTF8 -Path $FinalFile -Value $finalText
            $finals += $FinalFile
            Write-Log "本轮 stopReason=$stopReason，用时 $elapsed 分钟。"
        }
    }

    if ($exitCode -eq 8) {
        Write-Log "第 $round 轮撞上 --max-turns $MaxTurns（exit 8），继续下一轮。" "WARN"
        continue
    }
    if ($exitCode -eq 0) {
        Write-Log "第 $round 轮正常收尾（exit 0），agent 认为各阶段已推进完毕，停止循环。" "OK"
    } else {
        Write-Log "第 $round 轮异常退出 exit $exitCode，停止循环。查 $ErrLog" "ERROR"
    }
    break
}

$summary = @"

=== PvE 复刻还原循环结束 ===
开始:       $stamp
结束:       $(Get-Date -Format "yyyyMMdd-HHmmss")
完成轮数:   $round / $Rounds
最后退出码: $exitCode  (0=完成  8=轮数截断  其他=出错)
git 状态:   $StatusLog
最终答复:
$(if ($finals) { $finals -join "`n" } else { "(无)" })
"@
Add-Content -Encoding UTF8 -Path $SummaryLog -Value $summary
Write-Log "完成。汇总: $SummaryLog"
