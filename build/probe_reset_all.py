# -*- coding: utf-8 -*-
"""验证 /reset 不传 account（清全部）的分支。

会短暂清空真账号，所以开头先整份备份、结尾恢复。跑完必须重启 main 进程，
否则内存里的去重集合和恢复后的文件对不上。
"""
import io
import json
import shutil
import sys
import urllib.request
from pathlib import Path

if sys.stdout is None:
    sys.stdout = io.StringIO()

DATA = Path(r"C:\Users\Administrator\AppData\Local\MarkSixBot\data")
REAL = "g18959290733"
STATE = DATA / "state" / REAL
HOLD = DATA / "_hold_fullreset" / REAL
TOKEN = (DATA / ".api_token").read_text(encoding="utf-8").strip()

if not STATE.exists():
    sys.stdout.write("真账号目录不存在，中止\n")
    sys.exit(1)

# 0) 先备份
if HOLD.exists():
    shutil.rmtree(HOLD)
HOLD.parent.mkdir(parents=True, exist_ok=True)
shutil.copytree(STATE, HOLD)
sys.stdout.write("备份 %s -> %s，%d 项\n" % (STATE, HOLD, len(list(STATE.iterdir()))))

# 1) 造两个假账号
FAKES = ("__fake_a__", "__fake_b__")
for name in FAKES:
    d = DATA / "state" / name
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    nums = {"%02d" % i: 0.0 for i in range(1, 50)}
    nums["01"] = 10.0
    (d / "numbers_state.json").write_text(json.dumps(nums), encoding="utf-8")
    (d / "records.jsonl").write_text(
        json.dumps({"rec_id": "f#1", "msg_id": "f", "applied": {"01": 10.0},
                    "total": 10.0, "issue": "2026-11-14"}, ensure_ascii=False) + "\n",
        encoding="utf-8")
    (d / "current_issue.txt").write_text("2026-11-14", encoding="utf-8")

# 2) 全清（不传 account）
req = urllib.request.Request("http://127.0.0.1:8899/reset", data=b"",
                             headers={"X-Msb-Token": TOKEN})
res = json.loads(urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "replace"))
sys.stdout.write("\n--- /reset（清全部）返回 ---\n%s\n" % json.dumps(res, ensure_ascii=False, indent=2))

# 3) 查三个账号是不是都清了
sys.stdout.write("\n--- 清完状态 ---\n")
for name in (REAL,) + FAKES:
    d = DATA / "state" / name
    sys.stdout.write("%-18s records=%s unparsed=%s issue=%s issues=%s 合计=%s\n" % (
        name,
        (d / "records.jsonl").exists(),
        (d / "unparsed.jsonl").exists(),
        (d / "current_issue.txt").exists(),
        (d / "issues").exists(),
        round(sum(json.loads((d / "numbers_state.json").read_text(encoding="utf-8")).values()), 2)
        if (d / "numbers_state.json").exists() else "无文件"))

for f, label in ((DATA / "processed_ids.json", "processed_ids"),
                 (DATA / "seen_fingerprints.json", "seen_fingerprints")):
    txt = f.read_text(encoding="utf-8") if f.exists() else "（文件不存在）"
    sys.stdout.write("%-18s = %s\n" % (label, txt.strip()[:120]))

# 4) 恢复真账号
shutil.rmtree(STATE, ignore_errors=True)
shutil.copytree(HOLD, STATE)
sys.stdout.write("\n已恢复 %s：%s\n" % (REAL, [p.name for p in STATE.iterdir()]))

# 5) 删假账号和它产生的留底
for name in FAKES:
    shutil.rmtree(DATA / "state" / name, ignore_errors=True)
    shutil.rmtree(DATA / "backups" / name, ignore_errors=True)
for d in (DATA / "backups" / REAL).glob("_clear_*"):
    shutil.rmtree(d, ignore_errors=True)
shutil.rmtree(HOLD.parent, ignore_errors=True)
sys.stdout.write("已清掉临时账号与测试留底\n")
