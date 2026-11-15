# -*- coding: utf-8 -*-
"""程序化驱动「文本记账」弹窗，验它的解析/记账两个按钮接得对不对。

不点鼠标：Tk 自绘按钮不认 PostMessage，所以直接调方法、读结果标签。
"""

import os
import sys
import tkinter as tk

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import server_gui as g

root = tk.Tk()
root.withdraw()
dlg = g.QuickBetDialog(root, "selftest", chats=["老郭", "张三"])
root.update()

cases = [
    ("鼠50", "parse", ""),
    ("牛马各10", "parse", ""),
    ("鼠5尾各10", "parse", ""),
    ("平特鼠100", "parse", ""),
    ("乱七八糟 30", "parse", ""),
    ("今天天气不错", "parse", ""),
    ("鼠50", "record", "老郭"),
    ("小双20", "record", "张三"),
   ]

for text, act, chat in cases:
    dlg.txt.delete("1.0", "end")
    dlg.txt.insert("1.0", text)
    if act == "parse":
        dlg.do_parse()
    else:
        # 下拉是真实账号列表，这里强制指到测试账号，别写脏真实账本
        dlg.v_acc.set("selftest")
        dlg.v_chat.set(chat)
        dlg.v_loop.set(True)       # 别让「记完自动关窗」打断这轮自检
        dlg.do_record()
    root.update()
    body = dlg.lb_result.cget("text").replace("\n", " ⏎ ")
    print("%-12s %-7s %-9s %s" % (text, act, chat or "-", body))

dlg.destroy()
root.destroy()
print("弹窗自检完成")
