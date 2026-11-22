# -*- coding: utf-8 -*-
"""给 Windows EXE 注入图标资源（替代 Nuitka 的 --windows-icon-from-ico 注入）。

用法：python inject_icon.py <exe路径> <ico路径>
"""
import ctypes
import struct
import sys
import time
from ctypes import wintypes

RT_ICON = 3
RT_GROUP_ICON = 14

k32 = ctypes.windll.kernel32
k32.BeginUpdateResourceW.argtypes = (wintypes.LPCWSTR, wintypes.BOOL)
k32.BeginUpdateResourceW.restype = wintypes.HANDLE
k32.UpdateResourceW.argtypes = (
    wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
    wintypes.WORD, ctypes.c_void_p, wintypes.DWORD,
)
k32.UpdateResourceW.restype = wintypes.BOOL
k32.EndUpdateResourceW.argtypes = (wintypes.HANDLE, wintypes.BOOL)
k32.EndUpdateResourceW.restype = wintypes.BOOL
k32.EnumResourceNamesW.argtypes = (
    wintypes.HMODULE, ctypes.c_void_p, ctypes.c_void_p, wintypes.LPARAM,
)
k32.EnumResourceNamesW.restype = wintypes.BOOL
k32.LoadLibraryExW.argtypes = (wintypes.LPCWSTR, ctypes.c_void_p, wintypes.DWORD)
k32.LoadLibraryExW.restype = wintypes.HMODULE
k32.FreeLibrary.argtypes = (wintypes.HMODULE,)
k32.FreeLibrary.restype = wintypes.BOOL


def read_ico(path):
    """解析 .ico，返回 (每张图的原始字节列表, group 结构里的 entry 列表)"""
    with open(path, "rb") as f:
        raw = f.read()
    reserved, typ, count = struct.unpack_from("<HHH", raw, 0)
    if reserved != 0 or typ != 1:
        raise ValueError("不是合法的 ico 文件：%r" % path)
    images = []
    entries = []
    for i in range(count):
        w, h, colors, res, planes, bits, size, off = struct.unpack_from(
            "<BBBBHHII", raw, 6 + 16 * i)
        images.append(raw[off:off + size])
        entries.append((w, h, colors, res, planes, bits, size))
    return images, entries


def build_group(entries, first_id=1):
    parts = [struct.pack("<HHH", 0, 1, len(entries))]
    for idx, (w, h, colors, res, planes, bits, size) in enumerate(entries):
        parts.append(struct.pack(
            "<BBBBHHIH", w, h, colors, res, planes, bits, size, first_id + idx))
    return b"".join(parts)


def _write_once(exe, images, group):
    h = k32.BeginUpdateResourceW(exe, False)
    if not h:
        raise ctypes.WinError()
    try:
        for idx, img in enumerate(images, 1):
            if not k32.UpdateResourceW(h, RT_ICON, idx, 0, img, len(img)):
                raise ctypes.WinError()
        if not k32.UpdateResourceW(h, RT_GROUP_ICON, 1, 0, group, len(group)):
            raise ctypes.WinError()
    except Exception:
        # 失败必须显式放弃更新，否则句柄一直占着文件，后面的重试全会失败
        k32.EndUpdateResourceW(h, True)
        raise
    if not k32.EndUpdateResourceW(h, False):
        raise ctypes.WinError()


def inject(exe, ico, attempts=12, delay=2.0):
    images, entries = read_ico(ico)
    group = build_group(entries)
    last = None
    for n in range(1, attempts + 1):
        try:
            _write_once(exe, images, group)
            print("图标写入成功（第 %d 次尝试）" % n)
            return True
        except Exception as e:      # noqa: BLE001
            last = e
            print("第 %d 次失败：%s" % (n, e))
            time.sleep(delay)
    print("全部尝试失败，最后一次：%s" % last)
    return False


def verify(exe):
    """确认 exe 里确实存在 RT_GROUP_ICON / RT_ICON 资源"""
    flags = 0x2 | 0x20          # LOAD_LIBRARY_AS_DATAFILE | AS_IMAGE_RESOURCE
    hmod = k32.LoadLibraryExW(exe, None, flags)
    if not hmod:
        print("LoadLibraryEx 失败:", ctypes.WinError())
        return {}
    found = {}
    cb_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMODULE, ctypes.c_void_p,
                                 ctypes.c_void_p, wintypes.LPARAM)

    def make(kind):
        def _cb(h, t, name, lparam):
            found[kind] = found.get(kind, 0) + 1
            return True
        return cb_type(_cb)

    for kind in (RT_ICON, RT_GROUP_ICON):
        k32.EnumResourceNamesW(hmod, ctypes.c_void_p(kind), make(kind), 0)
    k32.FreeLibrary(hmod)
    return found


if __name__ == "__main__":
    exe_path, ico_path = sys.argv[1], sys.argv[2]
    ok = inject(exe_path, ico_path)
    print("资源统计（RT_ICON / RT_GROUP_ICON）：", verify(exe_path))
    sys.exit(0 if ok else 1)
