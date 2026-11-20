# 精华内容挖掘：接口、评分与流程

目标不是“搜到播放最高的”，而是从**评论区/楼中楼**和**相关视频图谱**里找出被观众反复推荐、标题却可能很朴素的视频。实现：`scripts/bili_discovery.py` + `bili.py explore`。

## 接口

### 关键词搜索（WBI）

```text
GET https://api.bilibili.com/x/web-interface/wbi/search/type
```

| 参数 | 说明 |
| --- | --- |
| `search_type=video` | 只搜视频 |
| `keyword` | 关键词 |
| `page` / `page_size` | 页码 / 每页条数（用 20） |
| `order` | `totalrank`、`click`、`pubdate`、`dm`、`stow` |

需要 WBI 签名（见 `api-comments.md`）。返回 `data.result[]`，常用字段：`bvid`、`aid`、`title`（含 `<em>` 高亮）、`author`、`play`、`review`、`duration`、`description`、`pubdate`。`data.numResults` 是总数。

2026-11-02 实测：搜“影之刃零”`code=0`、`numResults=1000`、每页返回正常。

### 相关视频（右侧推荐）

```text
GET https://api.bilibili.com/x/web-interface/archive/related?bvid=<BV>
```

匿名可用。返回 `data[]`，实测一次 40 条，字段：`bvid`、`aid`、`title`、`owner.name`、`stat.view/reply/like`、`duration`、`pubdate`、`desc`。这是“右侧相关视频”的可靠入口，不需要抓 HTML。

### 评论里的视频引用

不另调接口：从主评论和楼中楼的 `content.message` 里正则抽取：

- `BV[0-9A-Za-z]{10}`
- `(?<![A-Za-z0-9])av(\d{1,12})(?![A-Za-z0-9])`
- `(?:https?://)?b23\.tv/[A-Za-z0-9]+`（用 `parse_target` 解析短链）

每条证据保留：来源视频、评论 rpid、作者、点赞、是否楼中楼、原文。去重键：`(source_bvid, comment_rpid, target_bv)`。

## 挖掘流程

```text
关键词 ──搜索──> 候选池 + 取前 N 个种子
种子 BV ─────────> 种子
                         │
                         ├─ 抓主评论 + 楼中楼（默认 60 主 / 单楼 20 / 总 80）
                         │     └─ 抽 BV/av/b23 ──> 评论候选（带证据）
                         │
                         └─ /archive/related（默认前 5 个种子）
                               └─ 相关候选（记录共现种子）
                                         │
评论候选补元数据（默认前 20 个）──────────┘
                                         │
                                     去重/合并/打分
                                         │
                          discovery.md + discovery.json
```

默认参数（`explore --help` 可改）：

- 搜索：`--pages 1`、`--search-limit 20`
- 抓评论的种子：`--seed-limit 3`
- 抓相关的种子：`--related-limit 5`
- 每个种子评论：`--comment-limit 60`、`--max-sub 20`、`--max-sub-total 80`
- 补元数据的评论候选：`--candidate-limit 20`
- 输出：`--top 20`
- 间隔：`--delay 1.2`

`explore` 的评论是**采样**，不是全量；它的任务是“发现候选”。确定候选后，用 `comments` / `watch` 对单个视频深挖。

要搜两页时，`--pages 2` 和 `--search-limit 40` 要一起给：`search-limit` 会先截断，只加 `--pages` 不会多出候选。

## 评分口径

`score` 是可解释的加和，不是平台官方排序：

| 项 | 规则 | 目的 |
| --- | --- | --- |
| 评论提及 | 每条 +6；按不同来源视频再 +8 | 多人、跨视频重复推荐优先 |
| 评论点赞 | `min(15, 2+log10(1+赞))`；楼中楼 ×1.5 | 高赞楼中楼权重最高 |
| 相关共现 | `min(24, 8 × 相关种子数)` | 多个种子都相关，说明是同一圈层 |
| 搜索排名 | 第 1 名 +12，逐名递减 | 搜索只作入口，不压倒评论证据 |
| 互动 | 播放/评论/点赞的对数项，合计 ≤25 | 避免纯标题党；不按播放直接排序 |
| 评论率 | `min(8, 评论/播放 × 200)` | 评论密度高的内容更可能有讨论价值 |
| 扣分 | 广告/带货/搬运/直播回放/切片 -15；<30 秒 -5；3h+ -10；6h+ -20 | 去掉明显低信号内容 |

权重刻意让“一条高赞楼中楼推荐”能压过“40 条相关视频里的普通一条”。实测：种子 `BV1GJ411x7h7` 的楼中楼里有人用 633 赞推荐 `BV17x411w7KC`，该候选以 40.9 分排第一，高于所有相关视频候选（26–28 分）。

## 输出

`discovery.md`：

- 种子列表
- 候选表：`# / BV / 标题 / UP / 时长 / 播放 / 评论 / 来源 / 分数`
- 前 10 明细：来源、分数拆解、最多 5 条评论原文证据（标明主评论/楼中楼、点赞、来源 BV）

`discovery.json`：完整候选、`sources`、`evidence`、`score_parts`，可供后续二次筛选。

## 使用建议

- 关键词搜索命中多但杂时，先看 `discovery.md` 的“评论×N(楼M)”行，再看相关共现。
- 标题很朴素、无配音/无字幕的候选不要直接跳过：先 `watch` 抽帧 + 读评论，画面和评论往往比标题信息多。
- 多 P 视频的 `duration` 是总时长，`watch` 默认只下第 1 P；要整组另说。
- 当前没有内置 OCR。无音轨/无字幕且画面有大量文字时，只能抽帧人看；不要凭空总结。
- 搜索/相关/评论都受风控：遇到 `RISK_CONTROL` 停止，不换号、不换 IP、不重试。

## 实测记录（2026-11-02）

- 种子 `BV1GJ411x7h7`：相关 40 条；评论 20 条；抓到 1 条楼中楼引用（633 赞，指向 `BV17x411w7KC`）。
- 关键词“影之刃零”：搜索 5 条（总数 1000）、相关 40 条、候选 42 条；输出表格和 JSON 正常。
- 纯相关候选与评论推荐候选能正确合并来源；重复视频只保留一条。