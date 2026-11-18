param(
    [switch]$Apply
)
$ErrorActionPreference = "Stop"
$Base = Join-Path $env:LOCALAPPDATA "MarkSixBot"
$Data = Join-Path $Base "data"
$Bak  = Join-Path $Base "backups"
$ts   = Get-Date -Format "yyyyMMdd_HHmmss"

Write-Output "== 目标：$Data"
Get-ChildItem -LiteralPath $Data -Force | ForEach-Object {
    $size = 0
    if ($_.PSIsContainer) {
        $size = (Get-ChildItem -LiteralPath $_.FullName -Recurse -File -ErrorAction SilentlyContinue |
                 Measure-Object Length -Sum).Sum
    } else { $size = $_.Length }
    Write-Output ("  {0,-26} {1,12:N0}" -f $_.Name, $size)
}

if (-not $Apply) {
    Write-Output "`n（只预览。加 -Apply 才真备份并清空）"
    return
}

if (-not (Test-Path -LiteralPath $Bak)) { New-Item -ItemType Directory -Force -Path $Bak | Out-Null }
$zip = Join-Path $Bak ("before_pack_" + $ts + ".zip")
Compress-Archive -Path (Join-Path $Data "*") -DestinationPath $zip -CompressionLevel Optimal
$z = Get-Item -LiteralPath $zip
Write-Output ("`n[备份] {0}  {1:N0} 字节" -f $zip, $z.Length)

# 只清账本和运行时数据，不动 config（端口/密钥/大模型 Key 还在原处）
foreach ($n in @("state", "messages", "contacts", "targets", "logs",
                 "processed_ids.json", "seen_fingerprints.json")) {
    $p = Join-Path $Data $n
    if (Test-Path -LiteralPath $p) {
        Remove-Item -LiteralPath $p -Recurse -Force
        Write-Output "  删除 $n"
    }
}
Get-ChildItem -LiteralPath $Data -Force | ForEach-Object { Write-Output ("  留下 {0}" -f $_.Name) }
Write-Output "[清空] 完成（备份：$zip）"
