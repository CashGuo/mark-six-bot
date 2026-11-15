# -*- coding: utf-8 -*-
"""预览：服务端控制台的「消息」页，供抓图用。标题纯 ASCII，避免 PS 传参乱码。"""
import io
import os
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
root.title("PreviewMsg")
root.update_idletasks()
win.nb.select(win.tab_log)
win.refresh_log(force=True)
root.update_idletasks()

root.after(40000, root.destroy)
root.mainloop()
