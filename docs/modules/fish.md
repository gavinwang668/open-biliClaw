# 蓝鱼实验室（`fish/`）

> 状态：🧪 实验室功能，默认关闭，默认只预览不发布。
> 设计说明与取舍见 [2026-10-19-blue-fish-lab-spec.md](../plans/2026-10-19-blue-fish-lab-spec.md)。

## 它是什么

给 OpenBiliClaw 配一只**独立的小号**「蓝色大肥鱼」，让它出现在 B 站评论区：
从主链路**已经评估过的推荐池**里取视频 → 读评论区 → 挑一条评论 → 用人设回复并 @ 对方。

它是一层**动作（action layer）**，不是第二套管线：爬取与内容评估直接复用推荐链路，
这里只补上「在评论区说话」这个对外动作。也正因为一说话就是拿账号在公网留痕，模块的
设计重心是隔离和闸门，不是模型能力。

**鱼的反馈和用户的反馈是两条线。** 鱼发出去的评论只写进它自己的账本：不写推荐反馈、
不把推荐行标成「已评论」、不触发 cognition，也就不会改变用户画像。画像只由用户自己的
行为驱动。

## 开关与默认值

两层开关，默认都不放行：

| 层 | 字段 | 默认 | 含义 |
|---|---|---|---|
| 总开关 | `enabled` | `false` | 关着的时候不读 Cookie、不调 nav、不写账本；手动点「跑一轮」也只返回 `disabled` |
| 预览开关 | `dry_run` | `true` | 只生成预览并记账，不调用 `post_comment`；后台循环只在 `enabled && !dry_run` 时动手 |

所以默认状态下这个功能**一个外部请求都不会发**，也不会创建 `data/fish/`：设置页多一个
Tab（点了才发请求），启动时多一个每 5 分钟醒一次、只看配置就继续睡的协程
（`OPENBILICLAW_FISH_LOOP_DISABLED=1` 可彻底关掉它）。

## 它不是什么

- 不做点赞 / 收藏 / 转发 / 发动态
- 不做私信，只在评论区说话
- 不做多账号、不做人工审批队列（v1 用 `dry_run` + 配额代替）
- **不影响用户画像**：鱼的评论不会变成推荐反馈、不会出现在 cognition 更新里
- **不维护自己的画像/话题记忆**：成长只有账本推导出来的等级与经验
- **不碰主账号**：没有鱼自己的 Cookie 时，它不会退回去用 `[bilibili].cookie`

## 账号隔离

| 项 | 做法 |
|---|---|
| 凭证 | `<data_dir>/fish/bilibili_cookie.json`，与主账号 `bilibili_cookie.json` 分开 |
| 客户端 | 每次运行新建 `BilibiliAPIClient`，只吃鱼自己的 cookie |
| 身份校验 | 鱼的 `mid` 必须与主账号不同；Cookie 字符串也不能和主账号相同 |
| 失败行为 | 不满足就拒绝运行，状态页给出「必须是独立小号」的明确原因 |

`AuthManager` 本来就按 `data_dir` 存 `<data_dir>/bilibili_cookie.json`，所以用
`FishAccount(data_dir)` 拿到的就是一个完全独立的鉴权实例，没有改动现有鉴权代码。

## 数据布局

```
<data_dir>/fish/
├── config.json            # 实验室配置（刻意不进 config.toml）
├── bilibili_cookie.json   # 鱼自己的 Cookie
└── ledger.jsonl           # 一次行动一行，追加写
```

**没有 `state.json`。** 日配额、等级 / 经验、`bvid:rpid` 查重、单视频上限，全部由账本
现算（`FishStore.view(today)` 一次扫描）。理由是这只鱼不该在旁边长出一套自己的记忆：
它做过什么只写在账本里，它对「用户喜欢什么」的影响通过主链路的反馈事件发生。

为什么配置不写进 `config.toml`：字段还在变，塞进主配置会拉大签名 / 热重载 / 迁移的
兼容面；而 `fish/` 目录天然支持「删目录即重置」。

## 配置项

| 字段 | 默认 | 说明 |
|---|---|---|
| `enabled` | `false` | 实验室总开关 |
| `dry_run` | `true` | 只生成不发帖 |
| `persona_name` | `蓝色大肥鱼` | 对外名字 |
| `persona_prompt` | 内置 | 人格设定，设置页可改 |
| `reply_rules` | 内置 | 额外说话规矩，设置页可改 |
| `max_chars` | `80` | 单条字数上限（10–300） |
| `daily_limit` | `3` | 每天最多发几条（0–20） |
| `min_interval_minutes` | `30` | 两次发言的最小间隔（5–1440） |
| `per_video_limit` | `1` | 同一视频累计最多几条（跨天，1–3） |
| `max_candidates` | `5` | 每轮看几个视频（1–20） |
| `comments_per_video` | `10` | 每个视频取几条评论（1–50） |
| `min_comment_likes` | `0` | 只回点赞 ≥ N 的评论（0–10000） |
| `blocked_words` | `[]` | 命中即丢弃这条回复（最多 50 个） |
| `allow_top_level` | `false` | 是否允许发顶层评论 |

所有字段在读写两头钳制：越界就近取边界值、类型不对回落默认值，并在日志里
`WARNING` 记一条 —— 一个坏数字不该把设置页打挂。

## 一轮怎么跑

1. 守卫：启用 / 已登录 / 账号隔离 / 日配额 / 最小间隔（配额与间隔来自账本）
2. 取候选：`Database.get_recommendations(exclude_processed=True)` —— 已经过主链路评分 /
   解释、且没被点赞 / 点踩 / 屏蔽 / 评论过的 B 站条目；非 B 站条目直接过滤
3. 逐个视频拉评论，按点赞门槛、长度、是否已回过过滤
4. 一次 LLM 调用同时完成「挑哪条」+「怎么写」（这是唯一新增的模型调用）
5. 硬闸校验：字数、屏蔽词、空回复、rpid 必须在候选里
6. `dry_run` → 记 `preview`；否则 `post_comment(root, parent)` 发楼中楼
7. 发布成功 → 只写鱼的账本。推荐行、画像、cognition 一律不动
8. 等级 / 经验由账本推导（真实发布 = 3 XP，每 12 XP 一级）

第 7 步是隔离的关键：因为推荐行不会被标成「已评论」，那份推荐仍然留在用户的列表里、
仍然可以被用户自己反馈；鱼的查重只能靠账本 —— 同一 `bvid:rpid` 不回第二次，
同一视频达到 `per_video_limit`（默认 1，跨天累计）就换下一个候选。

模型返回 `skip` 是合法结果：宁可这次不说话，也不硬凑。`skip` 会记进账本，
所以「什么都没发生」永远有据可查。

## 安全闸（代码级）

1. `enabled` 关闭 → 不跑：不读 Cookie、不调 nav、不写账本（手动触发也一样）
2. 没有鱼 cookie / 失效 → 不跑
3. 鱼 mid == 主号 mid，或 cookie 与主号相同 → 拒绝
4. 当日计数 ≥ `daily_limit` → 不跑
5. 距上次发布 < `min_interval_minutes` → 不跑（手动触发不受此限）
6. 同一 `bvid:rpid` 只回一次（账本查重）
7. 同一视频累计已回 ≥ `per_video_limit` → 跳过（按账本跨天累计）
8. 回复为空 / 超字数 / 命中屏蔽词 → 丢弃并记 `skipped`
9. B 站接口报错 → 记 `failed`，本轮停止，不自动重试
10. `dry_run` 默认开 —— 要用户自己关掉才会真的发

`preview` 不占配额也不触发最小间隔：它没有发布任何东西。

## 公开 API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/fish/status` | 开关、账号、等级 / 经验（由账本推导）、今日计数、最近 20 条账本 |
| GET | `/api/fish/config` | 读配置（已钳制） |
| PUT | `/api/fish/config` | 改配置，逐字段钳制，返回生效值 |
| POST | `/api/fish/account` | 校验并保存鱼 cookie（与主号相同时拒绝并回滚） |
| DELETE | `/api/fish/account` | 清除鱼 cookie |
| POST | `/api/fish/run` | 跑一轮（`dry_run` 可覆盖配置） |
| GET | `/api/fish/ledger?limit=50` | 读账本，最新在前 |

写接口全部走 `_bilibili_credential_gate`：一次 run 就是拿账号在公网说话，不能只靠
「本机大概安全」。`config` / `status` / `account` / `ledger` 在 degraded 模式下仍可访问
（它们不依赖 LLM），`run` 不行。

## 后台循环

`run_fish_loop()` 每 5 分钟醒一次，只有 `enabled && !dry_run` 才真的跑一轮。
循环挂在 FastAPI startup 上，异常全部兜住 —— 实验室循环不该把 daemon 带走。
测试用 `OPENBILICLAW_FISH_LOOP_DISABLED=1` 关掉它。

## 前端

- 桌面 Web：设置页新增「养蓝色大肥鱼」Tab（`web/desktop/assets/js/fish-settings.js`）
- 扩展 popup：设置浮层同样的 Tab（`extension/popup/popup-fish.js`）

两处都不走全局 `PUT /api/config` 保存流程，各自调 `/api/fish/*`：鱼的 cookie 和
实验室字段不该混进主配置表单。

**有意排除**：移动 Web 设置浮层（只有语言与收藏同步）、CLI。移动端可以看推荐，
但「让一个号去评论区说话」这件事要求先看清人设和配额，桌面端更合适。
CLI 若需要，后续可加 `openbiliclaw fish run`，当前只提供 HTTP 接口。

## 测试

| 文件 | 覆盖 |
|---|---|
| `tests/test_fish_config.py` | 默认值、钳制、坏 JSON、屏蔽词去重 |
| `tests/test_fish_guard.py` | 全部守卫分支、字数 / 屏蔽词 / 清洗 |
| `tests/test_fish_engine.py` | dry-run 不发布、发布路径的 root/parent/@、配额、跨天视频查重、账号隔离、skip、超长、只写账本 |
| `tests/test_fish_api.py` | 路由、配置往返、空 cookie 拒绝、Cookie 文件隔离，以及**发布后不影响用户侧**（推荐行仍是未处理、cognition 没被调用、候选池仍包含它） |
| `tests/test_llm_prompts.py` | 新 builder 的 system 消息与入参无关 |

真实发评论属于需要授权的人工验证，不在单元测试里做。
