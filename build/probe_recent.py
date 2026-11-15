# -*- coding: utf-8 -*-
"""验证「最近记账」数据层：每条是否带 msg_id / rec_id / reversed。"""
import io
import json
import sys

if sys.stdout is None:
    sys.stdout = io.StringIO()

sys.path.insert(0, r"D:\Users\Administrator\Python\mark-six-bot")

import msb_view as v

for acc in (["g18959290733"] if v.all_accounts() else []):
    rows = v.recent_records(acc, 10)
    sys.stdout.write("account=%s rows=%d\n" % (acc, len(rows)))
    for r in rows:
        sys.stdout.write(json.dumps(
            {k: r.get(k) for k in ("ts", "rec_id", "msg_id", "type", "total",
                                   "reversed", "chat_name", "sender_name")},
            ensure_ascii=False) + "\n")
