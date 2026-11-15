# -*- coding: utf-8 -*-
"""探针：确认服务端控制台只剩 3 个页签，且不再有「监听对象」相关的属性/控件。"""
import io
import sys

sys.path.insert(0, r"D:\Users\Administrator\Python\mark-six-bot")
if sys.stdout is None:
    sys.stdout = io.StringIO()
else:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import tkinter as tk  # noqa: E402

import server_gui as S  # noqa: E402

root = tk.Tk()
win = S.MainWindow(root)
root.update_idletasks()
root.update()

print("页签数量 =", len(win.nb.tabs()))
for holder, lab, frame in win.nb._items:
    print("  页签:", lab.cget("text"))

for attr in ("tv_lst", "lb_lstmeta", "lst_acc_var", "lb_sel"):
    print("  %-14s 存在=%s" % (attr, hasattr(win, attr)))

for attr in ("render_list", "load_contacts", "save_targets", "save_all_mode"):
    print("  %-14s 方法存在=%s" % (attr, hasattr(win, attr)))

root.destroy()
print("OK")
