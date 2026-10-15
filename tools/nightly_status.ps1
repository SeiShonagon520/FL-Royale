# 早上查看夜间任务的进度，不影响正在跑的进程。
# 用法: powershell -ExecutionPolicy Bypass -File tools\nightly_status.ps1

$ErrorActionPreference = "Continue"
$Nightly = "D:\github\FL-Royale\.nightly"

Write-Host ""
Write-Host "=== 夜间任务状态 ===" -ForegroundColor Cyan

# 循环进程是否还活着
$loop = Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" |
        Where-Object { $_.CommandLine -like "*nightly_fix.ps1*" }
if ($loop) {
    Write-Host "循环进程: 运行中 (PID $($loop.ProcessId -join ', '))" -ForegroundColor Green
} else {
    Write-Host "循环进程: 已停止" -ForegroundColor Yellow
}

# 正在跑的那一轮
$cur = Get-ChildItem $Nightly -Filter "round-*.ndjson" -ErrorAction SilentlyContinue |
       Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($cur) {
    $age = [int]((Get-Date) - $cur.LastWriteTime).TotalMinutes
    Write-Host ("当前轮次: {0}  ({1} 分钟前有写入, 文件 {2:N0} 字节)" -f $cur.Name, $age, $cur.Length)
} else {
    Write-Host "当前轮次: 无" -ForegroundColor Yellow
}

# 汇总
$sum = Get-ChildItem $Nightly -Filter "summary-*.txt" -ErrorAction SilentlyContinue |
       Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($sum) { Write-Host "`n--- 汇总 ---"; Get-Content $sum.FullName }

# 已完成轮次的最终答复
$finals = Get-ChildItem $Nightly -Filter "*-final.txt" -ErrorAction SilentlyContinue |
          Sort-Object Name
if ($finals) {
    Write-Host "`n--- 已有最终答复 ---" -ForegroundColor Cyan
    $finals | ForEach-Object { Write-Host "  $($_.Name)" }
}

# 实时看看这一轮在干什么
$cur = Get-ChildItem $Nightly -Filter "round-*.ndjson" -ErrorAction SilentlyContinue |
       Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($cur) {
    try {
        $fs = [System.IO.File]::Open($cur.FullName, 'Open', 'Read', 'ReadWrite')
        $sr = New-Object System.IO.StreamReader($fs, [System.Text.Encoding]::UTF8)
        $sb = New-Object System.Text.StringBuilder
        while (-not $sr.EndOfStream) { [void]$sb.AppendLine($sr.ReadLine()) }
        $sr.Close(); $fs.Close()

        $tools = ($sb.ToString() -split "`r?`n") |
                 Where-Object { $_ -match '"tool_running"' } |
                 ForEach-Object { ($_ | ConvertFrom-Json).event.toolName }
        Write-Host "`n本轮已调用工具 (最近 12 个): $((($tools | Select-Object -Last 12) -join ', '))"
        Write-Host "本轮对话轮数: $((($sb.ToString() -split "`r?`n") | Where-Object { $_ -match '"turn_end"' } | Measure-Object).Count)"
    } catch {
        Write-Host "(无法读取实时日志: $($_.Exception.Message))" -ForegroundColor Yellow
    }
}
Write-Host ""
