# 查看 PvE 循环的进度，不影响正在跑的进程。
# 用法: powershell -ExecutionPolicy Bypass -File tools\pve_status.ps1

$ErrorActionPreference = "Continue"
$Dir = "D:\github\FL-Royale\.pve"

Write-Host ""
Write-Host "=== PvE 复刻还原状态 ===" -ForegroundColor Cyan

$loop = Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" |
        Where-Object { $_.CommandLine -like "*pve_loop.ps1*" }
if ($loop) {
    Write-Host "循环进程: 运行中 (PID $($loop.ProcessId -join ', '))" -ForegroundColor Green
} else {
    Write-Host "循环进程: 已停止" -ForegroundColor Yellow
}

$cur = Get-ChildItem $Dir -Filter "round-*.ndjson" -ErrorAction SilentlyContinue |
       Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($cur) {
    $age = [int]((Get-Date) - $cur.LastWriteTime).TotalMinutes
    Write-Host ("当前轮次: {0}  ({1} 分钟前有写入, {2:N0} 字节)" -f $cur.Name, $age, $cur.Length)
} else {
    Write-Host "当前轮次: 无" -ForegroundColor Yellow
}

$rounds = (Get-ChildItem $Dir -Filter "round-*.ndjson" -ErrorAction SilentlyContinue | Measure-Object).Count
Write-Host "累计轮次: $rounds"

$sum = Get-ChildItem $Dir -Filter "summary-*.txt" -ErrorAction SilentlyContinue |
       Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($sum) { Write-Host "`n--- 汇总 ---"; Get-Content $sum.FullName }

$finals = Get-ChildItem $Dir -Filter "*-final.txt" -ErrorAction SilentlyContinue | Sort-Object Name
if ($finals) {
    Write-Host "`n--- 已有最终答复 ---" -ForegroundColor Cyan
    $finals | ForEach-Object { Write-Host "  $($_.Name)" }
}

# 实时看看这一轮在干什么
$cur = Get-ChildItem $Dir -Filter "round-*.ndjson" -ErrorAction SilentlyContinue |
       Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($cur) {
    try {
        $fs = [System.IO.File]::Open($cur.FullName, 'Open', 'Read', 'ReadWrite')
        $sr = New-Object System.IO.StreamReader($fs, [System.Text.Encoding]::UTF8)
        $sb = New-Object System.Text.StringBuilder
        while (-not $sr.EndOfStream) { [void]$sb.AppendLine($sr.ReadLine()) }
        $sr.Close(); $fs.Close()
        $all = $sb.ToString() -split "`r?`n"

        $tools = $all | Where-Object { $_ -match '"tool_running"' } |
                 ForEach-Object { ($_ | ConvertFrom-Json).event.toolName }
        Write-Host "`n本轮已调用工具 (最近 12 个): $((($tools | Select-Object -Last 12) -join ', '))"
        Write-Host "本轮对话轮数: $(($all | Where-Object { $_ -match '"turn_end"' } | Measure-Object).Count)"

        # 抓最后一条纯文本输出，方便一眼看出它在想什么
        $say = $all | Where-Object { $_ -match '"type":"text_delta"' } |
               ForEach-Object { ($_ | ConvertFrom-Json).event.delta } |
               Where-Object { $_ -and $_ -ne "`n" } | Select-Object -Last 3
        if ($say) {
            Write-Host "`n--- 最近的叙述 ---" -ForegroundColor DarkGray
            ($say -join "") -split "(?<=\.)" | Select-Object -Last 3 | ForEach-Object { Write-Host "  $_" }
        }
    } catch {
        Write-Host "(无法读取实时日志: $($_.Exception.Message))" -ForegroundColor Yellow
    }
}
Write-Host ""
