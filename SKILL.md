---
name: bilibili-watch
description: Download and inspect Bilibili videos (BV/av/b23) with metadata, keyframes, subtitles/ASR, comment export, related-video discovery, and optional dense-frame evidence matrices. Use for local media processing or auditable collection; use the separate sense-now Skill for ordinary indexed video Q&A and anonymous read-only comments.
---

# Bilibili Watch

Use the **smallest workflow that answers the request**. This Skill handles local video processing and exported evidence; `sense-now` handles ordinary video indexing, Q&A, and read-only anonymous comments. Do not duplicate a successful Sense Now result.

## Start

Run from this installed Skill's root (`scripts/bili.py` relative to it). Use the existing Python venv containing required packages; no fixed user path is assumed.

```powershell
& python .\scripts\bili.py --help
& python .\scripts\bili.py doctor
```

If Python lives in a venv, substitute that `python.exe` in the commands. Set `BILIWATCH_DATA` to reuse existing downloads, model cache, and authentication state. See [setup](references/setup.md) for environment and dependencies.

## Choose the route

| Task | First action |
| --- | --- |
| Video overview / timestamped question | `sense-now` `index` → `query`; `zoom` for visual details |
| Comments / nested replies, read-only | `sense-now` `comments`; inspect `incomplete` and counts |
| Video metadata | `bili.py info BV...` |
| Download only | `bili.py download BV... --out <directory>` |
| Local keyframes / contact sheets | `bili.py frames <video>` |
| Missing speech/subtitles | `bili.py transcribe <video> --model small --language zh` |
| Export `comments.md/json` or explore candidates | `bili.py comments` / `explore`; check login needs first |
| Bundle video, frames, transcript, comments | `bili.py watch BV...` (select flags to skip unused work) |
| Full-coverage matrix / dense evidence | `coverage.py`; read [full coverage](references/full-coverage.md) |

If Sense Now reports no subtitle path and cannot retrieve dialogue, **do not retry `query` repeatedly**: download once and transcribe locally. Do not download/ASR merely to answer a question already resolved by the index.

## Evidence and limits

- Inspect text/subtitle segments first; inspect frames or `zoom` only for required visual details. Cite video timestamps and distinguish observation from inference.
- For comments, report main and nested replies **fetched versus available**, plus `incomplete` and any access limits. Do not generalize from a partial sample.
- For discovery, inspect `discovery.md` and start with a few candidates, not bulk downloads. See [discovery](references/discovery.md).
- On `LOGIN_REQUIRED`, prefer available anonymous Sense Now paths; for Python authenticated paths, ask the user to sign in themselves using `bili.py auth`. Never request passwords or print cookies.
- On `RISK_CONTROL`, stop; do not cycle accounts, proxies, or retries to evade controls. Respect copyright, privacy, and permitted use.
- A full-coverage claim must have a **frozen BV scope** and a checked evidence matrix; "all videos" without scope is not verifiable. Do not claim zero omissions.

## Details on demand

- [Local setup, commands, outputs, troubleshooting](references/setup.md)
- [Discovery and scoring](references/discovery.md)
- [Bilibili comments and API caveats](references/api-comments.md)
- [Dense-frame collection and evidence matrix](references/full-coverage.md)
