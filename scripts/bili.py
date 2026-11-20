from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import re
import subprocess
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

import bili_api
import bili_auth
import bili_discovery
import bili_media
from bili_common import (
    AUTH_JSON,
    CHROME_PROFILE,
    DATA_ROOT,
    MODELS_DIR,
    RUNS_DIR,
    VENV_PYTHON,
    YUTTO_AUTH,
    YUTTO_EXE,
    ffmpeg_bin,
    ffprobe_bin,
    format_duration,
    load_cookies,
)

VIDEO_SUFFIXES = {".mp4", ".mkv", ".mov", ".flv", ".webm"}


def emit(*lines) -> None:
    for line in lines:
        print(line, flush=True)


def _new_dir(prefix: str, base: Path | None = None) -> Path:
    root = base or RUNS_DIR
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    directory = root / f"{stamp}_{prefix}"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _video_prefix(info: dict) -> str:
    return str(info.get("bvid") or f"av{info.get('aid')}")


def _selected_page(info: dict, page: int) -> dict:
    pages = info.get("pages") or []
    if 0 < page <= len(pages):
        return pages[page - 1]
    return {}


def _meta(info: dict, page: int) -> dict:
    selected = _selected_page(info, page)
    stat = info.get("stat") or {}
    return {
        "bvid": info.get("bvid"),
        "aid": info.get("aid"),
        "title": info.get("title"),
        "up": (info.get("owner") or {}).get("name"),
        "up_mid": (info.get("owner") or {}).get("mid"),
        "page": page,
        "pages": len(info.get("pages") or []),
        "page_title": selected.get("part"),
        "duration": selected.get("duration") or info.get("duration"),
        "duration_text": format_duration(selected.get("duration") or info.get("duration")),
        "url": f"https://www.bilibili.com/video/{info.get('bvid')}/" if info.get("bvid") else f"https://www.bilibili.com/video/av{info.get('aid')}",
        "stats": {
            "view": stat.get("view"),
            "danmaku": stat.get("danmaku"),
            "reply": stat.get("reply"),
            "like": stat.get("like"),
            "coin": stat.get("coin"),
            "favorite": stat.get("favorite"),
            "share": stat.get("share"),
        },
        "pubdate": info.get("pubdate"),
    }


def _resolve_quality(value: str, logged_in: bool) -> int:
    if value and value != "auto":
        return int(value)
    return 80 if logged_in else 32


def _run_yutto(
    url: str,
    out_dir: Path,
    page: int,
    quality: int,
    danmaku: bool,
    subtitles: bool,
    interval: float | None,
) -> None:
    if not YUTTO_EXE.exists():
        raise RuntimeError(f"yutto 不存在：{YUTTO_EXE}")
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(YUTTO_EXE),
        url,
        "-d",
        str(out_dir),
        "-q",
        str(quality),
        "--no-progress",
        "--no-cover",
    ]
    if not danmaku:
        cmd.append("--no-danmaku")
    if not subtitles:
        cmd.append("--no-subtitle")
    if interval:
        cmd += ["--download-interval", str(interval)]
    if YUTTO_AUTH.exists():
        cmd += ["--auth-file", str(YUTTO_AUTH)]
    if page and page > 1:
        cmd += ["-p", str(page)]
    ffmpeg = ffmpeg_bin()
    if ffmpeg:
        cmd += ["--ffmpeg-path", ffmpeg]

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["NO_COLOR"] = "1"
    emit(f"下载命令：{' '.join(cmd)}")
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=7200,
        env=env,
    )
    combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
    tail = "\n".join(combined.splitlines()[-30:])
    emit(tail)
    if proc.returncode != 0:
        raise RuntimeError(f"yutto 下载失败（exit={proc.returncode}）")


def _find_video(video_dir: Path) -> Path:
    files = [
        path
        for path in video_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in VIDEO_SUFFIXES and path.stat().st_size > 0
    ]
    if not files:
        raise RuntimeError(f"下载目录里找不到视频文件：{video_dir}")
    return max(files, key=lambda path: path.stat().st_size)


def _download_video(
    target: str,
    page: int,
    quality: int,
    out_dir: Path,
    danmaku: bool,
    subtitles: bool,
    interval: float | None,
) -> Path:
    parsed = bili_api.parse_target(target)
    url = parsed["url"]
    if page and page > 1:
        url = f"{url}?p={page}"
    _run_yutto(url, out_dir, page, quality, danmaku, subtitles, interval)
    return _find_video(out_dir)


def _save_meta(path: Path, meta: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def cmd_doctor(args) -> int:
    emit(f"DATA_ROOT={DATA_ROOT}")
    emit(f"PYTHON={sys.executable}")
    emit(f"VENV_PYTHON={VENV_PYTHON} exists={VENV_PYTHON.exists()}")
    emit(f"YUTTO={YUTTO_EXE} exists={YUTTO_EXE.exists()}")
    try:
        emit(f"YUTTO_VERSION={importlib.metadata.version('yutto')}")
    except Exception:
        emit("YUTTO_VERSION=unknown")
    emit(f"FFMPEG={ffmpeg_bin() or 'MISSING'}")
    emit(f"FFPROBE={ffprobe_bin() or 'MISSING'}")
    try:
        import faster_whisper

        emit(f"FASTER_WHISPER={faster_whisper.__version__}")
    except Exception as exc:
        emit(f"FASTER_WHISPER=MISSING ({exc})")
    try:
        import av

        emit(f"PYAV={av.__version__}")
        major = int(str(av.__version__).split(".")[0])
        if major >= 19:
            emit("PYAV_WARNING=PyAV 19 与 faster-whisper 1.2.1 不兼容（metadata_errors），请装 av==17.1.0")
    except Exception as exc:
        emit(f"PYAV=MISSING ({exc})")
    models = []
    if MODELS_DIR.exists():
        models = [str(path) for path in MODELS_DIR.glob("**/model.bin") if path.is_file()]
    emit(f"WHISPER_MODELS={len(models)}")
    for path in models[:5]:
        emit(f"  {path}")
    try:
        chrome = bili_auth._find_chrome()
        emit(f"CHROME={chrome}")
    except Exception as exc:
        emit(f"CHROME=MISSING ({exc})")
    emit(f"AUTH_FILE={AUTH_JSON} exists={AUTH_JSON.exists()}")
    cookies = load_cookies()
    if cookies:
        login = bili_api.check_login(cookies)
        if login:
            emit(f"LOGIN=ok user={login.get('uname')} mid={login.get('mid')}")
        else:
            emit("LOGIN=expired_or_invalid")
    else:
        emit("LOGIN=none")
    emit(f"CHROME_PROFILE={CHROME_PROFILE}")

    critical_ok = YUTTO_EXE.exists() and bool(ffmpeg_bin()) and bool(ffprobe_bin())
    emit(f"DOCTOR={'ok' if critical_ok else 'missing_critical'}")
    return 0 if critical_ok else 1


def cmd_info(args) -> int:
    cookies = load_cookies()
    info = bili_api.video_info(args.target, cookies)
    page = args.page or bili_api.parse_target(args.target).get("page") or 1
    meta = _meta(info, page)
    if args.json:
        emit(json.dumps({"meta": meta, "raw": info}, ensure_ascii=False, indent=2))
        return 0
    emit(
        f"TITLE={meta['title']}",
        f"BV={meta['bvid']} AID={meta['aid']}",
        f"UP={meta['up']} UP_MID={meta['up_mid']}",
        f"PAGE={meta['page']}/{meta['pages']} PAGE_TITLE={meta['page_title']}",
        f"DURATION={meta['duration_text']} ({meta['duration']}s)",
        f"VIEWS={meta['stats']['view']} COMMENTS={meta['stats']['reply']} DANMAKU={meta['stats']['danmaku']}",
        f"URL={meta['url']}",
    )
    return 0


def cmd_download(args) -> int:
    cookies = load_cookies()
    login = bili_api.check_login(cookies)
    info = bili_api.video_info(args.target, cookies)
    page = args.page or bili_api.parse_target(args.target).get("page") or 1
    quality = _resolve_quality(args.quality, bool(login))
    directory = Path(args.out) if args.out else _new_dir(f"{_video_prefix(info)}_download")
    emit(f"OUT_DIR={directory}")
    video = _download_video(
        args.target, page, quality, directory / "video", args.danmaku, not args.no_subtitle, args.interval
    )
    meta = _meta(info, page)
    meta["quality"] = quality
    meta["video"] = str(video)
    _save_meta(directory / "meta.json", meta)
    emit(f"VIDEO={video}")
    emit(f"VIDEO_SIZE={video.stat().st_size}")
    emit(f"QUALITY={quality}")
    emit(f"LOGIN={'yes' if login else 'no'}")
    return 0


def cmd_comments(args) -> int:
    cookies = load_cookies()
    info = bili_api.video_info(args.target, cookies)
    directory = Path(args.out) if args.out else _new_dir(f"{_video_prefix(info)}_comments")
    emit(f"COMMENTS_DIR={directory}")
    result = bili_api.fetch_comments(
        args.target,
        cookies=cookies,
        limit=args.limit,
        max_sub=args.max_sub,
        max_sub_total=args.max_sub_total,
        sort=args.sort,
        delay=args.delay,
        include_subs=not args.no_subs,
        progress=emit,
    )
    paths = bili_api.save_comments(result, directory)
    stats = result["stats"]
    emit(
        f"COMMENTS_JSON={paths['json']}",
        f"COMMENTS_MD={paths['markdown']}",
        f"TOTAL={stats['total_count']}",
        f"ROOTS={stats['roots_fetched']}",
        f"SUBS={stats['subs_fetched']}",
        f"LOGIN={'yes' if result['login']['logged_in'] else 'no'}",
    )
    for warning in result["warnings"]:
        emit(f"WARNING={warning}")
    if stats["login_limited"]:
        emit("LOGIN_REQUIRED=1")
        emit("下一步：运行 `python bili.py auth` 让用户在弹出的 Chrome 里登录 B 站，然后重跑 comments。")
        return 3
    return 0


def _looks_like_video(value: str) -> bool:
    value = (value or "").strip()
    if bili_api.BV_RE.search(value):
        return True
    if "bilibili.com/video" in value or "b23.tv" in value:
        return True
    return bool(re.fullmatch(r"av\d{1,12}", value, re.I))


def _safe_slug(value: str, max_len: int = 40) -> str:
    slug = re.sub(r'[\\/:*?"<>|\s]+', "_", value).strip("_")
    return (slug[:max_len] or "query")


def cmd_explore(args) -> int:
    cookies = load_cookies()
    raw = (args.query or "").strip()
    if not raw:
        raise RuntimeError("缺少关键词或视频地址")
    directory = Path(args.out) if args.out else _new_dir(f"explore_{_safe_slug(raw)}")
    emit(f"DISCOVERY_DIR={directory}")

    candidates: dict[str, dict] = {}
    seeds: list[dict] = []
    warnings: list[str] = []
    stats = {
        "search_results": 0,
        "search_total": 0,
        "related_items": 0,
        "comment_refs": 0,
        "candidates": 0,
        "enrich_failures": 0,
    }

    if _looks_like_video(raw):
        info = bili_api.video_info(raw, cookies)
        bvid = str(info.get("bvid") or "")
        if not bvid:
            raise RuntimeError("种子视频没有 bvid")
        seed = {
            "bvid": bvid,
            "aid": info.get("aid"),
            "title": str(info.get("title") or ""),
            "url": f"https://www.bilibili.com/video/{bvid}/",
            "source": "种子视频",
        }
        seeds.append(seed)
        emit(f"SEED={bvid} {seed['title']}")
    else:
        page_size = 20
        collected = 0
        for page in range(1, max(1, args.pages) + 1):
            results, total = bili_api.search_videos(
                raw, cookies, page=page, page_size=page_size, order=args.order
            )
            stats["search_total"] = max(stats["search_total"], total)
            if not results:
                break
            for item in results:
                global_rank = (page - 1) * page_size + int(item.get("search_rank") or 0)
                item["search_rank"] = global_rank
                bili_discovery.add_feed_candidate(candidates, item, "search", rank=global_rank)
                stats["search_results"] += 1
                collected += 1
                if len(seeds) < args.seed_limit:
                    seeds.append(
                        {
                            "bvid": item["bvid"],
                            "aid": item.get("aid"),
                            "title": item.get("title") or "",
                            "url": item.get("url") or "",
                            "source": f"搜索#{global_rank}",
                        }
                    )
                if collected >= args.search_limit:
                    break
            if collected >= args.search_limit:
                break
            time.sleep(args.delay)
        if not candidates:
            raise RuntimeError(f"搜索没有结果：{raw}")
        emit(f"SEARCH_RESULTS={stats['search_results']} / total={stats['search_total'] or '?'}")

    for index, seed in enumerate(seeds[: max(0, args.seed_limit)], 1):
        emit(f"COMMENTS {index}/{min(len(seeds), args.seed_limit)} {seed['bvid']}")
        try:
            result = bili_api.fetch_comments(
                seed["bvid"],
                cookies=cookies,
                limit=args.comment_limit,
                max_sub=args.max_sub,
                max_sub_total=args.max_sub_total,
                sort="hot",
                delay=args.delay,
                include_subs=True,
                progress=emit,
            )
        except bili_api.RiskControlError:
            raise
        except bili_api.ApiError as exc:
            warnings.append(f"评论失败 {seed['bvid']}：{exc}")
            emit(f"WARNING=评论失败 {seed['bvid']}：{exc}")
            continue

        if result["stats"].get("login_limited"):
            warning = f"{seed['bvid']} 未登录只回 3 条热评，explore 的评论证据会偏少；先跑 auth"
            warnings.append(warning)
            emit(f"WARNING={warning}")

        seed_aid = seed.get("aid")

        def collect(text: str, reply: dict, nested: bool, source_seed: dict = seed) -> None:
            for kind, value in bili_discovery.extract_refs(text):
                if kind == "bvid":
                    target_key = str(value)
                elif kind == "av":
                    target_key = f"av{value}"
                else:
                    try:
                        parsed = bili_api.parse_target(str(value))
                    except bili_api.ApiError:
                        continue
                    target_key = parsed.get("bvid") or (
                        f"av{parsed['aid']}" if parsed.get("aid") else ""
                    )
                if not target_key:
                    continue
                if target_key == source_seed.get("bvid") or (
                    seed_aid and target_key == f"av{seed_aid}"
                ):
                    continue
                evidence = {
                    "source_bvid": source_seed.get("bvid"),
                    "source_title": source_seed.get("title"),
                    "comment_rpid": reply.get("rpid"),
                    "uname": reply.get("uname"),
                    "likes": int(reply.get("likes") or 0),
                    "nested": nested,
                    "text": str(reply.get("message") or "")[:300],
                    "time": reply.get("time"),
                }
                bili_discovery.add_comment_evidence(candidates, target_key, evidence)
                stats["comment_refs"] += 1

        for root in result["roots"]:
            collect(root.get("message") or "", root, False, seed)
            for sub in root.get("replies") or []:
                collect(sub.get("message") or "", sub, True, seed)
        emit(f"COMMENT_REFS={stats['comment_refs']}")

    for index, seed in enumerate(seeds[: max(0, args.related_limit)], 1):
        emit(f"RELATED {index}/{min(len(seeds), args.related_limit)} {seed['bvid']}")
        try:
            items = bili_api.related_videos(seed["bvid"], cookies)
        except bili_api.RiskControlError:
            raise
        except bili_api.ApiError as exc:
            warnings.append(f"相关视频失败 {seed['bvid']}：{exc}")
            emit(f"WARNING=相关视频失败 {seed['bvid']}：{exc}")
            continue
        for item in items:
            if item.get("bvid") == seed.get("bvid"):
                continue
            bili_discovery.add_feed_candidate(
                candidates,
                item,
                "related",
                seed_bvid=seed.get("bvid"),
                rank=int(item.get("related_rank") or 0),
            )
            stats["related_items"] += 1
        time.sleep(args.delay)

    pending = [
        candidate
        for candidate in candidates.values()
        if candidate.get("meta_status") == "placeholder"
    ]
    pending.sort(
        key=lambda item: (item.get("mention_count") or 0, item.get("evidence_like_sum") or 0),
        reverse=True,
    )
    for index, candidate in enumerate(pending[: max(0, args.candidate_limit)], 1):
        key = candidate.get("key") or candidate.get("bvid") or ""
        emit(f"ENRICH {index}/{min(len(pending), args.candidate_limit)} {key}")
        try:
            info = bili_api.video_info(key, cookies)
        except bili_api.RiskControlError:
            raise
        except bili_api.ApiError as exc:
            candidate["meta_status"] = "error"
            stats["enrich_failures"] += 1
            warnings.append(f"元数据失败 {key}：{exc}")
            continue
        bili_discovery.fill_candidate(candidates, key, info)
        time.sleep(args.delay)

    scored = bili_discovery.score_candidates(candidates)
    usable = [item for item in scored if item.get("meta_status") != "error"]
    stats["candidates"] = len(usable)
    result = {
        "query": raw,
        "mode": "keyword" if not _looks_like_video(raw) else "seed",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "seeds": seeds,
        "stats": stats,
    }
    paths = bili_discovery.save_discovery(result, usable, directory, top=args.top)
    emit(f"DISCOVERY_MD={paths['markdown']}")
    emit(f"DISCOVERY_JSON={paths['json']}")
    emit(
        f"CANDIDATES={stats['candidates']} COMMENT_REFS={stats['comment_refs']} "
        f"RELATED={stats['related_items']} SEARCH={stats['search_results']}"
    )
    for warning in warnings:
        emit(f"WARNING={warning}")
    for index, item in enumerate(usable[: min(10, args.top)], 1):
        emit(
            f"TOP{index}={item.get('score')} {item.get('bvid') or 'av' + str(item.get('aid'))} "
            f"{str(item.get('title') or '（待取元数据）')[:60]}"
        )
    return 0


def cmd_frames(args) -> int:
    video = Path(args.video)
    if not video.exists():
        raise RuntimeError(f"视频不存在：{video}")
    directory = Path(args.out) if args.out else video.parent / "frames"
    manifest = bili_media.extract_frames(
        video,
        directory,
        even=args.even,
        interval=args.interval,
        scene_threshold=args.scene_threshold,
        scene_max=args.scene_max,
        max_total=args.max_total,
        width=args.width,
    )
    emit(f"FRAMES_DIR={directory}")
    emit(f"FRAMES_JSON={directory / 'frames.json'}")
    emit(f"FRAMES={len(manifest['frames'])} SHEETS={len(manifest['sheets'])}")
    for sheet in manifest["sheets"]:
        emit(f"SHEET={sheet}")
    return 0


def cmd_transcribe(args) -> int:
    video = Path(args.video)
    if not video.exists():
        raise RuntimeError(f"视频不存在：{video}")
    directory = Path(args.out) if args.out else video.parent / "transcript"
    result = bili_media.transcribe(
        video,
        directory,
        model_name=args.model,
        language=args.language,
        max_minutes=args.max_minutes,
        vad=args.vad,
        progress=emit,
    )
    emit(
        f"TRANSCRIPT_TXT={result['txt']}",
        f"TRANSCRIPT_SRT={result['srt']}",
        f"TRANSCRIPT_JSON={result['json']}",
        f"SEGMENTS={result['segments']}",
    )
    return 0


def cmd_watch(args) -> int:
    cookies = load_cookies()
    login = bili_api.check_login(cookies)
    info = bili_api.video_info(args.target, cookies)
    page = args.page or bili_api.parse_target(args.target).get("page") or 1
    meta = _meta(info, page)
    directory = Path(args.out) if args.out else _new_dir(_video_prefix(info))
    emit(f"RUN_DIR={directory}")
    _save_meta(directory / "meta.json", meta)
    emit(f"TITLE={meta['title']}")
    emit(f"DURATION={meta['duration_text']} PAGE={page}/{meta['pages']}")

    warnings: list[str] = []
    if args.video:
        video = Path(args.video)
        if not video.exists():
            raise RuntimeError(f"本地视频不存在：{video}")
    else:
        quality = _resolve_quality(args.quality, bool(login))
        emit(f"LOGIN={'yes' if login else 'no'} QUALITY={quality}")
        video = _download_video(
            args.target, page, quality, directory / "video", args.danmaku, not args.no_subtitle, None
        )
    emit(f"VIDEO={video}")

    probe = bili_media.probe_media(video)
    emit(f"PROBE={probe['width']}x{probe['height']} {probe['duration_text']} audio={probe['has_audio']}")

    frames = bili_media.extract_frames(
        video,
        directory / "frames",
        even=args.even,
        interval=args.interval,
        scene_threshold=args.scene_threshold,
        scene_max=0 if args.no_scene else args.scene_max,
        max_total=args.max_total,
        width=args.width,
    )
    emit(f"FRAMES={len(frames['frames'])} SHEETS={len(frames['sheets'])}")
    for sheet in frames["sheets"]:
        emit(f"SHEET={sheet}")

    transcript = None
    subtitle = bili_media.collect_subtitles(video.parent, directory / "transcript", video.stem)
    if subtitle:
        emit(f"SUBTITLE_TRANSCRIPT={subtitle['txt']} SOURCE={subtitle['source']}")
        transcript = subtitle
    if (not transcript or args.force_transcribe) and not args.no_transcribe:
        try:
            transcript = bili_media.transcribe(
                video,
                directory / "transcript",
                model_name=args.model,
                language=args.language,
                max_minutes=args.max_minutes,
                vad=args.vad,
                progress=emit,
            )
            emit(f"TRANSCRIPT_TXT={transcript['txt']}")
        except Exception as exc:
            warning = f"转写失败：{exc}"
            warnings.append(warning)
            emit(f"WARNING={warning}")
    elif not transcript:
        warnings.append("没有字幕，且已跳过转写")
        emit("WARNING=没有字幕，且已跳过转写")

    comments_paths = None
    comments_status = "skipped"
    comments_exit = 0
    if not args.no_comments:
        try:
            result = bili_api.fetch_comments(
                args.target,
                cookies=cookies,
                limit=args.limit,
                max_sub=args.max_sub,
                max_sub_total=args.max_sub_total,
                sort=args.sort,
                delay=args.delay,
                include_subs=not args.no_subs,
                progress=emit,
            )
            comments_paths = bili_api.save_comments(result, directory / "comments")
            comments_status = "partial" if result["stats"]["login_limited"] else "ok"
            if result["stats"]["login_limited"]:
                comments_status = "login_required"
                warnings.append("评论区只拿到 3 条热评：需要登录后重跑 comments 才能抓全。")
            emit(f"COMMENTS_MD={comments_paths['markdown']}")
            emit(f"COMMENTS_STATUS={comments_status}")
        except bili_api.RiskControlError as exc:
            comments_status = "risk_control"
            comments_exit = 2
            warnings.append(f"评论抓取触发风控：{exc}")
            emit(f"COMMENTS_STATUS=risk_control")
            emit(f"WARNING={exc}")
        except bili_api.ApiError as exc:
            comments_status = "error"
            warnings.append(f"评论抓取失败：{exc}")
            emit(f"COMMENTS_STATUS=error")
            emit(f"WARNING={exc}")

    manifest = {
        "meta": meta,
        "probe": probe,
        "video": str(video),
        "frames": {
            "dir": str(directory / "frames"),
            "count": len(frames["frames"]),
            "sheets": frames["sheets"],
            "manifest": str(directory / "frames" / "frames.json"),
        },
        "transcript": transcript,
        "comments": comments_paths,
        "comments_status": comments_status,
        "warnings": warnings,
        "login": {"logged_in": bool(login), "uname": (login or {}).get("uname")},
    }
    _save_meta(directory / "run.json", manifest)
    emit(f"RUN_JSON={directory / 'run.json'}")
    for warning in warnings:
        emit(f"WARNING={warning}")
    return comments_exit


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="B 站视频下载、抽帧/转写与评论（含楼中楼）采集")
    parser.add_argument("--debug", action="store_true", help="异常时打印 traceback")
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor", help="检查依赖、模型与登录状态")
    doctor.set_defaults(func=cmd_doctor)

    auth = sub.add_parser("auth", help="开独立 Chrome 登录并保存 cookie")
    auth.add_argument("--check", action="store_true", help="只检查本地登录状态")
    auth.add_argument("--clear", action="store_true", help="删除本地登录文件")
    auth.add_argument("--timeout", type=int, default=300, help="等待登录秒数，默认 300")
    auth.add_argument("--port", type=int, default=9223, help="Chrome 调试端口，默认 9223（避开常见的 9222 调试窗）")
    auth.set_defaults(func=lambda a: bili_auth.check_auth() if a.check else (bili_auth.logout() if a.clear else bili_auth.login_via_chrome(a.port, a.timeout)))

    info = sub.add_parser("info", help="读取视频信息")
    info.add_argument("target")
    info.add_argument("--page", type=int, default=0)
    info.add_argument("--json", action="store_true")
    info.set_defaults(func=cmd_info)

    download = sub.add_parser("download", help="用 yutto 下载视频")
    download.add_argument("target")
    download.add_argument("--page", type=int, default=0)
    download.add_argument("--quality", default="auto", help="auto 或 yutto 清晰度代码（80=1080P, 32=480P）")
    download.add_argument("--out", default="")
    download.add_argument("--danmaku", action="store_true", help="同时生成弹幕")
    download.add_argument("--no-subtitle", action="store_true")
    download.add_argument("--interval", type=float, default=None, help="分P间隔秒数")
    download.set_defaults(func=cmd_download)

    comments = sub.add_parser("comments", help="抓取评论与楼中楼")
    comments.add_argument("target")
    comments.add_argument("--limit", type=int, default=100, help="主评论条数上限，默认 100")
    comments.add_argument("--max-sub", type=int, default=100, help="单条评论的楼中楼上限，默认 100")
    comments.add_argument("--max-sub-total", type=int, default=600, help="楼中楼总预算，默认 600")
    comments.add_argument("--sort", choices=["hot", "time", "reply"], default="hot")
    comments.add_argument("--delay", type=float, default=1.5, help="请求间隔秒数，默认 1.5")
    comments.add_argument("--no-subs", action="store_true", help="不抓楼中楼")
    comments.add_argument("--out", default="")
    comments.set_defaults(func=cmd_comments)

    explore = sub.add_parser("explore", help="从关键词/种子视频挖评论引用与相关视频候选")
    explore.add_argument("query", help="关键词，或 BV/av/视频链接")
    explore.add_argument("--pages", type=int, default=1, help="搜索页数，默认 1（每页 20 条）")
    explore.add_argument("--search-limit", type=int, default=20, help="搜索候选上限，默认 20")
    explore.add_argument("--seed-limit", type=int, default=3, help="取前几个种子抓评论，默认 3")
    explore.add_argument("--related-limit", type=int, default=5, help="取前几个种子抓相关视频，默认 5")
    explore.add_argument("--comment-limit", type=int, default=60, help="每个种子的主评论上限，默认 60")
    explore.add_argument("--max-sub", type=int, default=20, help="每个种子的单楼上限，默认 20")
    explore.add_argument("--max-sub-total", type=int, default=80, help="每个种子的楼中楼预算，默认 80")
    explore.add_argument("--candidate-limit", type=int, default=20, help="补元数据的评论候选上限，默认 20")
    explore.add_argument("--top", type=int, default=20, help="输出候选表条数，默认 20")
    explore.add_argument("--order", default="totalrank", help="搜索排序：totalrank/click/pubdate/dm/stow")
    explore.add_argument("--delay", type=float, default=1.2, help="请求间隔秒数，默认 1.2")
    explore.add_argument("--out", default="")
    explore.set_defaults(func=cmd_explore)

    frames = sub.add_parser("frames", help="从本地视频抽关键帧与九宫格")
    frames.add_argument("video")
    frames.add_argument("--out", default="")
    frames.add_argument("--even", type=int, default=12)
    frames.add_argument("--interval", type=float, default=0, help="每 N 秒一帧；全量模式用 5，配合 --max-total 0")
    frames.add_argument("--scene-max", type=int, default=24)
    frames.add_argument("--scene-threshold", type=float, default=0.30)
    frames.add_argument("--max-total", type=int, default=36, help="总帧数上限；0 表示不设上限（全量模式）")
    frames.add_argument("--width", type=int, default=960)
    frames.set_defaults(func=cmd_frames)

    transcribe = sub.add_parser("transcribe", help="用 faster-whisper 转写本地视频")
    transcribe.add_argument("video")
    transcribe.add_argument("--out", default="")
    transcribe.add_argument("--model", default="small", help="tiny/base/small/medium/large-v3")
    transcribe.add_argument("--language", default="zh")
    transcribe.add_argument("--max-minutes", type=float, default=90)
    transcribe.add_argument("--no-vad", dest="vad", action="store_false", help="关闭 VAD；纯音乐/演唱想保留歌词时用")
    transcribe.set_defaults(func=cmd_transcribe)

    watch = sub.add_parser("watch", help="一键：下载视频 + 抽帧 + 字幕/转写 + 评论")
    watch.add_argument("target")
    watch.add_argument("--video", default="", help="跳过下载，直接分析这个本地视频（评论仍按 target）")
    watch.add_argument("--page", type=int, default=0)
    watch.add_argument("--quality", default="auto")
    watch.add_argument("--limit", type=int, default=100)
    watch.add_argument("--max-sub", type=int, default=100)
    watch.add_argument("--max-sub-total", type=int, default=600)
    watch.add_argument("--sort", choices=["hot", "time", "reply"], default="hot")
    watch.add_argument("--delay", type=float, default=1.5)
    watch.add_argument("--no-subs", action="store_true")
    watch.add_argument("--no-comments", action="store_true")
    watch.add_argument("--no-transcribe", action="store_true")
    watch.add_argument("--force-transcribe", action="store_true", help="有字幕也再跑一遍转写")
    watch.add_argument("--no-scene", action="store_true")
    watch.add_argument("--danmaku", action="store_true")
    watch.add_argument("--no-subtitle", action="store_true")
    watch.add_argument("--even", type=int, default=12)
    watch.add_argument("--interval", type=float, default=0, help="每 N 秒一帧；全量模式用 5，配合 --max-total 0")
    watch.add_argument("--scene-max", type=int, default=24)
    watch.add_argument("--scene-threshold", type=float, default=0.30)
    watch.add_argument("--max-total", type=int, default=36, help="总帧数上限；0 表示不设上限（全量模式）")
    watch.add_argument("--width", type=int, default=960)
    watch.add_argument("--model", default="small")
    watch.add_argument("--language", default="zh")
    watch.add_argument("--max-minutes", type=float, default=90)
    watch.add_argument("--no-vad", dest="vad", action="store_false", help="关闭 VAD；纯音乐/演唱想保留歌词时用")
    watch.add_argument("--out", default="")
    watch.set_defaults(func=cmd_watch)

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
    except bili_api.RiskControlError as exc:
        print(f"RISK_CONTROL: {exc}", file=sys.stderr, flush=True)
        print("已停止，不再重试。请稍后再试或让用户手动打开页面确认。", file=sys.stderr, flush=True)
        return 2
    except bili_api.ApiError as exc:
        print(f"ERROR: {exc}", file=sys.stderr, flush=True)
        return 1
    except KeyboardInterrupt:
        print("INTERRUPTED", file=sys.stderr, flush=True)
        return 130
    except Exception as exc:
        if args.debug or os.environ.get("BILIWATCH_DEBUG"):
            traceback.print_exc()
        print(f"ERROR: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
