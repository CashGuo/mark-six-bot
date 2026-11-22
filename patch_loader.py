# -*- coding: utf-8 -*-
"""解析层外置补丁加载器 —— 规则分「用户层 / 内置层」，改规则不用重编 EXE。

为什么分两层（2026-11-20 客户拍板）：
  一条消息先用「用户规则」解析，**认不出来**再交给「内置规则」，两层都认不出才判失败。
  这样客户的叫法永远优先、升级永不覆盖；而程序自带规则的修正，也能随版本落到每一台机器上。

  · 用户层  patch\\bet_rules.py            永不覆盖。文件不存在时释放一份
                                            「完整内置规则 + 说明头」，客户照着改就行
  · 内置层  patch\\builtin_bet_rules.py    随版本更新（旧版留 .bak-日期时间）。
                                            它不是客户的，客户改过也不留 —— 客户要改请写用户层

回退只认「认不出」：
  解析结果带 fatal 标记的是「明确拒绝」——没写金额 / 12、13、14 判死 / 中特特肖 /
  写法有歧义。这类**绝不回退**，否则内置层会"好心"把客户故意不记的单记上账。

安全网（缺一条都会出事故）：
  · 某一层文件语法坏掉 → 只废掉这一层，另一层照常跑，错误写进界面状态栏；
  · 两层都没加载上（极端情况）→ 打印告警，服务不会因为规则文件而崩；
  · 生效路径必须能在界面上看见 —— 改了 A 处、实际跑的是 B 处，是最常见的一类无效修改。

注意：本模块不能 import bet_rules（会自我循环），只依赖 msb_paths。
"""

import hashlib
import importlib.util
import json
import shutil
import sys
import time
import traceback
import types
from pathlib import Path

import msb_paths

# 每个模块两层：user = 客户自己的，builtin = 程序自带
LAYERS = {
    "bet_rules": {"user": "bet_rules.py", "builtin": "builtin_bet_rules.py"},
    "manual_plays": {"user": "manual_plays.py", "builtin": "builtin_manual_plays.py"},
}

# 顺序有讲究：manual_plays 内部 import bet_rules，
# 必须先让 bet_rules 的门面落到 sys.modules 上，否则它拿到的是另一份。
ORDER = ("bet_rules", "manual_plays")

# 释放记录：记下「上次给用户层的那份模板」的正文指纹。
# 升级 EXE 时靠它判断用户目录里那份到底有没有被客户改过。
META_NAME = ".seed.json"

STATUS = {
    "seed_dir": "",
    "released": [],       # 本次新释放出去的文件
    "updated": [],        # 跟着新版覆盖掉的文件（覆盖前留了 .bak）
    "kept": [],           # 客户改过、没跟着覆盖的用户层文件
    "modules": {},        # name -> {"source": ..., "path": ..., "error": ...}（界面兼容用）
    "layers": {},         # name -> {"user": {...}, "builtin": {...}}
    "installed": False,
}

_README = """这个目录放的是「解析规则」，改完存盘、在配置页「停止 → 启动」一次即生效，不用重新装。

分成两级，各管各的：

  用户规则（你自己的，升级不会覆盖）
    bet_rules.py          玩法识别与号码集合（加一条叫法、加一种组合，改这里）
    manual_plays.py       手工记账的按钮表

  内置规则（程序自带的，升级会替换，别改）
    builtin_bet_rules.py
    builtin_manual_plays.py

解析顺序：先用你的 bet_rules.py → 它认不出来，再用 builtin_bet_rules.py
       → 两层都认不出来，才回「规则解析不了」。

所以：想加叫法，直接改 bet_rules.py 里的 PLAYS，永远不用担心升级被冲掉。
     （这个文件一开始就是内置规则的完整副本，照着现有条目加一行 Play(...) 即可；
       priority 越大越先匹配。）

改坏了怎么办：删掉 bet_rules.py，重启服务会自动从内置规则重新生成一份（旧的会先备份）。
    内置层坏了：把 .bak-日期时间 那个文件改回原名即可。

改了不生效？多半是这两种：
  1. 保存成了 .py.txt —— 记事本另存为时把「文件类型」选成「所有文件」；
  2. 改完没重启服务 —— 配置页「停止 → 启动」一次就好。
"""


def _seed_source(name: str):
    """内置规则的源头：源码模式取项目根，打包后取解包目录里的 patch_seed\\。"""
    base = Path(__file__).resolve().parent
    for cand in (base / "patch_seed" / (name + ".py"), base / (name + ".py")):
        try:
            if cand.is_file():
                return cand
        except Exception:
            continue
    return None


def _pick_file(filename: str):
    """按优先级找一个外部文件；都没有返回 None。"""
    for d in (msb_paths.HOME_DIR / "patch", msb_paths.APP_DIR / "patch"):
        p = d / filename
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


def _body_key(text: str) -> str:
    """正文指纹：跳过开头连续的注释/空行（说明头、编码声明），从第一行实际代码算起。

    这样「带说明头的模板」和「项目根那份裸规则」会被认成同一份，不会每次启动都
    误判成"内容变了"而反复覆盖、堆一堆 .bak。
    """
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        s = lines[i].strip()
        if s == "" or s.startswith("#"):
            i += 1
            continue
        break
    return hashlib.sha256("\n".join(lines[i:]).encode("utf-8")).hexdigest()


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


def _user_head(name: str) -> str:
    return "\n".join([
        "# -*- coding: utf-8 -*-",
        "# ─── 用户规则（升级不会覆盖）───────────────────────────",
        "# 这个文件是你的「用户规则」：升级新版本时不会被覆盖，永远优先生效。",
        "# 下面这份内容就是程序内置规则的完整副本，是给你照着改的模板。",
        "#",
        "#   · 加一条叫法 / 改号码集合 → 直接改下面的 PLAYS（或对应段落）",
        "#   · 改坏了 → 删掉这个文件，重启服务会自动重新生成一份",
        "#   · 内置规则在变 → 同目录的 builtin_*.py 跟着版本更新，别改那个",
        "#",
        "# 解析顺序：先用本文件解析 → 认不出来再交给内置规则 → 都认不出才回「规则解析不了」。",
        "# 明确拒绝的（没写金额、12/13/14 判死、中特特肖）不会回退到内置规则，免得误记账。",
        "# ────────────────────────────────────────────────────────────",
        "",
    ])


def _write_user_template(path: Path, name: str, builtin_text: str) -> None:
    """用户层模板 = 说明头 + 内置规则的完整内容（不是空壳，客户打开就能照着改）。"""
    body = builtin_text
    enc = "# -*- coding: utf-8 -*-\n"
    if body.startswith(enc):
        body = body[len(enc):]
    path.write_text(_user_head(name) + body, encoding="utf-8")


def release_seed() -> None:
    """把「内置层」同步到用户目录，并在缺用户层时给一份完整模板。

    内置层：正文变了就覆盖（旧版留 .bak）。
    用户层：不存在 → 生成模板；存在 → **只有当它还是我们上次给的那份模板时**才跟着更新，
            客户动过的永远不动（记进 STATUS["kept"]，界面提示）。
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
        files = LAYERS[name]
        src = _seed_source(name)
        pkg_text = ""
        pkg_hash = ""
        if src is not None:
            try:
                pkg_text = Path(src).read_text(encoding="utf-8-sig")
                pkg_hash = _sha(src)
            except Exception as e:
                STATUS.setdefault("seed_error", "%s: %s" % (type(e).__name__, e))

        # ---------- 内置层：跟着版本走 ----------
        bdst = dst_dir / files["builtin"]
        if pkg_text:
            if not bdst.exists():
                try:
                    bdst.write_text(pkg_text, encoding="utf-8")
                    STATUS["released"].append(bdst.name)
                except Exception as e:
                    STATUS.setdefault("seed_error", "%s: %s" % (type(e).__name__, e))
            else:
                try:
                    cur = bdst.read_text(encoding="utf-8-sig")
                    if _body_key(cur) != _body_key(pkg_text):
                        bak = _backup(bdst)
                        bdst.write_text(pkg_text, encoding="utf-8")
                        STATUS["updated"].append(bdst.name)
                        if bak:
                            meta.setdefault("backup", {})[bdst.name] = bak
                except Exception:
                    pass

        # 内置层当前内容（用来生成用户层模板）
        builtin_body = pkg_text
        if bdst.exists():
            try:
                builtin_body = bdst.read_text(encoding="utf-8-sig")
            except Exception:
                pass

        # ---------- 用户层：永不覆盖 ----------
        udst = dst_dir / files["user"]
        rec = meta.get(name) or {}
        if not udst.exists():
            if builtin_body:
                try:
                    _write_user_template(udst, name, builtin_body)
                    STATUS["released"].append(udst.name)
                except Exception as e:
                    STATUS.setdefault("seed_error", "%s: %s" % (type(e).__name__, e))
                    continue
            rec["template_body"] = _body_key(udst.read_text(encoding="utf-8-sig"))
            rec["seed_sha"] = pkg_hash
            rec["file_sha"] = _sha(udst)
            rec["at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            meta[name] = rec
            continue

        # 已经在 —— 判断这份还是不是「我们给的那份模板」
        try:
            cur_text = udst.read_text(encoding="utf-8-sig")
        except Exception:
            continue
        cur_body = _body_key(cur_text)
        # 老部署没有 template_body 记录：退回到旧机制记的 file_sha
        if rec.get("template_body"):
            unchanged = (cur_body == rec["template_body"])
        elif rec.get("file_sha"):
            unchanged = (_sha(udst) == rec["file_sha"])
        else:
            unchanged = False

        new_body = _body_key(builtin_body) if builtin_body else ""
        if unchanged and new_body and cur_body != new_body:
            # 客户没改过 → 跟着内置层更新（先备份，客户随时能捞回来）
            try:
                bak = _backup(udst)
                _write_user_template(udst, name, builtin_body)
                STATUS["updated"].append(udst.name)
                if bak:
                    meta.setdefault("backup", {})[udst.name] = bak
                cur_body = _body_key(udst.read_text(encoding="utf-8-sig"))
            except Exception:
                pass
        elif not unchanged:
            STATUS["kept"].append(udst.name)

        # 只有"还是模板"时才更新基准；客户改过的必须留着旧基准，
        # 否则下一次启动会把他的改动当成"没改过"，直接覆盖 —— 那就违反了永不覆盖。
        rec["seed_sha"] = pkg_hash
        if unchanged:
            rec["template_body"] = cur_body
            rec["file_sha"] = _sha(udst)
        rec["at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        meta[name] = rec

    _write_meta(dst_dir, meta)

    try:
        (dst_dir / "说明.txt").write_text(_README, encoding="utf-8")
    except Exception:
        pass


def _load_from(alias: str, path: Path):
    """从指定文件加载模块，注册为 alias（不占用 bet_rules 这个名字，两层互不覆盖）。"""
    spec = importlib.util.spec_from_file_location(alias, str(path))
    if spec is None or spec.loader is None:
        raise ImportError("无法从 %s 加载模块 %s" % (path, alias))
    mod = importlib.util.module_from_spec(spec)
    prev = sys.modules.get(alias)
    sys.modules[alias] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        if prev is not None:
            sys.modules[alias] = prev
        else:
            sys.modules.pop(alias, None)
        raise
    return mod


def _two_layer_parse(user_mod, builtin_mod):
    """两层解析：用户先 → 认不出（且不是明确拒绝）→ 内置。

    回退判据用结构化的 fatal 标记，不去猜中文原因文本：
      · ok=True            → 认出来了，直接用
      · fatal=True         → 明确拒绝（没写金额 / 12、13、14 / 中特特肖 / 歧义），不回退
      · ok=False, 无 fatal → 认不出，交给内置层再试一次
    """

    def parse_local(text, zodiac):
        r = None
        if user_mod is not None:
            try:
                r = user_mod.parse_local(text, zodiac)
            except Exception:
                r = None
            if isinstance(r, dict):
                if r.get("ok") or r.get("fatal"):
                    r["source"] = "user"
                    return r
            else:
                r = None

        if builtin_mod is not None:
            try:
                b = builtin_mod.parse_local(text, zodiac)
            except Exception:
                b = None
            if isinstance(b, dict):
                b["source"] = "builtin"
                if not b.get("ok") and r is not None and not b.get("reason"):
                    b["reason"] = r.get("reason") or ""
                return b

        if isinstance(r, dict):
            r.setdefault("source", "user")
            return r
        return {"ok": False, "reason": "没有可用的解析规则", "bets": {},
                "items": [], "detail": "", "market": "MO", "source": "none"}

    return parse_local


def _merge_groups(base, over):
    """按钮表合并：以内置层为骨架，用户层同名分组追加它独有的按钮。"""
    try:
        out = []
        idx = {}
        for gname, items in base or []:
            idx[gname] = len(out)
            out.append([gname, list(items)])
        for gname, items in over or []:
            if gname in idx:
                cur = out[idx[gname]][1]
                keys = {t for t, _v in cur}
                for it in items:
                    if it[0] not in keys:
                        cur.append(it)
            else:
                idx[gname] = len(out)
                out.append([gname, list(items)])
        return [(g[0], g[1]) for g in out]
    except Exception:
        return base or over


def _build_facade(name: str, mods: dict):
    """把两层拼成一个门面模块，占用 sys.modules[name]，上层代码完全不用改。"""
    builtin = mods.get("builtin")
    user = mods.get("user")
    mod = types.ModuleType(name)

    base = builtin if builtin is not None else user
    if base is not None:
        for k, v in base.__dict__.items():
            if not k.startswith("__"):
                mod.__dict__[k] = v
        mod.__file__ = getattr(base, "__file__", "")

    if user is not None and builtin is not None:
        # 用户层覆盖同名属性（常量、辅助函数都以客户那份为准）
        for k, v in user.__dict__.items():
            if k.startswith("__") or k in ("parse_local", "parse_core"):
                continue
            mod.__dict__[k] = v

    if name == "bet_rules":
        mod.__dict__["parse_local"] = _two_layer_parse(user, builtin)
    if name == "manual_plays" and user is not None and builtin is not None:
        mod.__dict__["GROUPS"] = _merge_groups(
            getattr(builtin, "GROUPS", []), getattr(user, "GROUPS", []))

    mod.__dict__["_LAYERS"] = {"user": user is not None, "builtin": builtin is not None}
    return mod


def _install_pair(name: str) -> None:
    files = LAYERS[name]
    mods = {}
    info = {}
    for layer in ("builtin", "user"):
        st = {"source": "", "path": "", "error": ""}
        path = _pick_file(files[layer])
        if path is None and layer == "builtin":
            path = _seed_source(name)
        if path is not None:
            alias = "_msb_%s_%s" % (layer, name)
            try:
                mods[layer] = _load_from(alias, path)
                st["source"] = layer
                st["path"] = str(path)
                print("[patch] %s/%s ← %s"
                      % (name, "内置层" if layer == "builtin" else "用户层", path))
            except Exception as e:
                st["error"] = "%s: %s" % (type(e).__name__, e)
                st["path"] = str(path)
                print("[patch] %s/%s 加载失败，这一层不参与：%s"
                      % (name, "内置层" if layer == "builtin" else "用户层", st["error"]))
                print("[patch] " + traceback.format_exc().replace("\n", " | ")[:600])
        info[layer] = st

    facade = _build_facade(name, mods)
    sys.modules[name] = facade
    STATUS["layers"][name] = info
    STATUS["modules"][name] = _summary(info)

    if not mods:
        print("[patch] %s 两层都没加载上 —— 服务会没有规则可用" % name)


def _summary(info: dict) -> dict:
    u = info.get("user") or {}
    b = info.get("builtin") or {}
    ok_u = bool(u.get("path")) and not u.get("error")
    ok_b = bool(b.get("path")) and not b.get("error")
    if ok_u and ok_b:
        src, path = "external", "用户层 %s ｜ 内置层 %s" % (u["path"], b["path"])
    elif ok_u:
        src, path = "external", "只有用户层 %s（内置层没加载上）" % u["path"]
    elif ok_b:
        src, path = "builtin", "只有内置层 %s（用户层没找到）" % b["path"]
    else:
        src, path = "builtin", ""
    errs = [x for x in (u.get("error"), b.get("error")) if x]
    return {"source": src, "path": path, "error": "；".join(errs)}


def install(force: bool = False) -> dict:
    """在第一次 import bet_rules 之前调用。重复调用只生效一次（除非 force）。"""
    if STATUS["installed"] and not force:
        return STATUS
    STATUS["modules"].clear()
    STATUS["layers"].clear()
    STATUS["released"].clear()
    STATUS["updated"].clear()
    STATUS["kept"].clear()

    release_seed()
    _install_pair("bet_rules")
    _install_pair("manual_plays")

    STATUS["installed"] = True
    return STATUS


def describe(name: str) -> str:
    """给界面用的一行说明。"""
    info = (STATUS.get("layers") or {}).get(name) or {}
    u = info.get("user") or {}
    b = info.get("builtin") or {}
    ok_u = bool(u.get("path")) and not u.get("error")
    ok_b = bool(b.get("path")) and not b.get("error")
    if ok_u and ok_b:
        return "用户规则 + 内置规则（用户优先）"
    if ok_u:
        return "只有用户规则"
    if ok_b:
        return "只有内置规则（用户规则没找到）"
    return "没有可用的规则"


def validate(name: str) -> dict:
    """检查规则文件能不能加载 —— 改完先跑这个，别急着重启服务。

    校验「用户层」那份（客户改的是它）；用户层缺失时退到内置层。
    返回 {"ok": bool, "stage": "ok|none|read|syntax|load|shape",
          "path": str, "line": int, "message": str, "detail": str}
    """
    files = LAYERS.get(name) or {}
    path = _pick_file(files.get("user", name + ".py"))
    if path is None:
        path = _pick_file(files.get("builtin", "builtin_" + name + ".py"))
    if path is None:
        return {"ok": False, "stage": "none", "path": "", "line": 0,
                "message": "没找到规则文件，当前用的是 EXE 内置的那份", "detail": ""}

    try:
        src = path.read_text(encoding="utf-8-sig")
    except Exception as e:
        return {"ok": False, "stage": "read", "path": str(path), "line": 0,
                "message": "读文件失败：%s" % e, "detail": ""}

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

    try:
        alias = "_validate_" + name
        spec = importlib.util.spec_from_file_location(alias, str(path))
        if spec is None or spec.loader is None:
            raise ImportError("无法为此文件创建加载器")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except Exception as e:
        return {"ok": False, "stage": "load", "path": str(path), "line": 0,
                "message": "试加载失败：%s: %s" % (type(e).__name__, e), "detail": ""}

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
    """恢复出厂规则：把「用户层」文件备份后删掉，下次启动按内置规则重新生成。

    界面上的「恢复出厂规则」按钮走这里。只动用户层，内置层不碰。
    """
    d = msb_paths.HOME_DIR / "patch"
    done = []
    for name in ORDER:
        p = d / (LAYERS[name]["user"])
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


def errors() -> list:
    """所有加载错误，界面拿它显示警示。"""
    out = []
    if STATUS.get("seed_error"):
        out.append("释放内置副本失败：%s" % STATUS["seed_error"])
    for name, info in (STATUS.get("layers") or {}).items():
        for layer, st in (info or {}).items():
            if st.get("error"):
                out.append("%s/%s：%s"
                           % (name, "内置层" if layer == "builtin" else "用户层", st["error"]))
    for name, st in (STATUS.get("modules") or {}).items():
        if not (STATUS.get("layers") or {}).get(name) and st.get("error"):
            out.append("%s：%s" % (name, st["error"]))
    return out
