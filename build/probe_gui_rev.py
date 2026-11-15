# -*- coding: utf-8 -*-
"""探针：造一笔真账 → 起控制台 → 选行 → 走 reverse_selected() → 校验账本。

messagebox 全部打桩，不弹窗；只验证"按钮背后的那条链路"走得通。
"""
import io
import sys
import time

sys.path.insert(0, r"D:\Users\Administrator\Python\mark-six-bot")
if sys.stdout is None:
    sys.stdout = io.StringIO()

import tkinter as tk  # noqa: E402

import server_gui as S  # noqa: E402
from probe_reverse import post_bet  # noqa: E402
import main as M  # noqa: E402

ACC = "g18959290733"
NUM = "06"

mid = "probe-gui-%d" % int(time.time())
r = post_bet("06 100", mid)
print("造账 =", r)
print("06 =", M.load_data(ACC).get(NUM))

# 打桩弹窗
asks = []
S.messagebox.showinfo = lambda *a, **k: asks.append(("info", a))
S.messagebox.showwarning = lambda *a, **k: asks.append(("warn", a))
S.messagebox.showerror = lambda *a, **k: asks.append(("err", a))
S.messagebox.askyesno = lambda *a, **k: True

root = tk.Tk()
root.withdraw()
win = S.MainWindow(root)
root.update_idletasks()
win.refresh_log(force=True)
root.update_idletasks()

tv = win.tv_msg
rows = tv.get_children()
print("行数 =", len(rows))
target = None
for iid in rows:
    if win._msg_mid.get(iid) == mid:
        target = iid
print("找到目标行 =", target, " 结果 =", win._msg_res.get(target),
      " 已撤销 =", win._msg_rev.get(target))

tv.selection_set(target)
root.update_idletasks()
win._on_msg_select()
print("按钮状态 =", str(win.btn_rev["state"]), " 提示 =", win.lb_rev_hint["text"])

win.reverse_selected()
root.update_idletasks()

print("弹窗记录 =", asks)
print("06 现在 =", M.load_data(ACC).get(NUM))

win.refresh_log(force=True)
root.update_idletasks()
print("撤销后该行结果 =", tv.item(target, "values")[-1])
print("撤销后已撤销标记 =", win._msg_rev.get(target))

code, _ = None, None
root.destroy()
