# Local setup, workflows, and outputs

## Runtime configuration (Windows PowerShell 5.1 / 7)

From the installed `bilibili-watch` directory use the **Python interpreter that has your media packages**. This may be a dedicated venv; do not install packages into unrelated project environments.

```powershell
# Optional: reuse an existing data root; set for the current session.
$env:BILIWATCH_DATA = 'D:\bilibili-watch-data'

# Optional: if yutto/ffmpeg aren't on PATH, provide their paths.
# $env:BILIWATCH_YUTTO = 'D:\venvs\bili\Scripts\yutto.exe'
# $env:BILIWATCH_FFMPEG = 'D:\bin\ffmpeg.exe'
# $env:BILIWATCH_FFPROBE = 'D:\bin\ffprobe.exe'
# $env:BILIWATCH_CHROME = 'C:\Program Files\Google\Chrome\Application\chrome.exe'

$py = 'python'  # replace with an existing venv\Scripts\python.exe if needed
& $py .\scripts\bili.py doctor
```

`BILIWATCH_DATA` defaults to `%LOCALAPPDATA%\bilibili-watch` on Windows. **For existing installations, set it to your old data root first** to keep cookies, models, and past runs accessible; the scripts do not migrate old data automatically. `yutto` resolves from `BILIWATCH_YUTTO`, then next to the selected Python interpreter, then PATH. `ffmpeg` and `ffprobe` resolve from their environment variables or PATH. `doctor` checks download prerequisites; basic metadata commands may work without all media tools.

Dependencies are task-specific: `yutto`, `ffmpeg`, `ffprobe` for downloads; `Pillow` for contact sheets; `faster-whisper` and a local model for ASR; `websockets` plus an interactive Chrome/Edge browser for login. When rebuilding an environment, check the installed PyAV/faster-whisper versions rather than blindly upgrading. The older faster-whisper 1.2.1 setup observed `metadata_errors` with PyAV 19; `av==17.1.0` was its known-good workaround, not a permanent universal requirement.

## Common commands

All commands below run from the Skill root. Invoke them with `& $py` in PowerShell:

```powershell
& $py .\scripts\bili.py info BV1xx411c7mD
& $py .\scripts\bili.py download BV1xx411c7mD --out 'D:\bilibili-watch-data\runs\demo'
& $py .\scripts\bili.py frames 'D:\video.mp4'
& $py .\scripts\bili.py transcribe 'D:\video.mp4' --model small --language zh
& $py .\scripts\bili.py comments BV1xx411c7mD --limit 30 --max-sub 10 --max-sub-total 100
& $py .\scripts\bili.py explore '主题关键词' --top 20
& $py .\scripts\bili.py watch BV1xx411c7mD --no-comments

# Authenticated Python comment/export/discovery paths, only when needed:
& $py .\scripts\bili.py auth --check
& $py .\scripts\bili.py auth
```

The standalone `sense-now` Skill provides its own CLI (`index`, `job_status`, `query`, `zoom`, `comments`) and guidance. Do not invoke the old in-directory compatibility shim: it is intentionally removed to avoid duplicate entry points.

## Outputs

By default, generated data goes under `BILIWATCH_DATA\runs` (unless `--out` is supplied):

- `watch`: `meta.json`, `video/`, `frames/` (`sheet_*.jpg`, `frames.json`), `transcript/` (`transcript.txt`, `.srt` or `transcript_from_subtitle.txt`), `comments/` (`comments.md/json`), `run.json`.
- `explore`: `discovery.md` and `discovery.json`, with source evidence and ranking explanations.
- `comments`: `comments.md` and `comments.json`, with main/nested reply counts and gaps.

Before reading large media/output files, inspect the small metadata and manifests. Prefer targeted time segments, frames, and relevant comments over full dumps.

## Login and safety

- `auth` opens a **dedicated Chrome profile**. Only the user may type credentials, scan codes, or approve account prompts.
- Local `auth/cookies.json` and `auth/yutto-auth.toml` are sensitive; never commit them, print them, or put them in a report.
- The login flow uses a localhost Chrome debugging port; never expose it over LAN or attach it to a normal personal browser profile.
- Python's legacy comment API may return only a handful of hot comments anonymously; prefer Sense Now's WBI comments for ordinary read-only work. Count completeness explicitly.
- Stop on risk-control responses. Do not evade platform limits or bulk redistribute copyrighted videos.

## Troubleshooting

| Symptom | Action |
| --- | --- |
| `DOCTOR=missing_critical` | Check `BILIWATCH_YUTTO` / `BILIWATCH_FFMPEG` / `BILIWATCH_FFPROBE` or PATH |
| `FASTER_WHISPER=MISSING` / `WHISPER_MODELS=0` | Use the installed venv and local model only for ASR work |
| Anonymous Python comments only show a few entries | Prefer Sense Now `comments` or sign in for Python exports |
| `LOGIN_REQUIRED` / `AUTH=expired` | Check state, then ask user to use the dedicated login window |
| `RISK_CONTROL` | Stop; do not keep retrying |
| No transcript / no subtitle | `download`, then local `transcribe`; inspect audio presence |
| Songs produce empty ASR | Try `--no-vad` only if needed; lyrics may still be wrong |
| Many frames | Start from contact sheets, then inspect only relevant full-resolution frames |