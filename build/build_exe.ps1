param(
    [switch]$SkipClean,
    # 产物文件名里的日期戳。默认当天，如 MarkSixBot-20261118.exe
    [string]$Stamp = (Get-Date -Format "yyyyMMdd")
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
    "--output-filename=MarkSixBot-$Stamp.exe",
    "--include-data-dir=$Root\templates=templates",
    # 解析层外置：bet_rules / manual_plays 不打进 EXE（--nofollow-import-to），
    # 运行时由 patch_loader 从用户目录 patch\ 或包内 patch_seed\ 加载。
    # 原始脚本同时作为数据文件塞进包里：既当兜底，也是首次启动释放到用户目录的模板。
    "--include-data-files=$Root\bet_rules.py=patch_seed\bet_rules.py",
    "--include-data-files=$Root\manual_plays.py=patch_seed\manual_plays.py",
    "--nofollow-import-to=bet_rules,manual_plays",
    "--enable-plugin=tk-inter",
    # openai SDK 里有靠 try/import 和间接引用才加载的子模块，静态分析会漏掉
    # （实测漏了 openai.resources.chat.chat，表现为运行时「AI 未返回答复」）。
    # 整个包强制包含最稳，代价是体积和编译时间。
    "--include-package=openai",
    "--include-package=httpx2",
    "--include-package=httpcore2",
    "--python-flag=-O",
    "--nofollow-import-to=pytest,tests,unittest",
    # 这台机器 15.7G 内存、打包时可用 4G 左右，Nuitka 默认用 zstd level 22
    # 多线程压 121MB 的 dist，实测直接 "Allocation error : not enough memory"
    # 打包失败。--low-memory 会把压缩级别降到 3、单线程，包大一点但一定能出。
    "--low-memory",
    # 编译阶段默认只起一个 cl.exe，14 核机器上 CPU 只占 1%，2461 个编译单元要跑 7 小时。
    # 这里显式给并发；内存 15.7G 编译期够用（吃内存的是最后的 onefile 压缩，不是编译）。
    "--jobs=$env:NUMBER_OF_PROCESSORS",
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

$exe = Join-Path $Out "MarkSixBot-$Stamp.exe"
if (-not (Test-Path -LiteralPath $exe)) { throw "没生成 $exe" }

# 配置不再随包放：服务端第一次启动会在 %LOCALAPPDATA%\MarkSixBot\config\
# 自己写一份出厂空壳（见 msb_paths.adopt_config），EXE 放只读目录也能改配置。
$f = Get-Item -LiteralPath $exe
Write-Output ("[build] 完成：{0}  {1:N0} 字节  {2}" -f $exe, $f.Length, $f.LastWriteTime)
Write-Output "[build] 运行后数据/配置目录：%LOCALAPPDATA%\MarkSixBot\（config\ 配置，data\ 账本与日志）"
