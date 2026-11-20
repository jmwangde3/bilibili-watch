from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from bili_common import MODELS_DIR, ffmpeg_bin, ffprobe_bin, format_duration


def _run(cmd: list[str], timeout: int = 3600) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "")[-1500:]
        raise RuntimeError(f"命令失败（{proc.returncode}）：{' '.join(cmd[:3])}\n{tail}")
    return proc


def probe_media(video: str | Path) -> dict:
    ffprobe = ffprobe_bin()
    if not ffprobe:
        raise RuntimeError("找不到 ffprobe，请将 ffprobe 加入 PATH 或设置 BILIWATCH_FFPROBE")
    proc = _run(
        [
            ffprobe,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(video),
        ],
        timeout=120,
    )
    doc = json.loads(proc.stdout or "{}")
    fmt = doc.get("format") or {}
    streams = doc.get("streams") or []
    video_stream = next((item for item in streams if item.get("codec_type") == "video"), None)
    audio_stream = next((item for item in streams if item.get("codec_type") == "audio"), None)
    duration = float(fmt.get("duration") or (video_stream or {}).get("duration") or 0)
    return {
        "path": str(video),
        "duration": duration,
        "duration_text": format_duration(duration),
        "width": int((video_stream or {}).get("width") or 0),
        "height": int((video_stream or {}).get("height") or 0),
        "video_codec": (video_stream or {}).get("codec_name"),
        "audio_codec": (audio_stream or {}).get("codec_name"),
        "has_audio": audio_stream is not None,
        "has_video": video_stream is not None,
        "size": int(fmt.get("size") or 0),
    }


def _even_times(duration: float, count: int) -> list[float]:
    if duration <= 0 or count <= 0:
        return []
    return [max(0.0, min(duration - 0.05, (index + 0.5) * duration / count)) for index in range(count)]


def _interval_times(duration: float, interval: float) -> list[float]:
    if duration <= 0 or interval <= 0:
        return []
    count = int(duration // interval) + 1
    return [max(0.0, min(duration - 0.05, index * interval)) for index in range(count)]


def _scene_times(video: str | Path, threshold: float, max_count: int) -> list[float]:
    ffmpeg = ffmpeg_bin()
    if not ffmpeg or max_count <= 0:
        return []
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-nostats",
        "-loglevel",
        "info",
        "-i",
        str(video),
        "-an",
        "-vf",
        f"scale=320:-2,select='gt(scene,{threshold})',showinfo",
        "-f",
        "null",
        "-",
    ]
    try:
        proc = _run(cmd, timeout=1800)
    except Exception:
        return []
    found = [float(value) for value in re.findall(r"pts_time:\s*([0-9]+(?:\.[0-9]+)?)", proc.stderr or "")]
    if len(found) > max_count:
        step = len(found) / max_count
        found = [found[min(len(found) - 1, int(index * step))] for index in range(max_count)]
    return found


def _extract_single(video: str | Path, timestamp: float, out_path: Path, width: int) -> None:
    ffmpeg = ffmpeg_bin()
    if not ffmpeg:
        raise RuntimeError("找不到 ffmpeg，请将 ffmpeg 加入 PATH 或设置 BILIWATCH_FFMPEG")
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{timestamp:.3f}",
        "-i",
        str(video),
        "-frames:v",
        "1",
        "-vf",
        f"scale={width}:-2",
        "-q:v",
        "3",
        "-y",
        str(out_path),
    ]
    _run(cmd, timeout=300)
    if not out_path.exists() or out_path.stat().st_size == 0:
        raise RuntimeError(f"抽帧失败：{out_path.name}")


def _font(size: int):
    from PIL import ImageFont

    for candidate in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\arial.ttf"):
        if Path(candidate).exists():
            try:
                return ImageFont.truetype(candidate, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _make_sheets(frames: list[dict], out_dir: Path, cell_w: int = 480, cell_h: int = 270) -> list[str]:
    from PIL import Image, ImageDraw, ImageOps

    if not frames:
        return []
    label_h = 30
    cols, rows = 3, 3
    per_sheet = cols * rows
    font = _font(17)
    sheets = []
    for sheet_index in range(0, len(frames), per_sheet):
        chunk = frames[sheet_index : sheet_index + per_sheet]
        sheet = Image.new("RGB", (cols * cell_w, rows * (cell_h + label_h)), (18, 18, 18))
        draw = ImageDraw.Draw(sheet)
        for index, frame in enumerate(chunk):
            row, col = divmod(index, cols)
            x = col * cell_w
            y = row * (cell_h + label_h)
            try:
                image = Image.open(frame["path"]).convert("RGB")
                image = ImageOps.fit(image, (cell_w, cell_h), method=Image.Resampling.LANCZOS)
                sheet.paste(image, (x, y))
            except Exception:
                draw.rectangle([x, y, x + cell_w, y + cell_h], fill=(40, 40, 40))
            draw.rectangle([x, y + cell_h, x + cell_w, y + cell_h + label_h], fill=(0, 0, 0))
            label = f"#{frame['index']:02d}  {frame['time_text']}  {frame['kind']}"
            draw.text((x + 8, y + cell_h + 5), label, fill=(255, 255, 255), font=font)
        sheet_path = out_dir / f"sheet_{sheet_index // per_sheet + 1:02d}.jpg"
        sheet.save(sheet_path, quality=88)
        sheets.append(str(sheet_path))
    return sheets


def extract_frames(
    video: str | Path,
    out_dir: str | Path,
    even: int = 12,
    interval: float = 0.0,
    scene_threshold: float = 0.30,
    scene_max: int = 24,
    max_total: int = 36,
    width: int = 960,
) -> dict:
    info = probe_media(video)
    if not info["has_video"]:
        raise RuntimeError("文件里没有视频流")
    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)

    if interval and interval > 0:
        base_times = [(value, "interval") for value in _interval_times(info["duration"], interval)]
    else:
        base_times = [(value, "even") for value in _even_times(info["duration"], even)]
    scene_times = [(value, "scene") for value in _scene_times(video, scene_threshold, scene_max)]

    merged: list[tuple[float, str]] = []
    for timestamp, kind in sorted(base_times + scene_times, key=lambda item: item[0]):
        if timestamp < 0 or timestamp >= max(0.0, info["duration"] - 0.05):
            continue
        if any(abs(timestamp - other[0]) < 0.8 for other in merged):
            continue
        merged.append((timestamp, kind))
    if max_total and len(merged) > max_total:
        merged = merged[:max_total]

    frames = []
    for index, (timestamp, kind) in enumerate(merged, 1):
        time_text = format_duration(timestamp)
        filename = f"f_{index:03d}_{time_text.replace(':', '-')}.jpg"
        path = directory / filename
        _extract_single(video, timestamp, path, width)
        frames.append(
            {
                "index": index,
                "path": str(path),
                "time": round(timestamp, 3),
                "time_text": time_text,
                "kind": kind,
            }
        )

    sheets = _make_sheets(frames, directory)
    manifest = {
        "video": str(video),
        "probe": info,
        "frames": frames,
        "sheets": sheets,
        "scene_threshold": scene_threshold,
        "even_count": even,
        "interval": interval,
        "sampling": "interval" if interval and interval > 0 else "even",
    }
    manifest_path = directory / "frames.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def extract_audio(video: str | Path, out_path: str | Path, max_seconds: float | None = None) -> str:
    ffmpeg = ffmpeg_bin()
    if not ffmpeg:
        raise RuntimeError("找不到 ffmpeg")
    info = probe_media(video)
    if not info["has_audio"]:
        raise RuntimeError("视频没有音轨，无法转写")
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000"]
    if max_seconds:
        cmd += ["-t", f"{max_seconds:.1f}"]
    cmd += ["-c:a", "pcm_s16le", "-y", str(path)]
    _run(cmd, timeout=1800)
    return str(path)


def _srt_time(seconds: float) -> str:
    milliseconds = int(round(seconds * 1000))
    hours, rem = divmod(milliseconds, 3600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def transcribe(
    video: str | Path,
    out_dir: str | Path,
    model_name: str = "small",
    language: str = "zh",
    max_minutes: float = 90,
    vad: bool = True,
    progress=None,
) -> dict:
    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

    info = probe_media(video)
    audio_duration = info["duration"]
    limit_seconds = None
    if max_minutes and audio_duration > max_minutes * 60:
        limit_seconds = max_minutes * 60
        if progress:
            progress(f"视频 {info['duration_text']} 超过 {max_minutes} 分钟，只转写前 {max_minutes:g} 分钟")

    audio_path = directory / "audio.wav"
    if progress:
        progress("提取音轨（16k mono wav）…")
    extract_audio(video, audio_path, limit_seconds)

    if progress:
        progress(f"加载 faster-whisper 模型：{model_name}（CPU int8）…")
    from faster_whisper import WhisperModel

    model = WhisperModel(
        model_name,
        device="cpu",
        compute_type="int8",
        download_root=str(MODELS_DIR),
        cpu_threads=0,
    )
    segments, transcribe_info = model.transcribe(
        str(audio_path),
        language=language or None,
        beam_size=5,
        vad_filter=vad,
        vad_parameters={"min_silence_duration_ms": 500},
        condition_on_previous_text=False,
    )

    rows = []
    srt_lines = []
    txt_lines = []
    for index, segment in enumerate(segments, 1):
        text = (segment.text or "").strip()
        if not text:
            continue
        start = float(segment.start or 0)
        end = float(segment.end or start)
        rows.append({"start": start, "end": end, "text": text})
        txt_lines.append(f"[{format_duration(start)}] {text}")
        srt_lines += [str(len(rows)), f"{_srt_time(start)} --> {_srt_time(end)}", text, ""]
        if progress and index % 10 == 0:
            progress(f"转写中：{format_duration(start)} / {format_duration(transcribe_info.duration or audio_duration)}")

    txt_path = directory / "transcript.txt"
    srt_path = directory / "transcript.srt"
    json_path = directory / "transcript.json"
    txt_path.write_text("\n".join(txt_lines) + "\n", encoding="utf-8")
    srt_path.write_text("\n".join(srt_lines), encoding="utf-8")
    json_path.write_text(
        json.dumps(
            {
                "video": str(video),
                "model": model_name,
                "language": language,
                "duration": transcribe_info.duration,
                "segments": rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    try:
        audio_path.unlink()
    except OSError:
        pass
    return {
        "txt": str(txt_path),
        "srt": str(srt_path),
        "json": str(json_path),
        "segments": len(rows),
        "duration": transcribe_info.duration,
    }


def _parse_srt(text: str) -> list[dict]:
    rows = []
    blocks = re.split(r"\n\s*\n", text.replace("\r\n", "\n"))
    for block in blocks:
        lines = [line for line in block.split("\n") if line.strip()]
        if len(lines) < 3:
            continue
        match = re.search(r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})", lines[1])
        if not match:
            continue
        start = int(match.group(1)) * 3600 + int(match.group(2)) * 60 + int(match.group(3)) + int(match.group(4)) / 1000
        content = re.sub(r"<[^>]+>", "", " ".join(lines[2:])).strip()
        if content:
            rows.append({"start": start, "text": content})
    return rows


def _parse_ass(text: str) -> list[dict]:
    rows = []
    for line in text.splitlines():
        if not line.startswith("Dialogue:"):
            continue
        parts = line.split(",", 9)
        if len(parts) < 10:
            continue
        stamp = parts[1].strip()
        match = re.match(r"(\d+):(\d{2}):(\d{2})\.(\d{1,2})", stamp)
        if not match:
            continue
        start = int(match.group(1)) * 3600 + int(match.group(2)) * 60 + int(match.group(3)) + int(match.group(4)) / 100
        content = re.sub(r"\{[^}]*\}", "", parts[9]).strip()
        if content:
            rows.append({"start": start, "text": content})
    return rows


def _read_text_file(path: Path) -> str:
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig", errors="replace")
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", errors="replace")
    if raw.count(b"\x00") > max(4, len(raw) // 10):
        try:
            return raw.decode("utf-16", errors="replace")
        except Exception:
            pass
    for encoding in ("utf-8", "gb18030", "big5"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _subtitle_rank(name: str, text: str) -> int:
    lower = name.lower()
    score = 0
    if any(key in lower for key in ("中文（简", "中文(简", "简体", "zh-hans", "zh-cn", "chs")):
        score += 120
    elif any(key in lower for key in ("中文", "chinese", "zh-han", "zh-tw", "繁體", "繁体")):
        score += 110
    elif any(key in lower for key in ("english", "en-us", "en-gb", ".en.")):
        score += 60
    elif any(key in lower for key in ("日本語", "japanese", "한국어", "korean")):
        score += 30
    else:
        score += 10
    if "手语" in lower or "asl" in lower:
        score -= 80
    if name.lower().endswith(".srt"):
        score += 3

    sample = text[:8000]
    arabic = sum(1 for char in sample if "\u0600" <= char <= "\u06ff" or "\u0750" <= char <= "\u077f")
    hebrew = sum(1 for char in sample if "\u0590" <= char <= "\u05ff")
    cjk = sum(1 for char in sample if "\u4e00" <= char <= "\u9fff")
    latin = sum(1 for char in sample if "a" <= char.lower() <= "z")
    letters = arabic + hebrew + cjk + latin
    if letters:
        expects_arabic_hebrew = any(
            key in lower for key in ("阿拉伯", "arabic", "עברית", "hebrew")
        )
        if not expects_arabic_hebrew and (arabic + hebrew) / letters > 0.2:
            score -= 200
        is_chinese = any(key in lower for key in ("中文", "chinese", "zh-hans", "zh-cn"))
        if is_chinese and cjk / letters < 0.05:
            score -= 120
    return score


def _bili_subtitle_json(path: Path) -> list[dict] | None:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    body = doc.get("body") if isinstance(doc, dict) else None
    if not isinstance(body, list):
        return None
    rows = []
    for item in body:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "").strip()
        if content:
            rows.append({"start": float(item.get("from") or 0), "text": content})
    return rows or None


def collect_subtitles(
    video_dir: str | Path, out_dir: str | Path, video_stem: str | None = None
) -> dict | None:
    directory = Path(video_dir)
    output = Path(out_dir)
    output.mkdir(parents=True, exist_ok=True)
    all_candidates = sorted(
        [path for path in directory.rglob("*") if path.suffix.lower() in {".srt", ".ass"} and path.stat().st_size > 0]
    )
    candidates = all_candidates
    if video_stem:
        matched = [path for path in all_candidates if path.name.startswith(video_stem)]
        if matched:
            candidates = matched
    ranked = []
    for path in candidates:
        try:
            text = _read_text_file(path)
        except Exception:
            continue
        parsed = _parse_srt(text) if path.suffix.lower() == ".srt" else _parse_ass(text)
        if not parsed:
            continue
        ranked.append({"path": path, "rows": parsed, "rank": _subtitle_rank(path.name, text)})
    ranked.sort(key=lambda item: (item["rank"], len(item["rows"])), reverse=True)

    chosen = ranked[0] if ranked and ranked[0]["rank"] >= 0 else None
    if chosen is None:
        for path in directory.rglob("*.json"):
            if path.stat().st_size < 200:
                continue
            parsed = _bili_subtitle_json(path)
            if parsed:
                chosen = {"path": path, "rows": parsed, "rank": 50}
                break
    if chosen is None:
        return None

    rows = list(chosen["rows"])
    source = str(chosen["path"])
    rows.sort(key=lambda item: item["start"])
    txt_path = output / "transcript_from_subtitle.txt"
    lines = [f"[{format_duration(item['start'])}] {item['text']}" for item in rows]
    txt_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    available = [
        {
            "file": item["path"].name,
            "rank": item["rank"],
            "segments": len(item["rows"]),
            "selected": item is chosen,
        }
        for item in ranked[:15]
    ]
    json_path = output / "transcript_from_subtitle.json"
    json_path.write_text(
        json.dumps(
            {"source": source, "rank": chosen["rank"], "segments": rows, "available": available},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "source": source,
        "txt": str(txt_path),
        "json": str(json_path),
        "segments": len(rows),
        "rank": chosen["rank"],
        "available": available,
    }
