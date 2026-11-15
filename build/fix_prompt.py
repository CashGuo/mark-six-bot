# -*- coding: utf-8 -*-
"""把 main.py 里 build_system_prompt 的金额说明整段换成新口径。

旧口径是"生肖 amount = 这个肖总共多少，程序摊到各号"，与客户实际报账相反；
新口径是"amount = 每个号各多少"。不换过来，模型会输出摊开后的数，账就少了一半。
"""

p = r"D:\Users\Administrator\Python\mark-six-bot\main.py"
src = open(p, "r", encoding="utf-8").read()
lines = src.split("\n")

NEW = '''- amount 一律是**每个号码各多少**，照用户报的原数填，不要自己乘、也不要摊开：
  · 生肖："牛100" → amount 100（牛 4 个号各 100，合计 400）；"鼠50" → amount 50（合计 200）
  · 波色 / 波色单双 / 大小单双 / 头组：同样每个号各 amount
  · 直接号码："1*13*34*7各10" → amount 10（每个号各 10）
- 几个名字合写、"各"在中间时，amount 是**每个名字各多少**，组内每个号仍是这个数：
  "牛马各100" → 一个 item，names ["牛","马"]，amount 100（9 个号各 100，合计 900）
  "马鼠鸡各号15" → 一个 item，names ["马","鼠","鸡"]，amount 15'''

i = next(k for k, l in enumerate(lines) if "amount 照用户报的原数填" in l)
j = next(k for k, l in enumerate(lines) if "马鼠鸡各号15" in l and k >= i)
lines[i:j + 1] = NEW.split("\n")

open(p, "w", encoding="utf-8", newline="\n").write("\n".join(lines))
print("替换完成：原第 %d~%d 行，换成 %d 行" % (i + 1, j + 1, len(NEW.split("\n"))))
