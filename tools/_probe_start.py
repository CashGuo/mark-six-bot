# -*- coding: utf-8 -*-
"""不动鼠标，直接跑一遍 GUI 里「启动服务 / 停止服务」的真实链路。

按钮本身是 Tk 回调，逻辑全在 start_server/stop_server 里；这里复刻同样的
调用顺序（self_cmd + Popen + health + taskkill），确认链路可用。
"""

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import server_gui as g

PORT = 8899
print("[probe] self_cmd() =", g.self_cmd())
print("[probe] app_dir    =", g.app_dir())
print("[probe] data_dir   =", g.data_dir())
print("[probe] pid_path   =", g.pid_path())

cmd = g.self_cmd() + ["--headless", "--quiet"]
log_dir = g.data_dir() / "logs"
log_dir.mkdir(parents=True, exist_ok=True)
out = open(log_dir / "console_probe.log", "a", encoding="utf-8")
p = subprocess.Popen(cmd, cwd=str(g.app_dir()), stdout=out,
                     stderr=subprocess.STDOUT, creationflags=0x08000000)
print("[probe] spawned pid =", p.pid)
g.pid_path().write_text(str(p.pid), encoding="utf-8")

ok = False
for _ in range(20):
    time.sleep(1)
    ok, msg = g.health(PORT)
    if ok:
        break
print("[probe] health after start:", ok, msg)
print("[probe] /status:", end=" ")
try:
    import urllib.request
    with urllib.request.urlopen("http://127.0.0.1:%d/status" % PORT, timeout=5) as r:
        print(r.read().decode("utf-8"))
except Exception as e:
    print("ERR", e)

# 复刻 stop_server
if g._pid_alive(p.pid):
    subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"],
                   capture_output=True, timeout=10, creationflags=0x08000000)
try:
    g.pid_path().unlink()
except Exception:
    pass
time.sleep(2)
print("[probe] health after stop:", g.health(PORT))
print("[probe] 完成")
