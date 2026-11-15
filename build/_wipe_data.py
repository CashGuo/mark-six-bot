# -*- coding: utf-8 -*-
"""清空业务数据：账本、流水、消息、未解析、备份、日志、去重集合。

不动的东西：
  · config\\server.json      服务端配置
  · config\\wx_clients.json  白名单
  · license.key              采集端授权码
  · targets.json             监听对象勾选
  · _wxdata                  微信库缓存（删了要重新解一遍，很慢）
"""
import io
import os
import shutil
import sys

if sys.stdout is None:
    sys.stdout = io.StringIO()

MSB = r"C:\Users\Administrator\AppData\Local\MarkSixBot"
DATA = os.path.join(MSB, "data")
WX = r"C:\Users\Administrator\AppData\Local\WxPushTool"


def rm_dir(p):
    if not os.path.isdir(p):
        return 0
    n = 0
    for name in os.listdir(p):
        fp = os.path.join(p, name)
        try:
            if os.path.isdir(fp):
                shutil.rmtree(fp)
            else:
                os.remove(fp)
            n += 1
        except Exception as e:
            print("  ! 删不掉 %s：%s" % (fp, e))
    return n


def rm_file(p):
    try:
        if os.path.isfile(p):
            os.remove(p)
            return True
    except Exception as e:
        print("  ! 删不掉 %s：%s" % (p, e))
    return False


print("=== 服务端 %s ===" % DATA)
for sub in ("state", "messages", "contacts", "targets", "backups", "logs"):
    n = rm_dir(os.path.join(DATA, sub))
    print("  %-10s 清了 %d 项" % (sub, n))

for fn in ("processed_ids.json", "seen_fingerprints.json", ".bot.lock"):
    if rm_file(os.path.join(DATA, fn)):
        print("  %-22s 已删" % fn)

print("\n=== 采集端 %s ===" % WX)
gone = 0
for name in os.listdir(WX):
    if name.startswith("messages_") and name.endswith(".log"):
        if rm_file(os.path.join(WX, name)):
            gone += 1
            print("  删 %s" % name)
print("  消息日志 %d 个" % gone)
n = rm_dir(os.path.join(WX, "logs"))
print("  logs 清了 %d 项" % n)
if rm_file(os.path.join(WX, "contacts_local.json")):
    print("  contacts_local.json 已删（下次启动会重新扫微信库生成）")
rm_file(os.path.join(WX, "collector.pid"))

print("\n=== 剩下什么 ===")
for label, p in (("服务端 data", DATA), ("采集端", WX)):
    print("  %s:" % label)
    if os.path.isdir(p):
        for name in sorted(os.listdir(p)):
            fp = os.path.join(p, name)
            size = os.path.getsize(fp) if os.path.isfile(fp) else 0
            print("    %-24s %s" % (name + ("/" if os.path.isdir(fp) else ""),
                                   "dir" if os.path.isdir(fp) else "%d B" % size))
