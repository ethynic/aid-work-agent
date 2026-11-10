#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
统一社会信用代码（USCC / 三证合一 18 位）本地校验工具。

本脚本只做「纯本地、无需联网」的算法校验，不含有任何搜索 / 联网逻辑。
用于在供应商资质审查技能中核验营业执照的统一社会信用代码是否形如合法编码、
校验位是否正确、登记管理部门与机构类别代码是否在合法范围内。

用法：
    python verify_uscc.py <USCC>                 # 校验单个代码，打印详细结论
    python verify_uscc.py --json <USCC>          # 以 JSON 返回结果，便于技能流程消费
    python verify_uscc.py --self-test            # 运行内置自检

退出码：0 = 通过；1 = 不通过；2 = 用法错误。
"""

import sys
import json

# GB 32100-2015《法人和其他组织统一社会信用代码编码规则》
# 第 1 位：登记管理部门代码；第 2 位：机构类别代码；第 3-8 位：登记机关行政区划码；
# 第 9-17 位：主体标识码（组织机构代码）；第 18 位：校验码。
USCC_CHARSET = "0123456789ABCDEFGHJKLMNPQRTUWXY"  # 31 个字符，不含 I O S V Z
USCC_WEIGHTS = [1, 3, 9, 27, 19, 26, 16, 17, 20, 29, 25, 13, 8, 24, 10, 30, 28]

# 第 1 位：登记管理部门代码（合法取值）
REG_DEPT = {
    "0": "市场监管（工商）",
    "1": "机构编制",
    "2": "民政",
    "3": "司法行政",
    "4": "农业",
    "5": "宗教",
    "6": "教育",
    "7": "文化旅游",
    "8": "卫生",
    "9": "市场监管（工商）",
    "A": "证监会",
    "B": "保监会",
    "C": "民航",
    "D": "外汇",
    "E": "铁路",
    "F": "海关",
    "G": "税务",
    "H": "通信",
    "I": "邮政",
    "J": "公安",
    "K": "人力资源和社会保障",
    "L": "自然资源",
    "M": "生态环境",
    "N": "住建",
    "O": "交通运输",
    "P": "水利",
    "Q": "卫健",
    "R": "文体",
    "S": "市场监督（特种）",
    "T": "退役军人事务",
    "U": "网信",
    "V": "档案",
    "W": "保密",
    "X": "密码管理",
    "Y": "边防",
    "Z": "other",
}

# 第 2 位：机构类别代码（取常见范围，非穷举；用于提示而非硬性拒绝）
ORG_CATEGORY = {
    "1": "企业",
    "2": "个体工商户",
    "3": "农民专业合作社",
    "4": "其他组织机构",
    "5": "其他组织机构（编制）",
    "9": "其他",
}


def char_value(ch: str) -> int:
    idx = USCC_CHARSET.find(ch)
    if idx < 0:
        raise ValueError(f"非法字符: {ch!r}")
    return idx


def compute_check_digit(body17: str) -> str:
    """给定前 17 位，按 ISO 7064 MOD 11-3 计算第 18 位校验码字符。"""
    if len(body17) != 17:
        raise ValueError("前 17 位长度必须为 17")
    total = 0
    for i, ch in enumerate(body17):
        total += char_value(ch) * USCC_WEIGHTS[i]
    mod = total % 31
    check = 31 - mod
    if check == 31:
        check = 0
    return USCC_CHARSET[check]


def verify_uscc(code: str) -> dict:
    """校验一个统一社会信用代码，返回结构化结果。"""
    code = (code or "").strip().upper()
    result = {
        "input": code,
        "valid": False,
        "length_ok": False,
        "charset_ok": False,
        "check_digit_ok": False,
        "reg_dept": None,
        "reg_dept_desc": None,
        "org_category": None,
        "org_category_desc": None,
        "expected_check_digit": None,
        "issues": [],
    }

    # 1) 长度
    if len(code) != 18:
        result["issues"].append(f"长度应为 18 位，实际 {len(code)} 位")
        return result
    result["length_ok"] = True

    # 2) 字符集（全部须在合法字符表内）
    bad_chars = [c for c in code if USCC_CHARSET.find(c) < 0]
    if bad_chars:
        result["issues"].append(f"含非法字符（不在 GB32100 字符表内）: {''.join(sorted(set(bad_chars)))}")
        return result
    result["charset_ok"] = True

    # 3) 登记管理部门 & 机构类别
    d1, d2 = code[0], code[1]
    result["reg_dept"] = d1
    result["reg_dept_desc"] = REG_DEPT.get(d1, "未知/未收录")
    result["org_category"] = d2
    result["org_category_desc"] = ORG_CATEGORY.get(d2, "未知/未收录")
    if d1 not in REG_DEPT:
        result["issues"].append(f"第 1 位登记管理部门代码 {d1!r} 不在已知合法范围")
    if d2 not in ORG_CATEGORY:
        # 机构类别代码范围较宽，这里仅作提示
        result["issues"].append(f"第 2 位机构类别代码 {d2!r} 不在常见范围，建议人工复核")

    # 4) 校验位
    body, given = code[:17], code[17]
    expected = compute_check_digit(body)
    result["expected_check_digit"] = expected
    if given == expected:
        result["check_digit_ok"] = True
    else:
        result["issues"].append(f"校验位错误：末位为 {given!r}，按算法应为 {expected!r}")

    result["valid"] = (
        result["length_ok"]
        and result["charset_ok"]
        and result["check_digit_ok"]
        and d1 in REG_DEPT
    )
    return result


def main(argv):
    if not argv:
        print(__doc__)
        return 2

    if argv[0] == "--self-test":
        samples = [
            # (code, 期望 valid)
            ("91110000100000000X", False),  # 末位大概率错，用于确认自检能识别
        ]
        # 构造一个确定合法的样例：以 17 位体 + 计算校验位
        body = "91110108MA01ABC23"
        ok_body = body[:17] if len(body) >= 17 else (body + "0" * (17 - len(body)))
        ok_code = ok_body + compute_check_digit(ok_body)
        samples.append((ok_code, True))
        all_pass = True
        for code, exp in samples:
            r = verify_uscc(code)
            status = "PASS" if r["valid"] == exp else "FAIL"
            if status == "FAIL":
                all_pass = False
            print(f"[{status}] {code} -> valid={r['valid']} (expected {exp})")
        print("SELF-TEST", "OK" if all_pass else "FAILED")
        return 0 if all_pass else 1

    as_json = False
    target = None
    for a in argv:
        if a == "--json":
            as_json = True
        else:
            target = a
    if not target:
        print("错误：未提供待校验的统一社会信用代码", file=sys.stderr)
        return 2

    r = verify_uscc(target)
    if as_json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        print(f"输入：{r['input']}")
        print(f"结论：{'✅ 合法' if r['valid'] else '❌ 不合法'}")
        print(f"  长度正确 : {r['length_ok']}")
        print(f"  字符集正确: {r['charset_ok']}")
        print(f"  校验位正确: {r['check_digit_ok']}（期望末位 {r['expected_check_digit']}）")
        print(f"  登记管理部门: {r['reg_dept']} = {r['reg_dept_desc']}")
        print(f"  机构类别    : {r['org_category']} = {r['org_category_desc']}")
        if r["issues"]:
            print("  问题：")
            for i in r["issues"]:
                print(f"    - {i}")
    return 0 if r["valid"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
