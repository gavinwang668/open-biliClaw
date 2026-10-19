# 蓝色大肥鱼（Blue Fish Lab）设计说明

日期：2026-10-19
状态：实现中
分支：`feat/blue-fish-lab`

## 1. 要解决什么

OpenBiliClaw 已经能发现内容、理解用户、写推荐语。缺的是**在 B 站评论区里说话**：
回复别人的评论，并在正文里 @ 对方。

「养蓝色大肥鱼」是一个**实验室功能**：给 OpenBiliClaw 配一个**独立的小号**，
让它以自己的人格出现在评论区，挑一条评论回复、@ 对方，并把每次行动记到账本里。
「养」体现在：大肥鱼有自己的等级、经验值和话题记忆，越聊越有连续性。

## 2. 非目标

- 不做自动点赞 / 收藏 / 转发 / 发动态
- 不做多只鱼、不做跨账号共享
- 不做自动私信（评论区之外的动作一律不做）
- 不在 v1 做人工审批队列（用 `dry_run` + 配额代替）

## 3. 账号隔离（硬要求）

| 项 | 做法 |
|---|---|
| 凭证文件 | `<data_dir>/fish/bilibili_cookie.json`，与主账号 `bilibili_cookie.json` 完全分开 |
| 客户端 | 每次运行新建 `BilibiliAPIClient`，只吃鱼自己的 cookie |
| 回退 | **禁止**回退到 `config.toml [bilibili].cookie`；没有鱼 cookie 就是「未登录」 |
| 身份校验 | 鱼的 `mid` 必须与主账号 `mid` 不同，cookie 字符串也不能相同；否则拒绝运行 |
| 数据 | `fish/` 目录只放鱼自己的配置、状态、账本；删掉目录 = 重置 |

`AuthManager` 已经按 `data_dir` 存 `<data_dir>/bilibili_cookie.json`，
所以用 `AuthManager(cfg.data_path / "fish")` 即可拿到隔离实例，不需要改鉴权代码。

## 4. 配置

存在 `<data_dir>/fish/config.json`，**故意不写进 `config.toml`**：

- 这是实验室功能，字段还会变，塞进主配置会把 `config.toml` 的兼容面拉大；
- 主配置的读写路径有签名/热重载/迁移逻辑，不值得为一个实验特性动它；
- `fish/` 目录自带配置，天然支持「删目录即重置」。

| 字段 | 默认 | 说明 |
|---|---|---|
| `enabled` | `false` | 实验室总开关 |
| `dry_run` | `true` | 只生成不发帖；默认就这么开 |
| `persona_name` | `蓝色大肥鱼` | 对外的名字 |
| `persona_prompt` | 内置 | 人格设定，用户可改 |
| `reply_rules` | 内置 | 额外说话规矩，用户可改 |
| `max_chars` | `80` | 单条评论字数上限（B 站上限 1000，我们主动收窄） |
| `daily_limit` | `3` | 每天最多发几条 |
| `min_interval_minutes` | `30` | 两次发言的最小间隔 |
| `per_video_limit` | `1` | 同一视频最多发几条 |
| `max_candidates` | `5` | 每轮看几个视频 |
| `comments_per_video` | `10` | 每个视频取几条评论 |
| `min_comment_likes` | `0` | 只回点赞数 ≥ N 的评论（0 = 不过滤） |
| `blocked_words` | `[]` | 命中即丢弃这条回复 |
| `allow_top_level` | `false` | 是否允许发顶层评论（默认只做楼中楼回复） |

写配置时逐字段钳制范围，非法值回落默认值并记一条 warning，**不抛异常**——
实验室功能不该因为一个数字把设置页打挂。

## 5. 运行数据

- `state.json`：`xp` / `level` / `topics`（话题计数）/ `counts`（按天计数）/ `last_run_at`
- `ledger.jsonl`：一次行动一行

账本字段：`ts`、`bvid`、`video_title`、`mode`（`reply` / `top_level`）、
`target_rpid`、`target_uname`、`target_text`、`reply`、`status`
（`preview` / `published` / `failed` / `skipped`）、`error`、`xp`。

## 6. 一轮怎么跑

```
守卫（启用 / 已登录 / 账号隔离 / 日配额 / 最小间隔）
  → 取候选视频（推荐池里最近的高分内容）
  → 逐个视频：拉评论 → 过滤（点赞门槛 / 已回过 / 太短太长）
  → 组 prompt → LLM 选一条 + 写回复（一次调用）
  → 硬闸校验（字数 / 屏蔽词 / 空回复 / 只挑一条）
  → dry_run ? 记账为 preview : 调 post_comment 发出去
  → 记账 + 加经验
```

- LLM 一次调用同时完成「挑哪条」和「怎么写」，失败就放弃这一轮，不重试轰炸。
- 回复写成楼中楼：`root` = 目标评论的根，`parent` = 目标评论本身；
  正文以 `@昵称` 开头（B 站的 @ 是纯文本）。
- 只有 `allow_top_level` 打开且模型明确选 `top_level` 时才发顶层评论。

## 7. 安全闸（代码级，不靠提示词）

1. `enabled` 关闭 → 不跑
2. 没有鱼 cookie / cookie 失效 → 不跑，返回明确原因
3. 鱼 mid == 主号 mid → 拒绝（隔离破坏）
4. 当日计数 ≥ `daily_limit` → 不跑
5. 距上次发言 < `min_interval_minutes` → 不跑
6. 同一 `rpid` 只回一次（账本查重）
7. 同一视频 ≥ `per_video_limit` → 换下一个视频
8. 回复为空 / 超 `max_chars` / 命中屏蔽词 → 丢弃并记 `skipped`
9. B 站接口报错 → 记 `failed`，本轮停止，不自动重试
10. `dry_run` 默认开，要用户自己关掉才会真的发

## 8. API

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/api/fish/status` | 开关、登录态、等级、今日计数、最近记录 |
| GET | `/api/fish/config` | 读鱼配置 |
| PUT | `/api/fish/config` | 改鱼配置（逐字段钳制） |
| POST | `/api/fish/account` | 校验并保存鱼 cookie |
| DELETE | `/api/fish/account` | 清掉鱼 cookie |
| POST | `/api/fish/run` | 跑一轮（`dry_run` 可覆盖） |
| GET | `/api/fish/ledger` | 读账本 |

## 9. UI

- **桌面 Web 设置页**：新增 Tab「养蓝色大肥鱼」（实验室徽标）
- **扩展 popup 设置浮层**：同样的 Tab，复用同一组接口
- 面板内容：状态行 / 总开关 / dry-run / 账号（cookie 输入 + 校验）/ 人格与规矩（可编辑）/
  配额 / 「预览一轮」「发一轮」/ 最近账本

两个前端都不把鱼 cookie 写进全局 `config.toml` 保存流程，单独调 `/api/fish/account`。

## 10. 后台循环

`BlueFishService.run_forever()`：每 5 分钟醒一次，交给引擎自己判断配额和间隔。
跟 `refresh_task` 一样挂在 `task_registry` 上，配置热重载时随其它后台任务一起重启。

## 11. 测试

- 配置读写与钳制（含坏 JSON）
- 守卫：未启用 / 未登录 / 账号相同 / 超配额 / 间隔不足
- 账本查重：同一 rpid 不回第二次
- dry-run 不发请求
- 发布路径：fake client 收到正确的 `root` / `parent` / `@`
- prompt builder：system 消息与调用参数无关（接进 `test_llm_prompts.py`）
- 账号隔离：绝不读主账号 cookie
