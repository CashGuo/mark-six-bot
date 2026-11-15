# -*- coding: utf-8 -*-
"""探针：验证消息流水 7 天清理 —— 老的该删，今天的必须留。"""
import os
import sys
import time

sys.path.insert(0, r"D:\Users\Administrator\Python\mark-six-bot")
import io

if sys.stdout is None:
    sys.stdout = io.StringIO()

import main as M  # noqa: E402

# 造一个 8 天前的假文件
old_day = time.strftime("%Y-%m-%d", time.localtime(time.time() - 8 * 86400))
old = M._msg_path(old_day)
old.write_text('{"ts":"old","content":"8 天前的老数据"}\n', encoding="utf-8")
old_mtime = time.time() - 8 * 86400
os.utime(old, (old_mtime, old_mtime))

today = M._msg_path(time.strftime("%Y-%m-%d"))
print("清理前文件：")
for p in sorted(M._msg_dir().glob("msg_*.jsonl")):
    print("   %s  %.1f 天前" % (p.name, (time.time() - p.stat().st_mtime) / 86400))

n = M.prune_messages()
print("删掉 =", n)
print("清理后文件：")
for p in sorted(M._msg_dir().glob("msg_*.jsonl")):
    print("   %s" % p.name)
print("老文件还在吗 =", old.exists())
print("今天的还在吗 =", today.exists())
print("保留天数 =", M.MSG_KEEP_DAYS)
