from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

DEFAULT_ALLOWED = ["视频", "推", "无画面"]
EVIDENCE_HEADER = ["bvid", "time", "character", "conclusion", "frame", "note"]
MD_HEADER = ["角色", "主幻兵", "武器", "防具", "饰品", "状态", "证据/备注"]
MD_STATUSES = ["视频", "推", "视频/推", "视频（待并入）", "无画面", "推（另一版视频）"]
MD_VIDEO_STATUSES = ["视频", "视频/推", "视频（待并入）"]
BVID_RE = re.compile(r"BV[0-9A-Za-z]{10}")
TIME_RE = re.compile(r"(?<![\d:])(?:\d{1,2}:){1,2}\d{2}(?![\d:])")


def load_spec(path: str | Path) -> dict:
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = [str(item).strip() for item in (doc.get("rows") or []) if str(item).strip()]
    columns = [str(item).strip() for item in (doc.get("columns") or []) if str(item).strip()]
    scope = [str(item).strip() for item in (doc.get("scope") or []) if str(item).strip()]
    allowed = [str(item).strip() for item in (doc.get("allowed") or DEFAULT_ALLOWED) if str(item).strip()]
    if not rows:
        raise ValueError("spec.rows 不能为空")
    if not columns:
        raise ValueError("spec.columns 不能为空")
    if not allowed:
        raise ValueError("spec.allowed 不能为空")
    return {
        "name": str(doc.get("name") or Path(path).stem),
        "rows": rows,
        "columns": columns,
        "scope": scope,
        "allowed": allowed,
    }


def init_files(spec: dict, matrix_path: Path, evidence_path: Path | None = None) -> None:
    matrix_path.parent.mkdir(parents=True, exist_ok=True)
    with matrix_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["玩法"] + spec["columns"])
        for row in spec["rows"]:
            writer.writerow([row] + [""] * len(spec["columns"]))
    if evidence_path:
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        with evidence_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(EVIDENCE_HEADER)


def _read_matrix(path: Path) -> tuple[list[str], list[dict]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def _read_evidence(path: Path) -> tuple[list[str], list[dict]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def check(spec: dict, matrix_path: Path, evidence_path: Path | None) -> tuple[bool, str]:
    issues: list[str] = []
    notes: list[str] = []

    matrix_header, matrix_rows = _read_matrix(matrix_path)
    expected_header = ["玩法"] + spec["columns"]
    if matrix_header != expected_header:
        missing_cols = [col for col in expected_header if col not in matrix_header]
        extra_cols = [col for col in matrix_header if col not in expected_header]
        if missing_cols:
            issues.append("矩阵缺列：" + "、".join(missing_cols))
        if extra_cols:
            issues.append("矩阵多列：" + "、".join(extra_cols))
        if not missing_cols and not extra_cols and matrix_header:
            issues.append("矩阵表头顺序与 spec 不一致")

    by_row = {str(item.get("玩法") or "").strip(): item for item in matrix_rows}
    expected_rows = set(spec["rows"])
    actual_rows = set(by_row)
    for row in spec["rows"]:
        if row not in by_row:
            issues.append(f"矩阵缺行：{row}")
    for row in sorted(actual_rows - expected_rows):
        issues.append(f"矩阵多行：{row}")

    blank_cells = []
    invalid_cells = []
    for row in spec["rows"]:
        item = by_row.get(row)
        if not item:
            continue
        for column in spec["columns"]:
            value = str(item.get(column) or "").strip()
            if not value:
                blank_cells.append(f"{row} | {column}")
            elif value not in spec["allowed"]:
                invalid_cells.append(f"{row} | {column} | {value}")
    if blank_cells:
        issues.append(f"矩阵空白格 {len(blank_cells)} 个")
    if invalid_cells:
        issues.append(f"矩阵非法值 {len(invalid_cells)} 个")

    evidence_bvids: set[str] = set()
    if spec["scope"]:
        if evidence_path is None or not evidence_path.exists():
            issues.append("缺少 evidence.csv，无法验证冻结 BVID 是否都有证据")
        else:
            evidence_header, evidence_rows = _read_evidence(evidence_path)
            for column in ("bvid", "time", "character", "conclusion"):
                if column not in evidence_header:
                    issues.append(f"evidence.csv 缺列：{column}")
            for index, item in enumerate(evidence_rows, 2):
                bvid = str(item.get("bvid") or "").strip()
                timestamp = str(item.get("time") or "").strip()
                character = str(item.get("character") or "").strip()
                conclusion = str(item.get("conclusion") or "").strip()
                frame = str(item.get("frame") or "").strip()
                if bvid:
                    evidence_bvids.add(bvid)
                if not bvid or not timestamp or not character or not conclusion:
                    issues.append(f"evidence.csv 第 {index} 行缺 bvid/time/character/conclusion")
                if conclusion and conclusion not in spec["allowed"]:
                    issues.append(f"evidence.csv 第 {index} 行结论非法：{conclusion}")
                if conclusion == "视频" and not frame:
                    issues.append(f"evidence.csv 第 {index} 行标“视频”但没有 frame 路径")
            missing_bvids = [bvid for bvid in spec["scope"] if bvid not in evidence_bvids]
            if missing_bvids:
                issues.append("冻结范围中这些 BVID 没有证据行：" + "、".join(missing_bvids))
            notes.append(f"scope {len(spec['scope'])} 个，evidence 覆盖 {len(evidence_bvids & set(spec['scope']))} 个")

    lines = [
        f"# 覆盖检查：{spec['name']}",
        "",
        f"- 结果：{'PASS' if not issues else 'FAIL'}",
        f"- 矩阵：{len(spec['rows'])} 行 × {len(spec['columns'])} 列；空白 {len(blank_cells)}；非法 {len(invalid_cells)}",
    ]
    lines += [f"- {note}" for note in notes]
    if issues:
        lines += ["", "## 问题", ""]
        lines += [f"- {issue}" for issue in issues]
    if blank_cells:
        lines += ["", "## 空白格", ""]
        lines += [f"- {cell}" for cell in blank_cells]
    if invalid_cells:
        lines += ["", "## 非法值", ""]
        lines += [f"- {cell}" for cell in invalid_cells]
    lines += [
        "",
        "## 口径",
        "",
        "- 每个矩阵格只能填：" + " / ".join(spec["allowed"]),
        "- “无画面”表示查过该视频但没有对应画面；“推”表示只有推测，不能当视频证据。",
        "- 冻结 BVID 必须在 evidence.csv 中至少出现一次。",
    ]
    return not issues, "\n".join(lines) + "\n"


def _split_list(value: str) -> list[str]:
    return [item.strip() for item in str(value or "").replace("，", ",").split(",") if item.strip()]


def _status_base(value: str) -> tuple[str, str]:
    text = str(value or "").strip()
    if "（" in text and text.endswith("）"):
        base, qualifier = text.split("（", 1)
        return base.strip(), qualifier[:-1].strip()
    return text, ""


def _status_allowed(value: str, allowed: list[str]) -> bool:
    if value in allowed:
        return True
    base, qualifier = _status_base(value)
    return bool(base and qualifier and base in allowed)


def _is_video_status(value: str, video_statuses: list[str]) -> bool:
    if value in video_statuses:
        return True
    base, qualifier = _status_base(value)
    return bool(base and qualifier and base in video_statuses)


def _parse_md(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    numbered: list[str] = []
    appendix: list[str] = []
    rows: list[tuple[str, list[str]]] = []
    headers: dict[str, list[str]] = {}
    section_rows: dict[str, int] = {}
    issues: list[str] = []
    current = ""
    in_table = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("### "):
            current = stripped[4:].strip()
            in_table = False
            section_rows[current] = 0
            if re.match(r"^\d+\.", current):
                numbered.append(current)
            else:
                appendix.append(current)
            continue
        if stripped.startswith("|"):
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            if not in_table:
                in_table = True
                headers[current] = cells
                continue
            if all(re.fullmatch(r":?-{2,}:?", cell) for cell in cells):
                continue
            if current and re.match(r"^\d+\.", current):
                rows.append((current, cells))
                section_rows[current] = section_rows.get(current, 0) + 1
            continue
        if in_table and not stripped:
            in_table = False
    return {
        "text": text,
        "numbered": numbered,
        "appendix": appendix,
        "rows": rows,
        "headers": headers,
        "section_rows": section_rows,
        "issues": issues,
    }


def load_scope(value: str) -> set[str]:
    if not value:
        return set()
    path = Path(value)
    if path.exists():
        text = path.read_text(encoding="utf-8")
        try:
            doc = json.loads(text)
            if isinstance(doc, dict) and isinstance(doc.get("scope"), list):
                return {str(item).strip() for item in doc["scope"] if str(item).strip()}
        except Exception:
            pass
        return set(BVID_RE.findall(text))
    return set(BVID_RE.findall(value))


def check_md(
    matrix_path: Path,
    expected_sections: int = 0,
    require_time: bool = False,
    scope: str = "",
    allowed: list[str] | None = None,
    video_statuses: list[str] | None = None,
) -> tuple[bool, str]:
    allowed = allowed or MD_STATUSES
    video_statuses = video_statuses or MD_VIDEO_STATUSES
    parsed = _parse_md(matrix_path)
    numbered = parsed["numbered"]
    appendix = parsed["appendix"]
    rows = parsed["rows"]
    issues = list(parsed["issues"])

    if expected_sections and len(numbered) != expected_sections:
        issues.append(f"数字编号玩法 {len(numbered)} 个，预期 {expected_sections} 个")
    for section in numbered:
        if section not in parsed["headers"]:
            issues.append(f"玩法没有表格：{section}")
            continue
        if parsed["headers"][section] != MD_HEADER:
            issues.append(f"表头不是 7 列标准格式：{section}")
        if parsed["section_rows"].get(section, 0) == 0:
            issues.append(f"玩法没有数据行：{section}")
    if not rows:
        issues.append("矩阵没有数据行")

    status = {}
    blank_rows = []
    bad_cols = []
    for section, cells in rows:
        if len(cells) != 7:
            bad_cols.append(f"{section} / {cells[0] if cells else '?'} / {len(cells)} 列")
        if any(not cell for cell in cells):
            blank_rows.append(f"{section} / {cells[0] if cells else '?'}")
        value = cells[5].strip() if len(cells) > 5 else ""
        status[value] = status.get(value, 0) + 1
        if not _status_allowed(value, allowed):
            issues.append(f"非法状态：{section} / {cells[0] if cells else '?'} / {value}")
    if bad_cols:
        issues.append(f"列数不是 7 的行 {len(bad_cols)} 个")
    if blank_rows:
        issues.append(f"空白格行 {len(blank_rows)} 个")

    video_rows = [
        (section, cells)
        for section, cells in rows
        if len(cells) > 5 and _is_video_status(cells[5].strip(), video_statuses)
    ]
    missing_bvid = [
        (section, cells)
        for section, cells in video_rows
        if not BVID_RE.search(cells[6] if len(cells) > 6 else "")
    ]
    missing_time = [
        (section, cells)
        for section, cells in video_rows
        if not TIME_RE.search(cells[6] if len(cells) > 6 else "")
    ]
    if missing_bvid:
        issues.append(f"视频证据行缺 BVID：{len(missing_bvid)} 行")
    if require_time and missing_time:
        issues.append(f"视频证据行缺时间：{len(missing_time)} 行")

    table_bvids = set()
    for _, cells in rows:
        if len(cells) > 6:
            table_bvids.update(BVID_RE.findall(cells[6]))
    full_bvids = set(BVID_RE.findall(parsed["text"]))
    no_video = []
    for section in numbered:
        section_video = [
            cells for sec, cells in rows
            if sec == section and len(cells) > 5 and _is_video_status(cells[5].strip(), video_statuses)
        ]
        if not section_video:
            no_video.append(section)

    scope_missing: list[str] = []
    scope_extra: list[str] = []
    scope_bvids = load_scope(scope)
    if scope_bvids:
        scope_missing = sorted(scope_bvids - table_bvids)
        scope_extra = sorted(table_bvids - scope_bvids)
        if scope_missing:
            issues.append(f"scope 中这些 BVID 没有逐行证据：{len(scope_missing)} 个")

    lines = [
        f"# Markdown 覆盖检查：{matrix_path}",
        "",
        f"- 结果：{'PASS' if not issues else 'FAIL'}",
        f"- 数字编号玩法：{len(numbered)}（预期 {expected_sections or '不限'}）；附录/图例：{len(appendix)}",
        f"- 数据行：{len(rows)}；7 列：{'是' if not bad_cols else '否'}；空白格行：{len(blank_rows)}",
        f"- 状态分布：" + "、".join(f"{key} {value}" for key, value in sorted(status.items())),
        f"- 视频证据行：{len(video_rows)}；缺 BVID：{len(missing_bvid)}；缺时间：{len(missing_time)}"
        + ("（--require-time 生效）" if require_time else "（仅报告，不判失败）"),
        f"- 表格证据 BVID：{len(table_bvids)} 个；全文 BVID：{len(full_bvids)} 个",
    ]
    if scope_bvids:
        lines.append(f"- scope：{len(scope_bvids)} 个；缺失 {len(scope_missing)}；额外 {len(scope_extra)}")
    lines += ["", "## 问题", ""]
    lines += [f"- {issue}" for issue in issues] if issues else ["- 无"]

    lines += ["", "## 视频行缺 BVID", ""]
    if missing_bvid:
        for section, cells in missing_bvid:
            note = cells[6] if len(cells) > 6 else ""
            lines.append(f"- {section} / {cells[0]} / {cells[5]} / {note[:80]}")
    else:
        lines.append("- 无")

    if require_time:
        lines += ["", "## 视频行缺时间", ""]
        if missing_time:
            for section, cells in missing_time:
                note = cells[6] if len(cells) > 6 else ""
                lines.append(f"- {section} / {cells[0]} / {cells[5]} / {note[:80]}")
        else:
            lines.append("- 无")

    lines += ["", "## 无确认视频证据的玩法", ""]
    lines += [f"- {section}" for section in no_video] if no_video else ["- 无"]

    if scope_bvids:
        lines += ["", "## scope 缺口", ""]
        lines += [f"- 缺：{bvid}" for bvid in scope_missing] or ["- 无"]
        if scope_extra:
            lines += [f"- 额外：{bvid}" for bvid in scope_extra]

    lines += [
        "",
        "## 口径",
        "",
        "- 视频证据状态：" + " / ".join(video_statuses),
        "- 允许“基础状态（限定）”，如 推（待核）、视频（待并入）、推（另一版视频）；基础必须是 视频 / 推 / 视频/推 / 无画面。",
        "- 只要状态属于视频证据，就必须在“证据/备注”列出现 BVID；加 `--require-time` 还必须有 mm:ss 或 hh:mm:ss。",
        "- “无画面/推”可以没有 BVID，但必须在状态列明确写出来，不能留空。",
        "- 全文出现 BVID 不等于逐行证据；本报告以表格“证据/备注”列为准。",
    ]
    return not issues, "\n".join(lines) + "\n"


def cmd_check_md(args) -> int:
    ok, report = check_md(
        Path(args.matrix),
        expected_sections=args.expected_sections,
        require_time=args.require_time,
        scope=args.scope,
        allowed=_split_list(args.allowed_status) or MD_STATUSES,
        video_statuses=_split_list(args.video_status) or MD_VIDEO_STATUSES,
    )
    output = Path(args.out) if args.out else Path(args.matrix).with_name("coverage_md_report.md")
    output.write_text(report, encoding="utf-8")
    print(f"REPORT={output}")
    print(f"RESULT={'PASS' if ok else 'FAIL'}")
    if not ok:
        in_issues = False
        for line in report.splitlines():
            if line.startswith("## "):
                in_issues = line.strip() == "## 问题"
                continue
            if in_issues and line.startswith("- "):
                print(line)
    return 0 if ok else 1


def cmd_init(args) -> int:
    spec = load_spec(args.spec)
    matrix_path = Path(args.out)
    evidence_path = Path(args.evidence_template) if args.evidence_template else None
    init_files(spec, matrix_path, evidence_path)
    print(f"MATRIX={matrix_path}")
    if evidence_path:
        print(f"EVIDENCE_TEMPLATE={evidence_path}")
    print(f"ROWS={len(spec['rows'])} COLUMNS={len(spec['columns'])} SCOPE={len(spec['scope'])}")
    print("下一步：填满矩阵和 evidence.csv，再运行 check。")
    return 0


def cmd_check(args) -> int:
    spec = load_spec(args.spec)
    matrix_path = Path(args.matrix)
    evidence_path = Path(args.evidence) if args.evidence else None
    ok, report = check(spec, matrix_path, evidence_path)
    output = Path(args.out) if args.out else matrix_path.with_name("coverage_report.md")
    output.write_text(report, encoding="utf-8")
    print(f"REPORT={output}")
    print(f"RESULT={'PASS' if ok else 'FAIL'}")
    if not ok:
        for line in report.splitlines():
            if line.startswith("- ") and ("缺" in line or "空白" in line or "非法" in line or "没有证据" in line):
                print(line)
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="全量取证覆盖矩阵：初始化与无空白检查")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="按 spec 生成矩阵 CSV")
    init.add_argument("--spec", required=True, help="spec JSON：scope/rows/columns/allowed")
    init.add_argument("--out", required=True, help="输出矩阵 CSV")
    init.add_argument("--evidence-template", default="", help="同时生成 evidence.csv 表头")
    init.set_defaults(func=cmd_init)

    check = sub.add_parser("check", help="检查矩阵无空白、BVID 有证据")
    check.add_argument("--spec", required=True)
    check.add_argument("--matrix", required=True)
    check.add_argument("--evidence", default="")
    check.add_argument("--out", default="")
    check.set_defaults(func=cmd_check)

    md = sub.add_parser("check-md", help="检查 7 列 Markdown 覆盖矩阵")
    md.add_argument("--matrix", required=True)
    md.add_argument("--expected-sections", type=int, default=0, help="玩法 section 数，FFRS 用 25；0 不限制")
    md.add_argument("--require-time", action="store_true", help="视频证据行必须有 mm:ss/hh:mm:ss 时间")
    md.add_argument("--scope", default="", help="可选：scope 文件（JSON/text）或逗号分隔 BVID")
    md.add_argument("--allowed-status", default=",".join(MD_STATUSES))
    md.add_argument("--video-status", default=",".join(MD_VIDEO_STATUSES))
    md.add_argument("--out", default="")
    md.set_defaults(func=cmd_check_md)
    return parser


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    parser = build_parser()
    args = parser.parse_args()
    try:
        return int(args.func(args) or 0)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr, flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
