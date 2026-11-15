# -*- coding: utf-8 -*-
"""验证「开机自动启动」写进注册表的那串命令行是否完整（源码模式下容易漏掉脚本名）。"""

import sys
import winreg
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from server_gui import self_cmd

KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
parts = self_cmd() + ["--headless", "--quiet"]
reg = " ".join('"%s"' % p if (" " in p or not p) else p for p in parts)

with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY, 0, winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE) as k:
    before = None
    try:
        before = winreg.QueryValueEx(k, "MarkSixBot")[0]
    except FileNotFoundError:
        pass
    print("[autorun] 原值:", before)
    winreg.SetValueEx(k, "MarkSixBot", 0, winreg.REG_SZ, reg)
    print("[autorun] 写入:", winreg.QueryValueEx(k, "MarkSixBot")[0])
    if before is None:
        winreg.DeleteValue(k, "MarkSixBot")
        print("[autorun] 已删除（恢复原状）")
    else:
        winreg.SetValueEx(k, "MarkSixBot", 0, winreg.REG_SZ, before)
        print("[autorun] 已还原")
