import io, os

p = r"D:\Users\Administrator\Python\mark-six-bot\server_gui.py"
npth = r"D:\Users\Administrator\Python\mark-six-bot\build\manual_dialog_new.py"

with io.open(p, encoding="utf-8") as f:
    src = f.read()
with io.open(npth, encoding="utf-8") as f:
    new = f.read()

i = src.index("class ManualBetDialog(")
j = src.index('if __name__ == "__main__":')
head = src[:i].rstrip() + "\n\n\n"
tail = src[j:]
out = head + new.rstrip() + "\n\n\n" + tail

with io.open(p, "w", encoding="utf-8", newline="\n") as f:
    f.write(out)
print("replaced: %d bytes -> %d bytes" % (len(src), len(out)))
