#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离线验证记账核心：不依赖 DeepSeek，用打桩的解析结果验证号码表、累加、落盘。

用法：python tests/verify_record.py
"""

import json
import shutil
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import main  # noqa: E402

# 多账号改造后账本按账号分目录。测试统一用独立账号，绝不碰真实账本。
ACC = "__unit__"
FILES = main.account_files(ACC)

_results = []


def check(name, ok, detail=""):
    _results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")


def main_test():
    # 清掉上一次的测试账号目录，跑完再删
    if (main.STATE_DIR / ACC).exists():
        shutil.rmtree(main.STATE_DIR / ACC, ignore_errors=True)

    try:
        # ---------- 1. 号码表 ----------
        _, _, zodiac_mapping = main.get_current_lunar_year()
        check("农历生肖表有 12 项", len(zodiac_mapping) == 12, f"{sorted(zodiac_mapping)}")

        # 马年：马 = 01,13,25,37,49
        check("马对应 5 个号", zodiac_mapping.get("马") == ["01", "13", "25", "37", "49"],
              str(zodiac_mapping.get("马")))

        bets = main.expand_bets([{"type": "color", "names": ["红波"], "amount": 10}], zodiac_mapping)
        check("红波 17 个号", len(bets) == 17, f"{len(bets)} 个")
        # 波色暂时维持老算法（每个号各 10），生肖才按总额摊开
        check("红波每个 10 元", all(v == 10 for v in bets.values()), "")

        bets = main.expand_bets(
            [{"type": "number", "numbers": ["01", "13", "1", "49"], "amount": 5}],
            zodiac_mapping)
        check("直接号码含补零（1→01）", set(bets) == {"01", "13", "49"}, str(sorted(bets)))
        check("同号重复累加（01/1 都算 01）", bets.get("01") == 10, str(bets.get("01")))

        bets = main.expand_bets([{"type": "head", "heads": ["0"], "amount": 3}], zodiac_mapping)
        check("0头 9 个号", len(bets) == 9 and "01" in bets and "09" in bets, str(len(bets)))

        # 模型实测会对 "03头" 输出 "03"，必须归一成 3 字头，否则金额静默丢失
        bets = main.expand_bets([{"type": "head", "heads": ["03"], "amount": 3}], zodiac_mapping)
        check("03头按 3 字头展开", len(bets) == 10 and "30" in bets and "39" in bets, str(sorted(bets)[:3]))
        check("03头不会落到 0 字头", "01" not in bets, str("01" in bets))
        bets = main.expand_bets([{"type": "head", "heads": [3], "amount": 3}], zodiac_mapping)
        check("int 型 head 也能归一", len(bets) == 10, str(len(bets)))

        # 模型偶尔不按 {"items":[...]} 输出
        check("_extract_items 接受裸数组",
              len(main._extract_items('[{"type":"number"}]')) == 1, "")
        check("_extract_items 接受 items 包裹",
              len(main._extract_items('{"items":[{"type":"number"},{"type":"x"}]}')) == 2, "")
        check("_extract_items 对空结构返回空",
              main._extract_items('{"items":[]}') == [], "")

        # ---------- 2. 记账累加 ----------
        main.save_data(main.init_data(), ACC)

        calls = []
        fake = {"01": 15.0, "13": 15.0, "25": 15.0}
        main.parse_bet_text = lambda text, zm_: (calls.append(text) or dict(fake))

        r1 = main.record_bet("马各号15米", "unit-1", "测试群", "tester", account=ACC)
        check("第一单记账成功", r1.get("ok") is True, str(r1))
        check("第一单 3 个号 45 元", r1.get("numbers") == 3 and r1.get("total") == 45.0, str(r1))

        r2 = main.record_bet("马各号15米", "unit-2", "测试群", "tester", account=ACC)
        check("第二单累计 90 元", r2.get("grand_total") == 90.0, str(r2))

        data = main.load_data(ACC)
        check("01 号累计 30", data.get("01") == 30.0, str(data.get("01")))
        check("其余号码仍为 0", data.get("02") == 0.0, str(data.get("02")))

        top = main.top_numbers(data, 10)
        check("Top 只有 3 个号", len(top) == 3, str(top))

        # ---------- 3. 解析失败落 unparsed ----------
        main.parse_bet_text = lambda text, zm_: {}
        n_before = main._count_today(FILES["unparsed"])
        r3 = main.record_bet("乱码乱码", "unit-3", "测试群", "tester", account=ACC)
        check("解析失败不记账", r3.get("ok") is False, str(r3))
        check("计入 unparsed", main._count_today(FILES["unparsed"]) == n_before + 1, "")

        # ---------- 4. 去重 ----------
        mid = "dedup-test-001"
        main.is_message_processed(mid)  # 确保不存在
        if mid in main._processed_set:
            main._processed_set.discard(mid)
        main.mark_message_processed(mid)
        check("去重命中", main.is_message_processed(mid) is True, "")
        check("去重已持久化", main.PROCESSED_FILE.exists() and mid in main.PROCESSED_FILE.read_text(encoding="utf-8"), "")

        # ---------- 5. 备份/恢复 ----------
        main.save_data(main.init_data(), ACC)
        bf = main.backup_data(manual=True, account=ACC)
        check("备份文件生成", bool(bf) and Path(bf).exists(), str(bf))

        main.save_data({f"{i:02d}": 1.0 for i in range(1, 50)}, ACC)
        today = __import__("datetime").datetime.now().strftime("%Y-%m-%d")
        ok = main.restore_data(today, ACC)
        check("恢复成功", ok is True, "")
        data = main.load_data(ACC)
        check("恢复后回到全 0", sum(data.values()) == 0.0, str(sum(data.values())))

    finally:
        if (main.STATE_DIR / ACC).exists():
            shutil.rmtree(main.STATE_DIR / ACC, ignore_errors=True)
        if FILES["backup"].exists():
            shutil.rmtree(FILES["backup"], ignore_errors=True)

    print("\n=== 汇总 ===")
    ok_all = all(r[1] for r in _results)
    for name, ok, _ in _results:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    print(f"总计 {sum(1 for r in _results if r[1])}/{len(_results)} 通过")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main_test())
