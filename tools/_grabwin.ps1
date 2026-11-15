param(
    [string]$Out = "D:\Users\Administrator\Python\mark-six-bot\build\gui_win.png",
    [string]$ProcName = "python"
)

Add-Type -AssemblyName System.Drawing, System.Windows.Forms

Add-Type -TypeDefinition @'
using System;
using System.Text;
using System.Runtime.InteropServices;
public class WinGrab {
    [StructLayout(LayoutKind.Sequential)]
    public struct RECT { public int Left; public int Top; public int Right; public int Bottom; }

    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
    [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr hdc, uint flags);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
    [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr h, IntPtr after, int x, int y, int cx, int cy, uint flags);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int cmd);
    [DllImport("user32.dll")] public static extern IntPtr SetForegroundWindow(IntPtr h);
    [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
    [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
}
'@

# 不声明 DPI 感知时 GetWindowRect 返回逻辑坐标（如 862x614），而窗口物理尺寸
# 是 1080x767 —— PrintWindow 只能抓到左上角一块，底部按钮条永远看不到。
[WinGrab]::SetProcessDPIAware() | Out-Null

$p = Get-Process -Name $ProcName -ErrorAction SilentlyContinue |
     Where-Object { $_.MainWindowHandle -ne 0 -and $_.MainWindowTitle -like "*MarkSixBot*" } |
     Select-Object -First 1
if (-not $p) { Write-Output "no-window"; exit 1 }

$h = $p.MainWindowHandle
$sb = New-Object System.Text.StringBuilder 512
[WinGrab]::GetWindowText($h, $sb, 512) | Out-Null
Write-Output ("title=" + $sb.ToString())
Write-Output ("visible=" + [WinGrab]::IsWindowVisible($h))

[WinGrab]::ShowWindow($h, 9) | Out-Null
[WinGrab]::SetForegroundWindow($h) | Out-Null
[WinGrab]::SetWindowPos($h, [IntPtr]::new(-1), 0, 0, 0, 0, 0x0003) | Out-Null
Start-Sleep -Milliseconds 600

$r = New-Object WinGrab+RECT
[WinGrab]::GetWindowRect($h, [ref]$r) | Out-Null
$w = $r.Right - $r.Left
$hh = $r.Bottom - $r.Top
Write-Output ("rect=" + $r.Left + "," + $r.Top + " " + $w + "x" + $hh)

$bmp = New-Object System.Drawing.Bitmap $w, $hh
$g = [System.Drawing.Graphics]::FromImage($bmp)
$hdc = $g.GetHdc()
$ok = [WinGrab]::PrintWindow($h, $hdc, 2)
$g.ReleaseHdc($hdc)
$g.Dispose()
$bmp.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)
$bmp.Dispose()
Write-Output ("printwindow=" + $ok)
Write-Output ("saved=" + $Out + " " + (Get-Item -LiteralPath $Out).Length)
