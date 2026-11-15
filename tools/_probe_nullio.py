# -*- coding: utf-8 -*-
"""复刻 --windows-console-mode=disable 的运行环境（sys.stdout/stderr = None），
验证 main.py 在"没有控制台 + 配置不全"时不会再抛 AttributeError 闪退。

做法：临时把 config\\server.json 和 .env 挪走（这样 config_problems() 必然非空，
走到 print(msg) 那条路径），跑完 finally 挪回来。
"""
import runpy
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACK = ROOT / "build" / "_cfg_backup"
CFG = ROOT / "config" / "server.json"
ENV = ROOT / ".env"

BACK.mkdir(parents=True, exist_ok=True)
moved = []
for p in (CFG, ENV):
    if p.exists():
        dst = BACK / p.name
        if dst.exists():
            dst.unlink()
        shutil.move(str(p), str(dst))
        moved.append(p)

real_out = sys.__stdout__
real_err = sys.__stderr__
sys.path.insert(0, str(ROOT))       # runpy 不会把项目根塞进 sys.path
code = None
try:
    sys.stdout = None
    sys.stderr = None
    sys.argv = ["main.py", "--headless", "--quiet"]
    runpy.run_path(str(ROOT / "main.py"), run_name="__main__")
except SystemExit as e:
    code = e.code
except BaseException as e:                                  # noqa: BLE001
    code = "EXC:%r" % (e,)
finally:
    sys.stdout = real_out
    sys.stderr = real_err
    for p in moved:
        src = BACK / p.name
        if src.exists():
            shutil.move(str(src), str(p))

print("verdict:", "OK (没问题，正常返回)" if code == 2 else "BAD")
print("exit  =", code)
