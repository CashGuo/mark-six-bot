param(
    [string]$Out = "D:\Users\Administrator\Python\mark-six-bot\build\shot.png",
    [int]$TargetPid = 0
)
# Grab the main window of one process by PID. Avoids matching by title:
# this console's title contains Chinese, which mangles on the way to the API.

Add-Type -AssemblyName System.Drawing

Add-Type @"
using System;
using System.Runtime.InteropServices;
public class Win32Shot {
    [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr hdc, uint f);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
    [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int cmd);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
    [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
}
"@

$proc = Get-Process -Id $TargetPid -ErrorAction Stop
$h = $proc.MainWindowHandle
if ($h -eq 0) { "no-window"; exit 1 }
"hwnd=$h visible=" + [Win32Shot]::IsWindowVisible($h)

# 最小化的窗口 GetWindowRect 返回 -25600，先恢复再抓
if ([Win32Shot]::IsIconic($h)) {
    "iconic -> restoring"
    [void][Win32Shot]::ShowWindow($h, 9)
    Start-Sleep -Seconds 1
}
[void][Win32Shot]::SetForegroundWindow($h)
Start-Sleep -Milliseconds 400

$r = New-Object Win32Shot+RECT
[void][Win32Shot]::GetWindowRect($h, [ref]$r)
$w = $r.Right - $r.Left
$hh = $r.Bottom - $r.Top
"rect=$($r.Left),$($r.Top) ${w}x${hh}"

$bmp = New-Object System.Drawing.Bitmap($w, $hh)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$hdc = $g.GetHdc()
$ok = [Win32Shot]::PrintWindow($h, $hdc, 2)
$g.ReleaseHdc($hdc)
$g.Dispose()
if (-not $ok) { "printwindow-failed"; exit 2 }
$bmp.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)
$bmp.Dispose()
"saved=$Out " + (Get-Item -LiteralPath $Out).Length
exit 0
