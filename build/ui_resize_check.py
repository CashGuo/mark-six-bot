# -*- coding: utf-8 -*-
"""验三件事：号码盘字号随窗口缩放、最近记账范围过滤、未解析只算本期。

不弹给人看，跑完打印结果。
"""

import sys
import time
import tkinter as tk

sys.path.insert(0, "D:\\Users\\Administrator\\Python\\mark-six-bot")
import server_gui

root = tk.Tk()
root.geometry("1600x1000")
app = server_gui.MainWindow(root)
root.update()
app.refresh_overview()
root.update()
time.sleep(0.2)
root.update()

print("大窗（1600x1000）号码盘字号：%s" % app._grid_font)

root.geometry("1050x640")
for _ in range(8):
    root.update()
    time.sleep(0.05)
    root.update()
print("小窗（1050x640）号码盘字号：%s" % app._grid_font)

rows = app.tv_rec.get_children()
print("默认范围：%s 条，提示=%s"
      % (len(rows), app.lb_rec_hint.cget("text")))

app.rec_scope.current(1)
app._on_rec_scope()
root.update()
rows3 = app.tv_rec.get_children()
print("最近 3 期：%s 条，提示=%s"
      % (len(rows3), app.lb_rec_hint.cget("text")))
for r in rows3:
    print("   ", app.tv_rec.item(r, "values")[:2])

print("本期未解析：chip=%s 按钮=%s"
      % (app.lb_kunp.cget("text"), app.btn_unp.cget("text")))

root.destroy()
