param(
    [int]$Hwnd = 0,
    [int]$ClickX = 0,
    [int]$ClickY = 0,
    [string]$TextFile = "",
    [switch]$DryRun
)
# 往微信当前会话发一句话：激活窗口 -> 点输入框 -> 粘贴 -> 回车。
# 脚本本身保持纯 ASCII，中文回复内容从 -TextFile 指定的 UTF-8 文件读，
# 免得经过命令行编码走样。

Add-Type @"
using System;
using System.Runtime.InteropServices;
public class WxIn {
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c);
    [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr h);
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
    [DllImport("user32.dll")] public static extern void mouse_event(uint f, uint dx, uint dy, uint d, int t);
    [DllImport("user32.dll")] public static extern void keybd_event(byte vk, byte scan, uint f, int extra);
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
    [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr h, System.Text.StringBuilder s, int n);
}
"@

$h = [IntPtr]$Hwnd
if ($h -eq [IntPtr]::Zero) { "no-hwnd"; exit 1 }

if ([WxIn]::IsIconic($h)) { "restoring"; [void][WxIn]::ShowWindow($h, 9); Start-Sleep -Milliseconds 800 }
[void][WxIn]::BringWindowToTop($h)
[void][WxIn]::SetForegroundWindow($h)
Start-Sleep -Milliseconds 600

$fg = [WxIn]::GetForegroundWindow()
if ($fg -ne $h) { "foreground-mismatch fg=$fg want=$h"; exit 2 }
"foreground-ok"

if ($ClickX -gt 0) {
    [void][WxIn]::SetCursorPos($ClickX, $ClickY)
    Start-Sleep -Milliseconds 250
    [WxIn]::mouse_event(0x0002, 0, 0, 0, 0)
    Start-Sleep -Milliseconds 90
    [WxIn]::mouse_event(0x0004, 0, 0, 0, 0)
    Start-Sleep -Milliseconds 500
    "clicked $ClickX,$ClickY"
}

if ($DryRun) { "dry-run-stop"; exit 0 }

if (-not (Test-Path -LiteralPath $TextFile)) { "text-file-missing"; exit 3 }
$text = (Get-Content -LiteralPath $TextFile -Raw -Encoding utf8).TrimEnd()
"text=[" + $text + "]"
Set-Clipboard -Value $text
Start-Sleep -Milliseconds 350

# Ctrl+V
[WxIn]::keybd_event(0x11, 0, 0, 0)
[WxIn]::keybd_event(0x56, 0, 0, 0)
Start-Sleep -Milliseconds 90
[WxIn]::keybd_event(0x56, 0, 2, 0)
[WxIn]::keybd_event(0x11, 0, 2, 0)
Start-Sleep -Milliseconds 700
"pasted"

# Enter
[WxIn]::keybd_event(0x0D, 0, 0, 0)
Start-Sleep -Milliseconds 90
[WxIn]::keybd_event(0x0D, 0, 2, 0)
Start-Sleep -Milliseconds 900
"sent"
exit 0
