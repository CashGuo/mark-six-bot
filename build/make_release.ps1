param(
    [string]$Version = "1.0.0",
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Exe  = Join-Path $Root "build\out\MarkSixBot.exe"
$Rel  = Join-Path $Root "build\release\MarkSixBot-Server"
$Tpl  = Join-Path $Root "build\release_template"

if (-not $SkipBuild) {
    & (Join-Path $Root "build\build_exe.ps1")
    if ($LASTEXITCODE -ne 0) { throw "编译失败，退出码 $LASTEXITCODE" }
}
if (-not (Test-Path -LiteralPath $Exe)) { throw "没有 $Exe（先跑 build\build_exe.ps1" }

if (Test-Path -LiteralPath $Rel) { Remove-Item -LiteralPath $Rel -Recurse -Force }
New-Item -ItemType Directory -Force -Path $Rel | Out-Null

# 交付就一个 EXE 加一份说明。配置、账本、日志都在 %LOCALAPPDATA%\MarkSixBot\，
# 换版本只换 EXE，客户目录不动。
Copy-Item -LiteralPath $Exe -Destination (Join-Path $Rel "MarkSixBot.exe") -Force
Copy-Item -LiteralPath (Join-Path $Tpl "从这里开始.txt") -Destination (Join-Path $Rel "从这里开始.txt") -Force

# 自检：交付包里绝不能出现大模型 Key。
# 推送密钥（wx_push_secret）是有意内置的出厂默认值 —— 它必须和采集端配对，
# 客户不该手工填；而大模型 Key 是客户自己的付费额度，只能客户自己填。
$bad = @()
Get-ChildItem -LiteralPath $Rel -Recurse -File | ForEach-Object {
    $txt = ""
    try { $txt = [IO.File]::ReadAllText($_.FullName) } catch { $txt = "" }
    if ($txt -match "sk-3690746a") {
        $bad += $_.Name
    }
}
if ($bad.Count -gt 0) { throw ("交付包里出现了密钥文件：" + ($bad -join ", ")) }

$zip = Join-Path $Root "build\release\MarkSixBot-Server-$Version.zip"
if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip -Force }
Compress-Archive -Path (Join-Path $Rel "*") -DestinationPath $zip -CompressionLevel Optimal

Write-Output "[release] 目录：$Rel"
Get-ChildItem -LiteralPath $Rel -Recurse -File |
    Sort-Object FullName |
    ForEach-Object { Write-Output ("  {0,-46} {1,12:N0} 字节" -f $_.FullName.Replace($Rel + "\", ""), $_.Length) }
$z = Get-Item -LiteralPath $zip
Write-Output ("[release] zip：{0}  {1:N0} 字节" -f $zip, $z.Length)
Write-Output "[release] 客户侧：双击 MarkSixBot.exe 就能用（推送密钥已内置）；要用大模型兜底再填自己的 API Key"
