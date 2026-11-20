from __future__ import annotations

import json
import math
import re
from datetime import datetime
from pathlib import Path

from bili_common import format_duration

BV_RE = re.compile(r"BV[0-9A-Za-z]{10}")
AV_RE = re.compile(r"(?<![A-Za-z0-9])av(\d{1,12})(?![A-Za-z0-9])", re.I)
B23_RE = re.compile(r"(?:https?://)?b23\.tv/[A-Za-z0-9]+", re.I)
PENALTY_WORDS = ("广告", "带货", "搬运", "直播回放", "切片")


def extract_refs(text: str) -> list[tuple[str, str | int]]:
    refs: list[tuple[str, str | int]] = []
    seen = set()
    for match in BV_RE.finditer(text or ""):
        value = match.group(0)
        if ("bvid", value) not in seen:
            seen.add(("bvid", value))
            refs.append(("bvid", value))
    for match in AV_RE.finditer(text or ""):
        value = int(match.group(1))
        if ("av", value) not in seen:
            seen.add(("av", value))
            refs.append(("av", value))
    for match in B23_RE.finditer(text or ""):
        value = match.group(0)
        if ("b23", value) not in seen:
            seen.add(("b23", value))
            refs.append(("b23", value))
    return refs


def blank_candidate(key: str) -> dict:
    bvid = key if key.startswith("BV") else None
    aid = int(key[2:]) if key.startswith("av") and key[2:].isdigit() else None
    return {
        "key": key,
        "bvid": bvid,
        "aid": aid,
        "title": "",
        "up": "",
        "up_mid": None,
        "duration": 0,
        "duration_text": "",
        "play": 0,
        "reply": 0,
        "like": 0,
        "pubdate": 0,
        "desc": "",
        "url": (
            f"https://www.bilibili.com/video/{bvid}/"
            if bvid
            else (f"https://www.bilibili.com/video/av{aid}/" if aid else "")
        ),
        "sources": {"search_rank": 0, "related_from": [], "related_rank": 0},
        "evidence": [],
        "mention_count": 0,
        "nested_mentions": 0,
        "evidence_like_sum": 0,
        "meta_status": "placeholder",
        "score": 0.0,
        "score_parts": {},
    }


def _apply_feed(candidate: dict, data: dict) -> None:
    numeric = {"play", "reply", "like", "duration", "pubdate"}
    always = {"title", "desc", "url"}
    fields = ("bvid", "aid", "title", "up", "up_mid", "duration", "play", "reply", "like", "pubdate", "desc", "url")
    for field in fields:
        value = data.get(field)
        if value in (None, ""):
            continue
        if field in numeric and int(value or 0) == 0:
            continue
        if field in always:
            candidate[field] = value
        elif not candidate.get(field):
            candidate[field] = value
    candidate["duration_text"] = format_duration(candidate.get("duration") or 0)
    if candidate.get("bvid"):
        candidate["url"] = f"https://www.bilibili.com/video/{candidate['bvid']}/"


def add_feed_candidate(candidates: dict[str, dict], data: dict, source: str, seed_bvid: str | None = None, rank: int = 0) -> dict:
    key = str(data.get("bvid") or f"av{data.get('aid')}")
    candidate = candidates.setdefault(key, blank_candidate(key))
    _apply_feed(candidate, data)
    candidate["meta_status"] = "feed"
    if source == "search":
        current = candidate["sources"].get("search_rank") or 0
        candidate["sources"]["search_rank"] = min(current, rank) if current else rank
    elif source == "related":
        sources = candidate["sources"]
        if seed_bvid and seed_bvid not in sources["related_from"]:
            sources["related_from"].append(seed_bvid)
        current = sources.get("related_rank") or 0
        sources["related_rank"] = min(current, rank) if current else rank
    return candidate


def add_comment_evidence(
    candidates: dict[str, dict],
    key: str,
    evidence: dict,
    data: dict | None = None,
) -> dict:
    candidate = candidates.setdefault(key, blank_candidate(key))
    if data:
        _apply_feed(candidate, data)
    dedupe = (evidence.get("source_bvid"), evidence.get("comment_rpid"), key)
    existing = {
        (item.get("source_bvid"), item.get("comment_rpid"), key)
        for item in candidate["evidence"]
    }
    if dedupe in existing:
        return candidate
    candidate["evidence"].append(evidence)
    candidate["mention_count"] += 1
    candidate["evidence_like_sum"] += int(evidence.get("likes") or 0)
    if evidence.get("nested"):
        candidate["nested_mentions"] += 1
    if candidate["meta_status"] == "feed":
        candidate["meta_status"] = "feed"
    return candidate


def promote_candidate(candidates: dict[str, dict], old_key: str, info: dict) -> str:
    bvid = str(info.get("bvid") or "")
    if not bvid or bvid == old_key:
        return old_key
    old = candidates.pop(old_key, None)
    candidate = candidates.setdefault(bvid, blank_candidate(bvid))
    _apply_feed(
        candidate,
        {
            "bvid": bvid,
            "aid": info.get("aid"),
            "title": info.get("title"),
            "up": (info.get("owner") or {}).get("name"),
            "up_mid": (info.get("owner") or {}).get("mid"),
            "duration": info.get("duration"),
            "play": (info.get("stat") or {}).get("view"),
            "reply": (info.get("stat") or {}).get("reply"),
            "like": (info.get("stat") or {}).get("like"),
            "pubdate": info.get("pubdate"),
            "desc": info.get("desc"),
            "url": f"https://www.bilibili.com/video/{bvid}/",
        },
    )
    candidate["meta_status"] = "info"
    if old:
        candidate["evidence"].extend(old.get("evidence") or [])
        candidate["mention_count"] += int(old.get("mention_count") or 0)
        candidate["nested_mentions"] += int(old.get("nested_mentions") or 0)
        candidate["evidence_like_sum"] += int(old.get("evidence_like_sum") or 0)
        old_sources = old.get("sources") or {}
        candidate["sources"]["search_rank"] = candidate["sources"].get("search_rank") or old_sources.get("search_rank") or 0
        for seed in old_sources.get("related_from") or []:
            if seed not in candidate["sources"]["related_from"]:
                candidate["sources"]["related_from"].append(seed)
    return bvid


def fill_candidate(candidates: dict[str, dict], key: str, info: dict) -> str:
    if key.startswith("BV") and key == str(info.get("bvid") or ""):
        candidate = candidates.setdefault(key, blank_candidate(key))
        _apply_feed(candidate, {
            "bvid": key,
            "aid": info.get("aid"),
            "title": info.get("title"),
            "up": (info.get("owner") or {}).get("name"),
            "up_mid": (info.get("owner") or {}).get("mid"),
            "duration": info.get("duration"),
            "play": (info.get("stat") or {}).get("view"),
            "reply": (info.get("stat") or {}).get("reply"),
            "like": (info.get("stat") or {}).get("like"),
            "pubdate": info.get("pubdate"),
            "desc": info.get("desc"),
            "url": f"https://www.bilibili.com/video/{key}/",
        })
        candidate["meta_status"] = "info"
        return key
    return promote_candidate(candidates, key, info)


def score_candidates(candidates: dict[str, dict]) -> list[dict]:
    for candidate in candidates.values():
        evidence = candidate.get("evidence") or []
        parts = {}
        parts["comment_mentions"] = 6 * len(evidence) + 8 * len({e.get("source_bvid") for e in evidence})
        parts["comment_likes"] = sum(
            min(15.0, 2.0 + math.log10(1 + max(0, int(e.get("likes") or 0)))) * (1.5 if e.get("nested") else 1.0)
            for e in evidence[:20]
        )
        related_count = len((candidate.get("sources") or {}).get("related_from") or [])
        parts["related"] = min(24.0, 8.0 * related_count)
        search_rank = int((candidate.get("sources") or {}).get("search_rank") or 0)
        parts["search"] = max(0.0, 12.0 - min(12, search_rank)) if search_rank else 0.0
        parts["engagement"] = (
            min(10.0, math.log10(1 + max(0, int(candidate.get("play") or 0))))
            + min(10.0, math.log10(1 + max(0, int(candidate.get("reply") or 0))) + 1.0)
            + min(5.0, math.log10(1 + max(0, int(candidate.get("like") or 0))))
        )
        reply_ratio = int(candidate.get("reply") or 0) / max(1, int(candidate.get("play") or 0))
        parts["reply_ratio"] = min(8.0, reply_ratio * 200)
        text = f"{candidate.get('title', '')} {candidate.get('desc', '')}".lower()
        penalty = 0.0
        for word in PENALTY_WORDS:
            if word in text:
                penalty -= 15.0
        duration = int(candidate.get("duration") or 0)
        if duration >= 6 * 3600:
            penalty -= 20.0
        elif duration >= 3 * 3600:
            penalty -= 10.0
        elif 0 < duration < 30:
            penalty -= 5.0
        parts["penalty"] = penalty
        candidate["score_parts"] = {key: round(value, 2) for key, value in parts.items()}
        candidate["score"] = round(sum(parts.values()), 1)
    return sorted(candidates.values(), key=lambda item: item.get("score") or 0, reverse=True)


def _clean_cell(value) -> str:
    return str(value or "").replace("|", "/").replace("\n", " ").strip()


def _source_label(candidate: dict) -> str:
    sources = candidate.get("sources") or {}
    labels = []
    if sources.get("search_rank"):
        labels.append(f"搜索#{sources['search_rank']}")
    related = len(sources.get("related_from") or [])
    if related:
        labels.append(f"相关×{related}")
    mentions = int(candidate.get("mention_count") or 0)
    if mentions:
        nested = int(candidate.get("nested_mentions") or 0)
        labels.append(f"评论×{mentions}" + (f"(楼{nested})" if nested else ""))
    return " + ".join(labels) or "候选"


def _date_text(timestamp) -> str:
    if not timestamp:
        return ""
    try:
        return datetime.fromtimestamp(int(timestamp)).strftime("%Y-%m-%d")
    except Exception:
        return ""


def _score_parts_text(parts: dict) -> str:
    labels = [
        ("评论", "comment_mentions"),
        ("赞", "comment_likes"),
        ("相关", "related"),
        ("搜索", "search"),
        ("互动", "engagement"),
        ("评论率", "reply_ratio"),
        ("扣分", "penalty"),
    ]
    return " · ".join(f"{label}{float(parts.get(key) or 0):g}" for label, key in labels)


def render_markdown(result: dict, candidates: list[dict], top: int = 20) -> str:
    stats = result.get("stats") or {}
    lines = [
        f"# B 站精华挖掘：{result['query']}",
        "",
        f"- 模式：{'关键词搜索' if result['mode'] == 'keyword' else '种子视频'}",
        f"- 种子：{len(result.get('seeds') or [])} 个；候选：{stats.get('candidates', 0)} 个",
        f"- 搜索命中：{stats.get('search_results', 0)}；相关视频：{stats.get('related_items', 0)}；评论引用：{stats.get('comment_refs', 0)}",
        f"- 生成时间：{result['generated_at']}",
        "- 评分：评论证据 > 相关共现 > 搜索/互动；广告/带货/搬运/直播回放降权。",
        "- 标题/简介简单、无配音/字幕不代表不是精华；先看候选，再决定 watch。",
        "",
        "## 种子",
        "",
    ]
    for seed in result.get("seeds") or []:
        lines.append(f"- [{seed.get('bvid')}]({seed.get('url')}) {seed.get('title')}（{seed.get('source', '')}）")
    lines += [
        "",
        "## 候选表",
        "",
        "| # | BV | 标题 | UP | 时长 | 播放 | 评论 | 来源 | 分数 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for index, candidate in enumerate(candidates[:top], 1):
        lines.append(
            "| {index} | [{bvid}]({url}) | {title} | {up} | {duration} | {play} | {reply} | {source} | {score} |".format(
                index=index,
                bvid=_clean_cell(candidate.get("bvid") or f"av{candidate.get('aid')}"),
                url=candidate.get("url") or "",
                title=_clean_cell(candidate.get("title") or "（待取元数据）")[:60],
                up=_clean_cell(candidate.get("up"))[:20],
                duration=candidate.get("duration_text") or "?",
                play=candidate.get("play") or 0,
                reply=candidate.get("reply") or 0,
                source=_source_label(candidate),
                score=candidate.get("score") or 0,
            )
        )
    lines += ["", "## 候选明细", ""]
    for index, candidate in enumerate(candidates[: min(10, top)], 1):
        lines += [
            f"### {index}. [{candidate.get('bvid') or 'av' + str(candidate.get('aid'))}]({candidate.get('url')}) {candidate.get('title') or '（待取元数据）'}",
            f"- UP：{candidate.get('up') or '?'} · 时长：{candidate.get('duration_text') or '?'} · 播放：{candidate.get('play') or 0} · 评论：{candidate.get('reply') or 0} · 发布：{_date_text(candidate.get('pubdate'))}",
            f"- 来源：{_source_label(candidate)} · 分数：{candidate.get('score')}（{_score_parts_text(candidate.get('score_parts') or {})}）",
        ]
        evidence = candidate.get("evidence") or []
        if evidence:
            lines.append("- 评论证据：")
            for item in evidence[:5]:
                nested = "楼中楼" if item.get("nested") else "主评论"
                text = str(item.get("text") or "").replace("\n", " ")[:180]
                lines.append(
                    f"  - [{nested} · 赞{item.get('likes') or 0}] “{text}” —— 来自 {item.get('source_bvid')}"
                )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def save_discovery(result: dict, candidates: list[dict], out_dir: str | Path, top: int = 20) -> dict:
    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    payload = dict(result)
    payload["candidates"] = candidates
    json_path = directory / "discovery.json"
    md_path = directory / "discovery.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(result, candidates, top), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path)}
