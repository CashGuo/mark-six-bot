# -*- coding: utf-8 -*-
"""解析层外置补丁加载器 —— 改叫法/改解析逻辑不用重编 EXE。

为什么单独抽出来：bet_rules / manual_plays 是改动最频繁的两个文件（加一条叫法、
加一种组合），而 Nuitka 出一次包要 90 分钟。把它们从 EXE 里「让」出来，优先加载
磁盘上的同名 .py 就行，改完重启服务即生效。

查找顺序（命中即用，不再往下找）：
  1. 用户目录 patch\\bet_rules.py     ——  %LOCALAPPDATA%\\MarkSixBot\\patch\\（主位置）
  2. EXE 同目录 patch\\bet_rules.py   ——  便携场景，整个文件夹拷走也能用
  3. 内置那份（打包时以 patch_seed\\ 塞进包里）—— 兜底，保证缺文件也能跑

三条安全网，缺一条都会出事故：
  · 首次启动把内置那份释放到用户目录——客户想改时文件已经在那儿，不用找模板；
  · 外部文件写坏（语法错 / 导入报错）→ 不注入、退回内置，错误记进 STATUS 让界面显示，
    绝不能让一个缩进错误把服务搞得起不来；
  · 生效路径必须能在界面上看见——改了 A 处、实际跑的是 B 处，是最常见的一类无效修改。

注意：本模块不能 import bet_rules（会自我循环），只依赖 msb_paths。
"""

import hashlib
import importlib.util
import json
import shutil
import sys
import time
import traceback
from pathlib import Path

import msb_paths

# 顺序有讲究：manual_plays 内部 import bet_rules，
# 必须先让 bet_rules 落到 sys.modules 上，否则它拿到的是另一份。
ORDER = ("bet_rules", "manual_plays")

# 释放记录：记下「上次从包里释放出去的那份」的指纹。
# 升级 EXE 时靠它判断用户目录里那份到底有没有被客户改过。
META_NAME = ".seed.json"

STATUS = {
    "seed_dir": "",
    "released": [],       # 首次释放出去的文件名
    "updated": [],        # 跟着新版覆盖掉的文件名（覆盖前留了 .bak）
    "kept": [],           # 客户改过、没跟着覆盖的文件名
    "modules": {},        # name -> {"source": "external|builtin|patch_seed", "path": ..., "error": ...}
    "installed": False,
}

_README = """这个目录是「解析规则」的外置版，改完存盘、重启服务即生效，不用重新编译。

  bet_rules.py    玩法识别与号码集合（加一条叫法、加一种组合，改这里）
  manual_plays.py 手工记账的按钮表（控制台和网页共用这一份）

怎么改：用记事本打开 bet_rules.py，找到 PLAYS = sorted([...]) 那一段，
照现有条目的样子加一行 Play(...) 即可，优先级（priority）越大越先匹配。

升级新版本时会怎样：
  · 你没动过这两个文件 —— 新版本会自动把它们换成新规则（旧的存成 .bak-日期）；
  · 你改过 —— 不动你的版本，但会在配置页提示「未随新版更新」。
    想改用新版规则，点配置页的「恢复出厂规则」即可（同样会先备份）。

改了不生效？多半是下面两种：
  1. 保存成了 .py.txt —— 记事本另存为时把「文件类型」选成「所有文件」；
  2. 改完没重启服务 —— 配置页「停止 → 启动」一次就好。

改坏了怎么办：配置页点「恢复出厂规则」，或者手工删掉本目录这两个文件再重启。
"""


def _seed_source(name: str):
    """内置那份的源文件：源码模式取项目根，打包后取解包目录里的 patch_seed\\。"""
    base = Path(__file__).resolve().parent
    for cand in (base / "patch_seed" / (name + ".py"), base / (name + ".py")):
        try:
            if cand.is_file():
                return cand
        except Exception:
            continue
    return None


def _pick(name: str):
    """按优先级挑一个外部文件；都没有返回 None。"""
    for d in (msb_paths.HOME_DIR / "patch", msb_paths.APP_DIR / "patch"):
        p = d / (name + ".py")
        try:
            if p.is_file() and p.stat().st_size > 0:
                return p
        except Exception:
            continue
    return None


def _sha(p) -> str:
    try:
        return hashlib.sha256(Path(p).read_bytes()).hexdigest()
    except Exception:
        return ""


def _read_meta(d: Path) -> dict:
    try:
        doc = json.loads((d / META_NAME).read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else {}
    except Exception:
        return {}


def _write_meta(d: Path, meta: dict) -> None:
    try:
        (d / META_NAME).write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _backup(p: Path) -> str:
    """覆盖前留一份，放同一个目录里，.bak-时间戳 后缀。"""
    dst = p.with_name("%s.bak-%s" % (p.name, time.strftime("%Y%m%d-%H%M%S")))
    try:
        shutil.copyfile(p, dst)
        return dst.name
    except Exception:
        return ""


def release_seed() -> None:
    """把内置规则脚本同步到用户目录，附带一份说明。

    默认行为按「客户不会自己写规则」来定：包里那份变了就覆盖过去，让升级后的
    客户直接拿到新规则。但覆盖前会判断一次——
      · 用户目录那份的指纹 == 上次释放时的指纹（没人动过）→ 覆盖，留 .bak；
      · 指纹对不上（客户自己改过）→ 不覆盖，只记进 STATUS["kept"] 让界面提示。
    没有指纹记录的老部署按「没改过」处理（有 .bak 兜底）。
    """
    dst_dir = msb_paths.HOME_DIR / "patch"
    STATUS["seed_dir"] = str(dst_dir)
    try:
        dst_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        STATUS.setdefault("seed_error", "%s: %s" % (type(e).__name__, e))
        return

    meta = _read_meta(dst_dir)

    for name in ORDER:
        dst = dst_dir / (name + ".py")
        src = _seed_source(name)
        if not src:
            continue
        pkg_hash = _sha(src)
        if not pkg_hash:
            continue

        rec = meta.get(name) or {}

        # 首次：直接放出去
        if not dst.exists():
            try:
                shutil.copyfile(src, dst)
                STATUS["released"].append(dst.name)
                meta[name] = {"seed_sha": pkg_hash, "file_sha": pkg_hash,
                              "at": time.strftime("%Y-%m-%d %H:%M:%S")}
            except Exception as e:
                STATUS.setdefault("seed_error", "%s: %s" % (type(e).__name__, e))
            continue

        # 包内那份没变 —— 一律不动磁盘上的文件。
        # 这条是安全线：客户自己往 PLAYS 里加了一条叫法，重启服务不该被冲掉。
        if rec.get("seed_sha") == pkg_hash:
            if not rec.get("file_sha"):
                rec["file_sha"] = _sha(dst)      # 老记录补齐，不动内容
                meta[name] = rec
            continue

        cur_hash = _sha(dst)
        # 磁盘这份和「上次释放出去的内容」一致 → 客户没改过，跟着新版覆盖
        # （没有记录的老部署按「没改过」处理，反正覆盖前留 .bak）
        if (not rec) or (rec.get("file_sha") and cur_hash == rec["file_sha"]):
            try:
                bak = _backup(dst)
                shutil.copyfile(src, dst)
                STATUS["updated"].append(dst.name)
                if bak:
                    meta.setdefault("backup", {})[dst.name] = bak
                meta[name] = {"seed_sha": pkg_hash, "file_sha": pkg_hash,
                              "at": time.strftime("%Y-%m-%d %H:%M:%S")}
            except Exception as e:
                STATUS.setdefault("seed_error", "%s: %s" % (type(e).__name__, e))
            continue

        # 客户改过，而包里又有新版本 → 保留客户的，让界面提示去点「恢复出厂规则」
        STATUS["kept"].append(dst.name)

    _write_meta(dst_dir, meta)

    readme = dst_dir / "说明.txt"
    if not readme.exists():
        try:
            readme.write_text(_README, encoding="utf-8")
        except Exception:
            pass


def _load_from(name: str, path: Path):
    """从指定文件加载模块，先注册到 sys.modules（供 manual_plays import bet_rules）。"""
    spec = importlib.util.spec_from_file_location(name, str(path))
    if spec is None or spec.loader is None:
        raise ImportError("无法从 %s 加载模块 %s" % (path, name))
    mod = importlib.util.module_from_spec(spec)
    prev = sys.modules.get(name)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        # 回滚：让后面的正常 import 拿到内置那份，而不是一个半成品
        if prev is not None:
            sys.modules[name] = prev
        else:
            sys.modules.pop(name, None)
        raise
    return mod


def validate(name: str) -> dict:
    """检查外置规则文件能不能加载 —— 改完先跑这个，别急着重启。

    分三段查，越往后越贵：
      1. 读文件 + compile()：抓语法错，能给出准确行号；
      2. exec 到隔离的命名空间：抓语法查不出来的问题（正则编译、依赖缺失、
         顶层代码报错），全程不碰 sys.modules，正在跑的规则不受影响；
      3. 结构检查：PLAYS 在不在、有没有重复的 key、pattern 能不能编。

    返回 {"ok": bool, "stage": "ok|none|read|syntax|load|shape",
          "path": str, "line": int, "message": str, "detail": str}
    """
    path = _pick(name)
    if path is None:
        return {"ok": False, "stage": "none", "path": "", "line": 0,
                "message": "没找到外部文件，当前用的是 EXE 内置的那份", "detail": ""}

    try:
        src = path.read_text(encoding="utf-8-sig")
    except Exception as e:
        return {"ok": False, "stage": "read", "path": str(path), "line": 0,
                "message": "读文件失败：%s" % e, "detail": ""}

    # 1) 语法
    try:
        compile(src, str(path), "exec")
    except SyntaxError as e:
        bad_line = (e.text or "").strip()
        msg = "第 %d 行语法错误：%s" % (e.lineno or 0, e.msg)
        if bad_line:
            msg += "\n    " + bad_line
        return {"ok": False, "stage": "syntax", "path": str(path),
                "line": e.lineno or 0, "message": msg, "detail": ""}
    except Exception as e:
        return {"ok": False, "stage": "syntax", "path": str(path), "line": 0,
                "message": "编译失败：%s: %s" % (type(e).__name__, e), "detail": ""}

    # 2) 试加载（独立命名空间；manual_plays import bet_rules 时拿的是当前生效那份，
    #    这也是它该有的行为 —— 校验的是"新文件接上现有环境"能不能跑）
    try:
        spec = importlib.util.spec_from_file_location("_validate_" + name, str(path))
        if spec is None or spec.loader is None:
            raise ImportError("无法为此文件创建加载器")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except Exception as e:
        return {"ok": False, "stage": "load", "path": str(path), "line": 0,
                "message": "试加载失败：%s: %s" % (type(e).__name__, e), "detail": ""}

    # 3) 结构（只对 bet_rules 有意义）
    detail = ""
    if name == "bet_rules":
        plays = getattr(mod, "PLAYS", None)
        if not isinstance(plays, list) or not plays:
            return {"ok": False, "stage": "shape", "path": str(path), "line": 0,
                    "message": "PLAYS 不存在或为空 —— 可能是保存时把整段删掉了", "detail": ""}
        keys = [getattr(p, "key", "") for p in plays]
        dup = sorted({k for k in keys if keys.count(k) > 1})
        if dup:
            detail = "重复的玩法 key：%s（不影响启动，但两条规则会互相抢匹配）" % "、".join(dup)
        detail = ("%d 条玩法" % len(plays)) + (("；" + detail) if detail else "")

    return {"ok": True, "stage": "ok", "path": str(path), "line": 0,
            "message": "检查通过", "detail": detail}


def restore_factory() -> list:
    """恢复出厂规则：把用户目录里的外置脚本备份后删掉，下次启动用包里那份。

    界面上的「恢复出厂规则」按钮走这里。删之前留 .bak，客户反悔还能捞回来。
    """
    d = msb_paths.HOME_DIR / "patch"
    done = []
    for name in ORDER:
        p = d / (name + ".py")
        try:
            if p.exists():
                _backup(p)
                p.unlink()
                done.append(p.name)
        except Exception:
            continue
    try:
        (d / META_NAME).unlink()
    except Exception:
        pass
    return done


def install(force: bool = False) -> dict:
    """在第一次 import bet_rules 之前调用。重复调用只生效一次（除非 force）。"""
    if STATUS["installed"] and not force:
        return STATUS
    STATUS["modules"].clear()
    release_seed()

    for name in ORDER:
        st = {"source": "builtin", "path": "", "error": ""}
        path = _pick(name)
        if path is not None:
            try:
                _load_from(name, path)
                st["source"] = "external"
                st["path"] = str(path)
                STATUS["modules"][name] = st
                print("[patch] %s ← 外部文件 %s" % (name, path))
                continue
            except Exception as e:
                st["error"] = "%s: %s" % (type(e).__name__, e)
                st["path"] = str(path)
                print("[patch] %s 外部加载失败，回退内置：%s" % (name, st["error"]))
                print("[patch] " + traceback.format_exc().replace("\n", " | ")[:800])

        # 退回内置：打包态从 patch_seed 加载（--nofollow-import-to 排除了它，
        # 不能再指望 import 拿到）；源码模式直接留给正常 import。
        seed = _seed_source(name)
        if msb_paths.is_compiled() and seed is not None:
            try:
                _load_from(name, seed)
                st["source"] = "patch_seed"
                st["path"] = str(seed)
            except Exception as e:
                st["error"] = (st["error"] + " / " if st["error"] else "") + \
                              "内置加载失败 %s: %s" % (type(e).__name__, e)

        STATUS["modules"][name] = st

    STATUS["installed"] = True
    return STATUS


def describe(name: str) -> str:
    """给界面用的一行说明。"""
    st = (STATUS.get("modules") or {}).get(name) or {}
    src = st.get("source", "builtin")
    if src == "external":
        return "外部：%s" % st.get("path", "")
    if src == "patch_seed":
        return "内置（包内副本）：%s" % st.get("path", "")
    return "内置（EXE 里编译的那份）"


def errors() -> list:
    """所有加载错误，界面拿它显示警示。"""
    out = []
    if STATUS.get("seed_error"):
        out.append("释放内置副本失败：%s" % STATUS["seed_error"])
    for name, st in (STATUS.get("modules") or {}).items():
        if st.get("error"):
            out.append("%s：%s" % (name, st["error"]))
    return out
