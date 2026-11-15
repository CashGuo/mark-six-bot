# -*- coding: utf-8 -*-
"""探针：白名单页 4 个页签、表格内容、模式文案、启停后文件真实变化。"""
import io
import json
import sys

sys.path.insert(0, r"D:\Users\Administrator\Python\mark-six-bot")
if sys.stdout is None:
    sys.stdout = io.StringIO()
else:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import tkinter as tk  # noqa: E402

import server_gui as S  # noqa: E402

root = tk.Tk()
root.withdraw()                     # 不弹窗，纯读几何和文本
win = S.MainWindow(root)
root.update_idletasks()

print("页签数量 =", len(win.nb.tabs()))
for _, lab, _f in win.nb._items:
    print("  页签:", lab.cget("text"))

print("白名单文件 =", S.WHITELIST_PATH, "存在 =", S.WHITELIST_PATH.exists())
win.refresh_white()
print("模式文案   =", win.lb_wl_mode.cget("text"))
print("模式颜色   =", win.lb_wl_mode.cget("fg"))
print("说明       =", win.lb_wl_sub.cget("text").replace("\n", " | "))

cols = [win.tv_wl.heading(c)["text"] for c in win.tv_wl["columns"]]
print("表头       =", " / ".join(cols))

rows = win.tv_wl.get_children()
print("行数       =", len(rows))
for r in rows:
    print("   ", win.tv_wl.item(r, "values"), "tag=", win.tv_wl.item(r, "tags"))

# 停用 → 看文件里真的变了 → 再启用回来
if rows:
    win.tv_wl.selection_set(rows[0])      # 不选中就没有"当前行"，停用会静默返回
    root.update_idletasks()
    wid = win.tv_wl.item(rows[0], "values")[0]
    win._white_set(False)
    with open(S.WHITELIST_PATH, "r", encoding="utf-8") as f:
        d = json.load(f)
    got = [x.get("enabled") for x in d["clients"] if x.get("wechat_id") == wid]
    print("停用后文件 enabled =", got, " 页面文案 =", win.lb_wl_mode.cget("text"))
    win._white_set(True)
    with open(S.WHITELIST_PATH, "r", encoding="utf-8") as f:
        d = json.load(f)
    print("启用后文件 enabled =",
          [x.get("enabled") for x in d["clients"] if x.get("wechat_id") == wid])

root.destroy()
print("OK")
