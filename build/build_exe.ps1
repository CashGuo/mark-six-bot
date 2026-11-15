param(
    [switch]$SkipClean
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Py   = Join-Path $Root "build\venv\Scripts\python.exe"
$Out  = Join-Path $Root "build\out"

if (-not (Test-Path -LiteralPath $Py)) {
    throw "找不到编译用 venv：$Py（先建 build\venv 并装 nuitka flask openai python-dotenv LunarDate psutil）"
}
foreach ($m in @("flask", "openai", "dotenv", "lunardate", "psutil")) {
    & $Py -c "import $m" 2>$null
    if ($LASTEXITCODE -ne 0) { throw "build\venv 里缺模块：$m" }
}

# 旧 EXE 正在跑会让 Remove-Item 报 Access denied
Get-Process -Name "MarkSixBot" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Milliseconds 400

if (-not $SkipClean -and (Test-Path -LiteralPath $Out)) {
    Remove-Item -LiteralPath $Out -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $Out | Out-Null

$nuitkaArgs = @(
    "-m", "nuitka",
    "--standalone", "--onefile",
    "--windows-console-mode=disable",
    "--assume-yes-for-downloads",
    "--output-dir=$Out",
    "--output-filename=MarkSixBot.exe",
    "--include-data-dir=$Root\templates=templates",
    "--enable-plugin=tk-inter",
    # openai SDK 里有靠 try/import 和间接引用才加载的子模块，静态分析会漏掉
    # （实测漏了 openai.resources.chat.chat，表现为运行时「AI 未返回答复」）。
    # 整个包强制包含最稳，代价是体积和编译时间。
    "--include-package=openai",
    "--include-package=httpx2",
    "--include-package=httpcore2",
    "--python-flag=-O",
    "--nofollow-import-to=pytest,tests,unittest",
    "--company-name=MarkSixBot",
    "--product-name=MarkSixBot server",
    "--file-version=1.0.0.0",
    "--product-version=1.0.0.0",
    "--quiet",
    "$Root\main.py"
)

Push-Location $Root
try {
    & $Py @nuitkaArgs
    if ($LASTEXITCODE -ne 0) { throw "Nuitka 编译失败，退出码 $LASTEXITCODE" }
} finally {
    Pop-Location
}

$exe = Join-Path $Out "MarkSixBot.exe"
if (-not (Test-Path -LiteralPath $exe)) { throw "没生成 $exe" }

# 配置不再随包放：服务端第一次启动会在 %LOCALAPPDATA%\MarkSixBot\config\
# 自己写一份出厂空壳（见 msb_paths.adopt_config），EXE 放只读目录也能改配置。
$f = Get-Item -LiteralPath $exe
Write-Output ("[build] 完成：{0}  {1:N0} 字节  {2}" -f $exe, $f.Length, $f.LastWriteTime)
Write-Output "[build] 运行后数据/配置目录：%LOCALAPPDATA%\MarkSixBot\（config\ 配置，data\ 账本与日志）"
