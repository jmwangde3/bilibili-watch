# bilibili-watch

Bilibili media acquisition and evidence Skill for local coding agents. Includes metadata, `yutto` downloads, contact sheets, speech transcription, comment export, discovery, and optional dense evidence-matrix checks.

## Install / assign

Place the repository as one Skill folder with `SKILL.md` at its root, or import it through your Skill manager. Assign to projects that need downloads, media analysis, transcript/frames, or auditable collection. Ordinary video Q&A and anonymous comments use the **separately installed** `sense-now` Skill.

## Quick start

From this repository's root (PowerShell 5.1 or 7):

```powershell
# Use your existing media-enabled virtualenv's python.exe if not on PATH.
& python .\scripts\bili.py --help
& python .\scripts\bili.py doctor
```

Runtime data defaults to `%LOCALAPPDATA%\bilibili-watch`. If you previously used another data folder, set `BILIWATCH_DATA` before running to keep old cookies, models, and runs. Read [environment setup](references/setup.md) for dependencies, optional env overrides, commands, and safety notes.

## Files

- `SKILL.md` — short routing/trigger instructions.
- `scripts/bili.py` — local CLI; Python modules in `scripts/` implement downloads, auth, comments, discovery, frames and ASR.
- `scripts/coverage.py` — evidence matrix initialization and validation.
- `references/` — details only when needed.

No cookies, downloads, profiles, credentials, model weights, or generated runs are included. `sense-now` source is not bundled. This package is a refactor of the supplied local Skill; the third-party media dependencies retain their own licenses.
