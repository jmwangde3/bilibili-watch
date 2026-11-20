# B 站评论区接口笔记（2026-11-02 实测）

主要依据活跃镜像 [pskdje/bilibili-API-collect](https://github.com/pskdje/bilibili-API-collect)（原 `SocialSisterYi/bilibili-API-collect` 已归档，默认分支 `deprecated`）。具体实现见 `scripts/bili_api.py`。

## 登录与匿名限制

- 鉴权：Cookie 里的 `SESSDATA`；写评论还需要 `bili_jct`，本 skill 只读，不写评论。
- **匿名只能拿 3 条热评**。`GET /x/v2/reply` 第 1 页返回 `replies` 3 条，但 `page.count` 仍是全量（实测 av170001：`count=184249`、`replies=3`），`pn=2` 返回空。
- 因此不要相信匿名的 `is_end` / `page.count` 推断“抓完了”。判断标准是 `logged_in` + 实际返回条数 + 是否还能翻页。
- 登录态由 `GET /x/web-interface/nav` 的 `data.isLogin` 确认；`-101` 表示未登录/过期。

## 主评论列表

### 翻页接口（本 skill 首选）

```text
GET https://api.bilibili.com/x/v2/reply
```

| 参数 | 说明 |
| --- | --- |
| `type` | 评论区类型，投稿视频为 `1` |
| `oid` | 目标 id；投稿视频填 `aid` |
| `pn` | 页码，从 1 开始 |
| `ps` | 每页条数，最大 20 |
| `sort` | `0` 按时间，`1` 按点赞，`2` 按回复数 |
| `nohot` | `1` 不显示热评；本 skill 用 `0` |

返回 `data.replies[]`、`data.top_replies[]`（置顶）、`data.page.count`。按点赞排序时 `replies` 本身就是热评顺序。

### WBI 懒加载接口（备用）

```text
GET https://api.bilibili.com/x/v2/reply/wbi/main
```

| 参数 | 说明 |
| --- | --- |
| `type=1`, `oid=<aid>` | 同旧接口 |
| `mode` | `3` 仅热度，`2` 仅时间，`1` 热度+时间 |
| `pagination_str` | `{"offset":""}`；下一页用 `data.cursor.pagination_reply.next_offset` |
| `plat=1`, `web_location=1315875` | 常用固定值 |

必须带 WBI 签名。签名错误返回 `-403`，风控返回 `-352`。

返回 `data.replies[]`、`data.hots[]`（仅第一页）、`data.top_replies`、`data.cursor.all_count`、`data.cursor.is_end`、`data.cursor.pagination_reply.next_offset`。

### WBI 签名

1. `GET /x/web-interface/nav` 取 `data.wbi_img.img_url` / `sub_url`，文件名去掉 `.png` 得到 `img_key` / `sub_key`。
2. 参数加 `wts=<秒级时间戳>`，按 key 排序。
3. 所有 value 去掉字符 `!'()*`，`urlencode` 得到 query。
4. `mixin_key = (img_key + sub_key)[mixin_tab][:32]`；`w_rid = md5(query + mixin_key)`。
5. 请求参数带上 `wts` 和 `w_rid`。

`mixin_tab` 是 64 个固定索引，实现见 `bili_api.py::_MIXIN_TAB`。密钥可缓存约 10 分钟；签名错误时重新取。

## 楼中楼

```text
GET https://api.bilibili.com/x/v2/reply/reply
```

| 参数 | 说明 |
| --- | --- |
| `type=1`, `oid=<aid>` | 同主评论 |
| `root` | 根评论 `rpid`（主评论的 `rpid`） |
| `pn` | 页码，从 1 |
| `ps` | 文档定义域 1-49，但实际每页最多返回 20 条 |

返回 `data.replies[]`、`data.page.count`（该根评论下的回复总数）、`data.root`。

- 只有 `rcount > 0` 的主评论才需要拉。
- 主评论接口可能顺带返回 3 条预览回复，但**不能当作完整楼中楼**。
- 必须按 `rcount` 翻页；每页 20，直到拿满或返回空。
- B 站楼中楼只有两层：对楼中楼的回复也平铺在同一个 `root` 下，`parent` 指向被回复者。本 skill 不再递归。
- 需要单条对话树时可用 `GET /x/v2/reply/dialog/cursor`（`dialog=<rpid>`），本 skill 未使用。

## 评论对象里常用的字段

- `rpid` / `root` / `parent` / `dialog`：评论 id / 根评论 / 父评论 / 对话树 id。
- `rcount`：楼中楼回复数；`like`：点赞；`ctime`：Unix 秒。
- `member.uname` / `member.mid` / `member.level_info.current_level`。
- `content.message`：正文；`content.picture` 等表示图片。
- `reply_control.location`：IP 属地；`reply_control.is_pinned`：置顶。
- `member.mid == 视频 owner.mid` 即 UP 主。

## 风控与限速

- HTTP 412、`code=-352`、`code=-403` 都按风控处理：停止、不重试、不换账号/IP。
- 本 skill 默认 `--delay 1.5` 秒（0~0.4 秒抖动），主评论 100 条、单楼 100 条、楼中楼总预算 600 条。
- 评论区可能关闭：`12002`；类型不合法：`12009`。
- 删除的评论、审核中的评论不会返回，`rcount` 与实际可抓数可能不一致；输出必须写“已抓/总数”。

## 实测记录

- 2026-11-02，`av170001` / `BV17x411w7KC`：
  - 匿名：主评论 3 条，`count=184249`，`pn=2` 为空。
  - 楼中楼匿名可翻页：`root=1353715029` 第一页 20 条，`rcount=618`。
  - 登录后实测：主评论 20 条/页正常翻页；楼中楼 `root=1353715029` 取 25 条跨 2 页、无重复。
  - WBI 备用接口实测：签名请求 `wbi/main` 返回 `code=0`、20 条、`next_offset` 有值。
  - 说明“匿名只有 3 条”只约束主评论列表，楼中楼接口本身仍能读，但登录后总量/字段更完整。