# -*- coding: utf-8 -*-
"""验证 /reset 是不是「全清」：用一个临时账号，不碰真账本。

查四件事：
1. 清之前自动留底（backups/<账号>/_clear_<时间戳>/）
2. records.jsonl / unparsed.jsonl / current_issue.txt / issues/ 全部消失
3. numbers_state.json 49 个号归零
4. 接口返回的 removed 列表和实际文件状态一致
"""
import io
import json
import os
import shutil
import sys
import urllib.request
from pathlib import Path

if sys.stdout is None:
    sys.stdout = io.StringIO()

DATA = Path(r"C:\Users\Administrator\AppData\Local\MarkSixBot\data")
ACC = "__reset_test__"
STATE = DATA / "state" / ACC
BACKUP = DATA / "backups" / ACC
PORT = 8899
TOKEN = (DATA / ".api_token").read_text(encoding="utf-8").strip()

# ---- 造一个像模像样的假账号 ----
if STATE.exists():
    shutil.rmtree(STATE)
STATE.mkdir(parents=True)
nums = {"%02d" % i: 0.0 for i in range(1, 50)}
nums["01"] = 20.0
nums["13"] = 12.5
(STATE / "numbers_state.json").write_text(json.dumps(nums, indent=2), encoding="utf-8")
rec = {"rec_id": "t-1#1", "msg_id": "t-1", "ts": "2026-11-13 10:00:00",
       "applied": {"01": 20.0}, "total": 20.0, "issue": "2026-11-14", "account": ACC}
(STATE / "records.jsonl").write_text(json.dumps(rec, ensure_ascii=False) + "\n", encoding="utf-8")
(STATE / "unparsed.jsonl").write_text(json.dumps({"msg_id": "t-2", "text": "乱写的"},
                                                 ensure_ascii=False) + "\n", encoding="utf-8")
(STATE / "current_issue.txt").write_text("2026-11-14", encoding="utf-8")
(STATE / "issues").mkdir()
(STATE / "issues" / "2026-11-12.json").write_text('{"issue":"2026-11-12"}', encoding="utf-8")

sys.stdout.write("--- 造好之后 ---\n")
for p in sorted(STATE.rglob("*")):
    sys.stdout.write("  %s %d\n" % (p.name, p.stat().st_size if p.is_file() else 0))

# ---- 调接口 ----
url = "http://127.0.0.1:%d/reset?account=%s" % (PORT, ACC)
req = urllib.request.Request(url, data=b"", headers={"X-Msb-Token": TOKEN})
with urllib.request.urlopen(req, timeout=10) as r:
    res = json.loads(r.read().decode("utf-8", "replace"))
sys.stdout.write("\n--- /reset 返回 ---\n%s\n" % json.dumps(res, ensure_ascii=False, indent=2))

# ---- 查结果 ----
sys.stdout.write("\n--- 清完的 state ---\n")
for p in sorted(STATE.rglob("*")):
    sys.stdout.write("  %s %d\n" % (p.name, p.stat().st_size if p.is_file() else 0))
n2 = json.loads((STATE / "numbers_state.json").read_text(encoding="utf-8"))
sys.stdout.write("归零后合计=%.2f 非零号数=%d\n"
                 % (sum(n2.values()), sum(1 for v in n2.values() if v)))
sys.stdout.write("records 还在吗: %s\n" % (STATE / "records.jsonl").exists())
sys.stdout.write("unparsed 还在吗: %s\n" % (STATE / "unparsed.jsonl").exists())
sys.stdout.write("current_issue 还在吗: %s\n" % (STATE / "current_issue.txt").exists())
sys.stdout.write("issues 还在吗: %s\n" % (STATE / "issues").exists())

r1 = res.get("results") or [{}]
sys.stdout.write("留底目录: %s\n" % r1[0].get("backup"))
bk = r1[0].get("backup") or ""
if bk and Path(bk).exists():
    sys.stdout.write("留底内容: %s\n" % [p.name for p in Path(bk).iterdir()])

# ---- 收尾：把这个假账号彻底删掉，别在界面上留幽灵账号 ----
shutil.rmtree(STATE, ignore_errors=True)
shutil.rmtree(BACKUP, ignore_errors=True)
sys.stdout.write("\n已清掉临时账号 %s\n" % ACC)
