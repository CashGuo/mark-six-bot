# -*- coding: utf-8 -*-
"""验总览页新版面：两本号码盘并排、两本风险榜、最近记账带期号列。

只构建窗口读一遍数据，不弹给人看，跑完打印结果。
"""

import os
import sys
import tkinter as tk

sys.path.insert(0, "D:\\Users\\Administrator\\Python\\mark-six-bot")
import server_gui

root = tk.Tk()
root.withdraw()
app = server_gui.MainWindow(root)
app.refresh_overview()
root.update()

print("号码盘格数：%s" % {k: len(v) for k, v in app._grid_cells.items()})
print("风险行数：%s" % {k: len(app.tv_risk[k].get_children()) for k in app.tv_risk})
print("澳门 %s ／ 香港 %s ／ 总 %s"
      % (app.lb_mo.cget("text"), app.lb_hk.cget("text"), app.lb_grand.cget("text")))
print("本期 %s" % app.lb_issue.cget("text"))
print("最近记账列：%s" % (app.tv_rec["columns"],))
rows = app.tv_rec.get_children()
print("最近记账行数：%d" % len(rows))
if rows:
    vals = app.tv_rec.item(rows[0], "values")
    print("第一行（时间/期号/会话…）：%s" % (vals[:3],))

root.destroy()
