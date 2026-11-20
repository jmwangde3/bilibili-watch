# Changes

## Optimized local Skill

- Shortened `SKILL.md`, with task-specific guides in `references/setup.md` and existing references.
- Routed basic indexed video Q&A and anonymous comments to the independent `sense-now` Skill; removed an obsolete local forwarding shim.
- Made media data root / Python / yutto / ffmpeg paths configurable instead of using hard-coded user machine paths.
- Removed outdated one-off FFRS coverage results and the retired `xlsx-review-edit` reference.
- Scoped the dedicated Chrome debugger explicitly to localhost.
- Preserved the CLI subcommands and coverage checker; no behavioral claim about authenticated Bilibili access or real media downloads without a Windows runtime check.
