# 全量模式：冻结范围 + 覆盖矩阵

用途：一次性把一组 B 站视频里的装备/能力证据收完，先让缺口可见，再出成品。适用于 FFRS 的 EQUIPMENT & ABILITY 画面取证，也可换成其他主题。

## 一键指令

```text
全量模式：先冻结 BVID 清单，每个视频每 5 秒抽帧，EQUIPMENT & ABILITY 画面全部逐张读；先给 25 玩法 × 角色的武器/防具/饰品覆盖矩阵，缺的标“无画面/推”；矩阵确认后再生成 xlsx，中途不要出新版。
```

## 1. 冻结范围

先把 BVID 清单写下来，再开始抽帧：

- 来源核对里的所有 BVID；
- 每个 BVID 的相关推荐 1 跳（`explore` 的 `related`）；
- 关键词搜索前 20 个（如果这次是搜出来的）；
- 冻结后新增 BVID 必须用户同意，不能边做边扩。

冻结清单写进 `spec.json` 的 `scope`。可以用 `explore` 先生成候选，再人工挑出要冻结的 BVID：

```bash
"$PY" "$BILI" explore "主题关键词" --top 50
```

## 2. spec.json

`coverage.py` 只认这个结构：

```json
{
  "name": "FFRS 全量覆盖",
  "scope": ["BV1xxxxxxxxx", "BV1yyyyyyyyy"],
  "rows": ["玩法01", "玩法02", "玩法03"],
  "columns": [
    "角色A · 主幻兵",
    "角色A · 武器",
    "角色A · 防具",
    "角色A · 饰品",
    "角色A · 来源",
    "角色B · 主幻兵",
    "角色B · 武器",
    "角色B · 防具",
    "角色B · 饰品",
    "角色B · 来源"
  ],
  "allowed": ["视频", "推", "无画面"]
}
```

- `rows`：这次要覆盖的条目；FFRS 就是 25 条玩法。
- `columns`：角色 × 字段；每个角色重复“主幻兵/武器/防具/饰品/来源”五列。
- `scope`：冻结的 BVID；checker 会要求每个 BVID 在 `evidence.csv` 至少出现一次。
- `allowed`：默认“视频/推/无画面”，不要改成空值或“待定”。

## 3. 抽帧

每 3–5 秒一帧，场景帧保留，不设上限：

```bash
"$PY" "$BILI" frames "D:/path/video.mp4" --interval 5 --max-total 0 --width 1280
```

- `--interval 5`：00:00、00:05、00:10……每 5 秒一帧。
- `--max-total 0`：不截断。长视频会产生很多帧，先看九宫格，再对含装备/能力的画面逐张 `Read`。
- 场景帧（`scene`）仍会加进来；如果只要固定间隔，把 `--scene-max 0`。
- 抽帧产物在 `frames/`，清单是 `frames.json`；单帧文件名带时间。

## 4. evidence.csv

每读到一条有效画面就记一行：

| 列 | 必填 | 说明 |
| --- | --- | --- |
| `bvid` | 是 | 来源视频 |
| `time` | 是 | 画面时间，`mm:ss` 或 `hh:mm:ss` |
| `character` | 是 | 角色/对象 |
| `conclusion` | 是 | `视频` / `推` / `无画面` |
| `frame` | 标“视频”时必填 | 单帧路径 |
| `note` | 否 | 装备名、来源、疑点 |

规则：

- 标 `视频` 的行必须有 `frame` 路径；只有文字描述不算视频证据。
- 标 `推` 的行表示只能从上下文推断，不能当画面证据。
- 标 `无画面` 的行表示查过该视频但没有对应画面；不要留空。
- 同一个 BVID 可以有多行；scope 里的每个 BVID 至少要有一行（哪怕结论是“无画面”）。

## 5. 覆盖矩阵

```bash
COVER="scripts/coverage.py"  # 从本 Skill 根目录运行
"$PY" "$COVER" init --spec spec.json --out coverage.csv --evidence-template evidence.csv
```

初始化后 CSV 是空矩阵；逐格填 `视频` / `推` / `无画面`，**不允许空白**。

检查：

```bash
"$PY" "$COVER" check --spec spec.json --matrix coverage.csv --evidence evidence.csv
```

检查器会报：

- 矩阵缺行/多行、缺列/多列、表头顺序不对；
- 空白格、非法值；
- 标“视频”但没有 frame 路径；
- 冻结 scope 中哪些 BVID 没有 evidence 行；
- 结果 `RESULT=PASS/FAIL`，有缺口退出码 1，报告写到 `coverage_report.md`。

### 5.1 已有 7 列 Markdown 矩阵（当前 FFRS 格式）

项目里已经有一份 7 列矩阵时，不要为了检查去转 CSV；直接跑 `check-md`：

```bash
"$PY" "$COVER" check-md \
  --matrix coverage_matrix_20261102.md \
  --expected-sections 25 \
  --require-time \
  --out coverage_md_report.md
```

它直接读：

```text
角色 | 主幻兵 | 武器 | 防具 | 饰品 | 状态 | 证据/备注
```

检查内容：

- `### N.` 编号玩法数量（FFRS 用 `--expected-sections 25`）；
- 每张表必须是 7 列标准表头；数字编号玩法不能没有表；
- 单元格不能空白（`—` 是显式无数据，不算空白）；
- 状态必须在允许集合（`视频`、`推`、`视频/推`、`无画面`），允许“基础状态（限定）”写法，如 `推（待核）`、`视频（待并入）`、`推（另一版视频）`；
- 视频证据行（`视频` / `视频/推` / `视频（待并入）`）必须在“证据/备注”列有 BVID；
- 加 `--require-time` 时还必须有 `mm:ss` 或 `hh:mm:ss`；
- 输出缺 BVID、缺时间、无确认视频证据玩法、表格 BVID vs 全文 BVID；
- 可选 `--scope <JSON/text/BVID列表>` 对比冻结范围与表格证据列，列出缺失/额外 BVID；
- 只写报告，不改矩阵；有缺口 `RESULT=FAIL`、退出码 1。


## 6. 先矩阵后出表

流程硬门禁：

1. 先交矩阵 + 缺口清单（`coverage_report.md`）。
2. 用户确认矩阵口径/缺口处理方式。
3. 才生成 xlsx；xlsx 可以用普通 Excel 工具生成；需要复杂结构或布局验证时使用独立的 `xlsx-verify` Skill。
4. **中途不出新版**：确认前只维护当前矩阵，不生成 v2/v3 成品；用户要求改口径时再改。

## 7. 验收清单

- [ ] `spec.scope` 就是冻结清单，和用户确认过，没有偷偷加 BVID。
- [ ] 每个 BVID 至少有 1 行 evidence（含“无画面”）。
- [ ] 矩阵每格非空，且值只在 `allowed` 里。
- [ ] 每个“视频”格都有 BVID + 时间 + frame 路径。
- [ ] `coverage.py check` 返回 PASS。
- [ ] 矩阵和缺口清单先给用户看过。
- [ ] xlsx 只在确认后生成，且没有中途版本。

## 8. 边界

- 不能保证绝对零遗漏；“所有视频”本身没有边界。冻结范围 + 矩阵只能保证**漏了能看见**。
- 当前没有内置 OCR；画面文字靠 `Read` 逐张看。无音轨/无字幕不影响画面取证，但不要补写画面里没有的结论。
- 全量抽帧会产生大量图片；先看九宫格定位，再按 `EQUIPMENT & ABILITY` 逐张读，不要只读九宫格就下结论。
- 相关视频 1 跳、搜索前 20 是默认冻结口径；换口径要在 spec/报告里写清楚，不要口头改。
