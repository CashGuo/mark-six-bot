"""手工记账对话框的解析口径自检（只跑纯函数，不弹窗）。"""
import io
import os
import sys

sys.stdout = io.StringIO() if sys.stdout is None else sys.stdout
sys.path.insert(0, r"D:\Users\Administrator\Python\mark-six-bot")

import msb_view
import server_gui as sg


def main():
    mapping = msb_view.zodiac_year()[2]
    d = sg.ManualBetDialog.__new__(sg.ManualBetDialog)
    d.mapping = mapping
    print("money(30.0)  =", repr(sg.ManualBetDialog._money(30.0)))
    print("money(32.5)  =", repr(sg.ManualBetDialog._money(32.5)))
    print("fmt_money(30)=", repr(sg.fmt_money(30.0)))
    for line in ["06 13 25 = 30", "牛 = 100", "鼠 马 = 200",
                 "红波 10", "0头 3", "6 13 25", "50 = 10", "牛 100", "各 = 5"]:
        r = d._parse(line)
        if r["ok"]:
            print("%-16s OK  %s" % (line, r["note"]))
        else:
            print("%-16s ERR %s" % (line, r["err"]))


if __name__ == "__main__":
    main()
    out = sys.stdout.getvalue() if isinstance(sys.stdout, io.StringIO) else ""
    with open(os.path.join(os.path.dirname(__file__), "probe_manual_out.txt"),
              "w", encoding="utf-8") as f:
        f.write(out)
    print(out)
