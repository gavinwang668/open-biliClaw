# Issue #332：跨进程扩展在线状态修复验收

- 范围：`capability-increment`，修复已有浏览器任务的共享在线准入；不扩展平台取数、账号权限或 UI。
- 基线：`be8714c2449ba43c4d193cbff4c285fdc16ecf8a`，与 0.3.231 的相关 presence / producer 实现一致。
- 分支：`fix/issue-332-shared-presence`。
- Worktree：`/Users/white/workspace/OpenBiliClaw/.worktrees/issue332-shared-presence`；主工作区既有 `outputs/`、`promo/` 未动。
- Python：共享开发环境的 Python 3.12；每条命令显式设置 `PYTHONPATH="$PWD/src"`，已确认 `openbiliclaw.__file__` 指向该 worktree。
- 第一阶段隔离：单测和进程集成的数据库、配置、在线状态均位于 pytest 临时目录；这一阶段未调用真实模型。随后按用户要求进行的真实环境验证见文末，两个阶段的证据分开记录。

## 根因与限定契约

原 `PresenceTracker` 是带线程锁的进程内计数器。API 的 WebSocket 更新自身实例，而 full/discovery worker 重新构造 runtime，90 秒启动宽限后其抖音 producer 在发现/入队之前返回 `extension_absent`。此前的测试只验证一个进程中的计数和 fake presence，未穿过 API → worker 边界。

本次共享契约：

```text
API runtime-stream connect / disconnect / idle receive heartbeat
    → same local tracker, serialized atomic file replacement
    → data/runtime/extension_presence.json
    → headless full/discovery worker is_present()
    → unchanged DouyinDiscoveryProducer / DyTaskQueue
    → GET /api/sources/dy/next-task → existing claim protocol
```

活动 API 是单一发布者；worker 构造和无本地连接的 heartbeat 不写文件。活动连接每 20 秒续约，60 秒无新心跳即失效；正常断开发布零连接，按调用方 grace 判断。活动心跳使用 wall clock，进程内计数和启动宽限继续使用 monotonic。正常/降级 factory 使用相同启动数据目录，热重载保留 tracker。原子临时文件由 `mkstemp` 创建，POSIX 权限为 0600；读到缺失、损坏、未来时间、非法数值或过期租约时不提供共享在线证据。共享写失败记录 WARNING，保留本地连接状态。

共享 presence 证明在线准入，不证明登录有效、页面 capability、任务执行结果或上游可访问性。`douyin direct request returned HTTP 403` 仍单独表示直连失败。初始化 `empty_signals` 和 worker 自身心跳过期没有被伪装成同一个问题。

## 验证记录

| Gate | 适用性 | 状态 | 证据层级 / 证据 |
|---|---|---|---|
| Worktree / import provenance | required | PASS | 静态；独立分支、原 checkout 未修改，Python import 指向 worktree |
| 原因最小复现 | required | PASS | 本地双进程；原诊断脚本两次出现 API 在线、worker `extension_absent`、发现调用 0 次；仅给 worker 本地计数连接后可继续 |
| 真实任务领取回归 | required | PASS | 本地进程集成；`tests/test_shared_presence.py` 使用 `spawn`，API 持有真实 TestClient WebSocket，独立 worker 的启动宽限已过；启用共享发布后实际 SQLite 入队且 next-task 返回 200，关闭发布则仍为 `extension_absent` + 204 |
| API 空闲续约 / 降级连接 | required | PASS | 集成；正常和 degraded 两种 WebSocket 均定时续约，退出后发布零连接 |
| 正常 / 降级 factory | required | PASS | 单测；相同 data root，worker 构造不覆盖 API 文件，worker 本地连接计数为 0 仍能读到共享在线 |
| 源码 / 桌面 / Docker 启动接线 | required | PASS | 静态；CLI 与 `packaging/entry.py` 均经 `create_app()` / `backend_child_env()` 保留同一 active data root，worker 用同一 factory；Docker 的 `serve-api` 复用 CLI，状态文件位于原 data volume |
| 过期 / 多客户端 / 断线宽限 / 重连 | required | PASS | 单测；worker 不能自行续活 API；最后连接退出才进入 grace，重连立即恢复，异常重复 disconnect 不刷新文件 |
| 损坏文件 / 非有限值 / 未来时间 / 写失败 | required | PASS | 单测；非法状态不给共享在线证据，写失败可诊断且不打断本地在线 |
| 相关 producer / 增量同步 / worker 既有行为 | required | PASS | 108 个 scoped tests 通过，含新的共享状态测试和原 presence、抖音、B 站、周期同步、worker boot 测试 |
| 完整后端测试（排除可选 integration） | required | PASS | `pytest -m 'not integration'`：11969 passed、46 skipped、176 deselected，退出码 0，2085.52 s；本次新增共享状态回归全部执行并通过 |
| 可选桌面浏览器 integration 基线 | N/A：前端未改 | FAIL | `test_recommendations_and_runtime_recover_without_leaving_init_gate` 期望库存 30，实际为 27；原 main 单独运行同样失败（25.36 s），相关测试和前端文件与 main 无 diff |
| Ruff / MyPy | required | PASS | `ruff check src/ tests/`；`mypy src/`：363 个源文件无类型错误 |
| 模块文档 / changelog / 架构图 | required | PASS | runtime、api、changelog、architecture、spec、README 中英文同步 |

注册审计与完整来源契约为本次范围的 N/A：没有 source slug、registry、auth、schema、任务 payload、claim/result 协议或平台路由改动；本文件限定共享准入契约，未把修复扩大成“抖音全链路已完成”。

四个产品面继续共用原后端与协议，没有 UI 变更，不要求扩展重装。第一阶段未验证真实来源、模型或安装版浏览器；第二阶段补测本机 Chrome 与真实请求，结果见下文。Firefox、Windows PyInstaller 安装包及 Docker 真机仍为 **NOT_RUN**；本机进程集成使用 Windows 同款 `spawn`，不能当作这些部署的实机证据。没有依赖、配置或安装流程变更，不需要修改安装器。

## 命令

在本 worktree 下使用 `/Users/white/workspace/OpenBiliClaw/.venv/bin/python`，显式指定 `PYTHONPATH="$PWD/src"`：

```bash
python -m pytest tests/test_shared_presence.py tests/test_presence.py \
  tests/test_douyin_producer.py tests/test_bilibili_producer.py \
  tests/test_source_incremental_sync.py tests/test_worker_degraded_boot.py \
  -q --disable-warnings -p no:cacheprovider
python -m pytest -q -m 'not integration' --disable-warnings -p no:cacheprovider --tb=short
python -m ruff check src/ tests/
python -m mypy src/
```

首批回归在实现前失败；实现后初轮 scoped tests 为 `108 passed`，随后加入发布关闭对照并覆盖 API runtime-stream 的筛选轮为 `44 passed, 704 deselected`。最终固定代码的后端全量结果为 `11969 passed, 46 skipped, 176 deselected, 12866 warnings in 2085.52s`，退出码 0；skip/deselect 不作为真实账号、浏览器或安装包验证通过的证据。`ruff check src/ tests/`、改动文件的 `ruff format --check` 与 `git diff --check` 通过；MyPy 为 `Success: no issues found in 363 source files`。

完整 `pytest` 曾连同本机已安装的 Playwright 可选集成测试运行，并在未改的桌面库存断言中失败。该用例只通过 `ThreadingHTTPServer` 模拟 API 并读取静态前端，不调用本次修改的后端；切回原 main、显式使用 main 的 `PYTHONPATH` 单独运行仍得到 `expected 30 / actual 27`。确认基线后停止慢速浏览器轮次（停止时 1 failed、3464 passed、2 skipped；该轮没有跑完整仓），将必需的后端全量检查独立运行，未修改旧库存断言或标成通过。

判定：`complete`，仅指 #332 的跨进程在线准入修复与本地进程集成验收；不表示整个来源、可选桌面浏览器套件或安装包全绿。代码、测试和文档在独立分支提交，修复版本以本分支 HEAD 为准；未合并、安装、发布或关闭 issue。

## 第二阶段：真实 Chrome、扩展和上游请求

用户随后明确要求真实环境、真实请求 E2E。本轮代码固定为 `f201187ad5b5a75f57a378ac4ab4a32b5256c33a`，Python import 指向本 worktree。运行的是实际 Uvicorn/FastAPI 与独立 Python worker；没有 TestClient、HTTP stub、人工构造的 task-result 或替换的模型响应。worker 使用生产 `build_runtime_context()`、`DouyinDiscoveryProducer`、`DouyinPluginSearchClient` 和统一候选评估/推荐组件，由诊断脚本有界触发，没有宣称运行了无人值守 full-worker 调度循环。

### 实机与隔离

- 已安装 Chrome 商店扩展：`cdfjfkdjjhdaccbldipkjhpibnfbiamg`，版本 `0.3.231`。实际包位于 `~/Library/Application Support/Google/Chrome/Default/Extensions/cdfjfkdjjhdaccbldipkjhpibnfbiamg/0.3.231_0`；没有用 worktree build 冒充安装包。
- 安装包 service worker SHA-256：`a64489d44aa541bd1f7dc04172a9a5daedac70dae957b13918787f018c43e759`；manifest SHA-256：`3966fe13e7155ac94f3f34cf6585a8deabe64ac368561ce7fab14318c032f386`。
- 扩展实际连接 `127.0.0.1:18420`。原服务位于 `~/Library/Application Support/OpenBiliClaw-AppReview`，通过未修改的 `com.openbiliclaw.app-review.plist` 管理。测试临时停下原服务，在相同 loopback 端口启动修复版，使用 `/tmp/openbiliclaw-issue332-live-20261025-run1` 的独立配置、空 SQLite 和数据目录；只复制已有画像、用户覆盖和抖音凭据供真实调用，不写原画像、收藏或推荐库。
- 使用项目 loader 合并原配置并保留模型路由与 embedding：GLM / DeepSeek / SenseNova 真实配置链，`nomic-embed-text` Ollama embedding。没有把云模型换成本地测试模型，没有调低相关性阈值。其它来源、周期账号同步、更新器、tailnet 和自启动在隔离配置中关闭；API 的后台循环委托关闭，由单个诊断 worker 执行当轮业务。
- 预检时真实 `/api/sources/status` 确认抖音凭据已经通过联网验证。未重新扫码或证明私有账号初始化；本轮只跑公开发现的只读任务，没有点赞、收藏、关注或发布。

### 已取得的实测证据

| 链路 | 结果 | 证据范围 |
|---|---|---|
| 无连接负对照 | PASS | worker 实际等待 95.04 s，`active_count=0`，返回 `extension_absent`，任务为 0；没有注入时钟或调用 worker 的 `on_connect()` |
| 扩展连接与续约 | PASS | 实际 `client=background` 为唯一连接；连续 100.05 s 的六次采样显示共享 `active_count=1`，`updated_at` 每约 20 s 更新；状态文件权限为 0600 |
| 宽限后跨进程准入 | PASS | 第一个 worker 运行 320.98 s、本地断线计时 322.89 s、本地连接仍为 0，但共享在线为 true；第二个正式推荐流 worker 也实际等待 95 s 后进入生产者 |
| 正式 producer → 已安装扩展 → 真实抖音 → 入队 | PASS：feed | 正式 `produce_if_due(limit=3)` 返回 `discovered=3`、`source_counts={dy-plugin-feed:3}`、`feed=used`、`enqueued=3`；对应 task 在真实 API 领取并完成，发生在扩展重载之前 |
| 禁用直连兜底的独立浏览器探针 | PASS：feed | `allow_direct_fallback=False`，实际 21.58 s 返回 3 条稳定 ID、非空标题视频，task `completed`；扩展诊断为 MAIN tap 安装成功、DOM 取到 3 条，API/被动响应取数为 0，不冒充浏览器 JSON API 成功 |
| 后端直连 | PASS：transport | 真实 hot terms 返回 3 条、feed 返回 2 条；独立正式 normalizer 产生 2 条 `dy-direct-feed` 待评估候选。与扩展结果分别计数，不能替代安装版 dispatcher 的证明 |
| 首轮 search / hot | FAIL / NOT_RUN | 第一个 search task 已领取，但后端等待 180 s 后 `wait_timeout`；共享 cycle deadline 使其余关键词及 hot 分支记 timeout，不能算作实际执行了三个搜索或热榜任务。首个并行 feed 探针在队列中等待超时；未把后续成功覆盖掉这些失败 |
| 普通行为污染 | PASS：发现阶段 | 成功独立 feed 探针前后 `events` 增量 0；首轮全表比较中的 events、seen、profile ledger、visual profile、V2EX snapshot 等行为 sink 增量均为 0 |
| 真实 LLM 评估与拒绝回写 | PASS：负向 admission | 串行轮 `evaluated=5, cached=0, rejected=5`，5 条全部持久化为 `rejected_low_score`；SenseNova 真实模型完成有效轮，未重写分数或放宽阈值 |
| 推荐 HTTP 输出 | PASS：空结果；正向出卡未验证 | 真实 `GET /api/recommendations` 返回 200、`items=[]`；与候选全部被拒绝相符，不能宣称成功产生推荐卡 |

本机使用自定义 18420 端口，现有插件客户端的 best-effort kick 仍固定访问 8420。本轮在任务 pending 时显式调用真实 `POST /api/sources/dy/kick` 唤醒扩展，未手工领取或提交任务结果；因此不把本次结果解释为该自定义端口下的无人值守唤醒验收。`POST /api/extension/reload` 在正式 feed 已成功回传之后才调用，不能把成功归因于重载。扩展的 legacy task/result 没有 extension ID 字段，安装 artifact 的对应关系来自 Chrome 安装记录、相同 endpoint 和唯一 background 连接，未伪造 callback 身份字段。

真实模型请求遇到上游 429，正常使用原有 fallback 链；关键词生成实际由 `sensenova-6.8-flash-lite` 完成，第一批评估实际调用 `deepseek-v4-flash`。初次直连候选评估期间，另一个诊断进程构造 pipeline 时重置了 evaluating 租约，使这批结果未写回；这是本轮测试编排干扰，该轮不作为评估通过证据。之后停止并发重建，由已有 worker 串行处理 5 条真实候选。

浏览器观察工具可列出真实任务 tab 与任务 marker，DOM/截图读取多次超时；没有据此认定验证码或具体页面根因，也没有绕过浏览器工具禁止的扩展管理页。未取得三端 UI 截图，不把来源取数通过扩大成 UI 验收。

### 最终计数与结论

有效串行评估共 5 条：直连来源 2 条分别得分 0.15、0.30；扩展正式 producer 的 3 条分别为 0.25、0.25、0.55，低于 producer 保留的 0.60 准入门槛。最终 `discovery_candidates=5`、`rejected_low_score=5`、`content_cache=0`、`recommendations=0`，普通 `events=0`、seen/profile ledger/saved memberships 均为 0。独立浏览器探针的另外 3 条只验收 dispatcher 往返，没有混入这批业务候选。

判定：**#332 的跨进程在线准入和真实 feed 取数/候选入队通过；真实模型完成了低分拒绝分支。搜索成功、安装版 hot 分支、正向推荐出卡及 Windows 原环境仍未验证通过，不能汇总为所有来源模式完整 E2E 全绿。** 本轮没有据这几个样本调低阈值，也未把“模型请求成功”单独当作候选评估写回通过。

脱敏任务状态、真实时间采样、候选分数、模型调用计数和恢复检查保存在 [live-evidence.json](assets/2026-10-25-issue-332/live-evidence.json)。原始配置、Cookie、画像、关键词、模型 prompt/response 与原始日志不进入仓库。

### 恢复与清理

通过原 launch agent 恢复原服务，18420 的健康检查返回 200，画像、embedding 与模型注册就绪；原 `config.toml` 与测试前 SHA-256 一致。重启和 Cookie 同步后，抖音状态暂为 unverified；真实 `POST /api/sources/douyin/verify` 返回 verified，最终状态为 ready / logged_in=true。隔离 API、supervisor 和 worker 均已退出，临时配置、凭据、画像副本、SQLite、原始日志与诊断脚本已删除，仓库只保留脱敏记录。

浏览器 inventory 确认无遗留 `openbiliclaw_dy_task` 任务页。两个临时观察页的显式关闭被浏览器工具报 `Debugger unattached`，CDP 关闭也超时，关闭状态未确认；它们未被标记保留，仍适用工具的回合结束自动清理。未关闭用户原有页面，未更换扩展安装包或 backend endpoint。

修复代码仍在独立分支；本轮恢复的是原先运行的服务，没有将测试版合入 main、长期部署、发布或关闭 issue。

本轮只新增验收文档和脱敏 JSON，没有再改生产代码。提交前重新运行 `pytest tests/test_shared_presence.py -q --disable-warnings -p no:cacheprovider`：`18 passed, 32 warnings in 47.59s`；证据 JSON 可解析、最终计数断言与 `git diff --check` 通过。前述完整后端 / Ruff / MyPy 结果仍对应同一修复代码，未重复运行整仓长测试。

## 第三阶段：搜索、热榜与正向推荐卡

> 本阶段的“修复版待加载”是当时的状态；后续自主加载与热重载证据见[第四阶段](#第四阶段自主加载修复版与后端热重载)。热榜取数和出卡仍按各阶段真实结果分别记录。

用户追加要求“搜索热榜正向推荐出卡，有问题就修复”。本阶段继续使用原 worktree、真实画像、真实凭据、原模型链及 Ollama embedding，准入门槛保持 0.60。范围仍是 `capability-increment`。画像、关键词、原始模型输出和凭据不进入仓库。

### 新发现与修复

- `695b1d15`：默认 `kick_douyin_task_dispatcher()` 固定访问 8420，与本机实际 18420 不符。搜索、热榜、feed 三个真实客户端入口的回归先出现 `expected 18420 / actual 8420`，修复后读取当前 `api.port`。主取数轮在入队同秒领取，后续故障探针也在秒级领取，没有手动 kick。
- `3e706907`：`DyTaskQueue.fail()` 原来覆盖整个 partial payload，发现客户端又丢弃非 completed 结果。六个回归覆盖三类任务的后续失败 / 客户端超时；现在保留已接收视频供正常评估，仍返回原 `failed` / `timeout` 诊断，不把失败改成成功，取消和 first-final-wins 保持原约束。
- 同一提交修复扩展热榜种子路径：未成功点击热榜时直接用 hot board 的 seed 调 related API，跳过首页的视频路由等待、DOM 采集及滚动。原 helper 会把首页当前视频误用成热榜种子；原 2 秒等待在延迟定时器回归中累计到 11 秒。现在保留种子归属并按 monotonic 实际经过时间结束等待。新增两条回归先红后绿；不增加权限、payload 或配置。

初始 API 和常驻诊断 worker 从 `695b1d15` 对应源码启动，随后未在评估期间重建 runtime。独立公开取数探针也使用本 worktree。后续明确停下旧 worker、恢复原服务，再重启 `3e706907` 的隔离 API；新 API 的健康检查和推荐 HTTP 返回再次通过。扩展始终按实际安装记录归因：商店版仍为 0.3.231，后端重启没有部署新的 content script。

### 真实链路与结果

| Gate | 适用性 | 状态 | 实测证据 |
|---|---|---|---|
| 自定义端口自动唤醒 | required | PASS | 真实 18420 API、Chrome 商店扩展，search / hot 秒级 claim；没有测试注入 claim / task-result |
| 搜索取数 | required | PASS | 两次真实搜索分别回传 9、10 条；禁用 direct fallback。关键词取自原画像，第二次选择具体兴趣，未改画像 |
| 热榜小批次 | required | PASS | 3 条目标实际返回 3 条，约 166.8 s；related API 1 页、3 条，seed 与 hot board 对应；UI 点击失败、DOM 0，不能写成热榜 DOM 路由成功 |
| 商店版热榜较大批次 | required | FAIL | 12 条目标两次均收到扩展 `task_timeout`，没有用小批次成功覆盖失败；这两次不是后端漏 kick |
| 修复版热榜真机 | required | BLOCKED | 代码、类型检查、回归和 Chrome 构建完成；浏览器接口不支持扩展管理页，已提供本地测试构建并请求用户加载，尚无加载完成证据 |
| 正常评估与 admission | required | PASS | 共 22 条真实候选：搜索 19、热榜 3；19 条低分拒绝、3 条搜索内容准入，分数 0.70 / 0.78 / 0.82 |
| 热榜候选正向出卡 | required | FAIL | 本轮入队的 3 条热榜候选均被真实模型低分拒绝，尚无 hot → 正向卡证据；没有改分数或降阈值。需在新扩展的大批次取数复测后继续验收 |
| 真实模型文案与推荐落库 | required | PASS | `recommendation.write_expression` 完成 3 条，文案长度 115 / 159 / 160；正常 `serve()` 持久化 3 条推荐，库存变 shown |
| HTTP 推荐响应 | required | PASS | 真实 `GET /api/recommendations` 返回 200、3 个对应 content ID；重启到最新 API 后再次返回相同 3 条 |
| 桌面实际渲染 | required | PASS | Codex 内置浏览器打开真实 `/web`，3 张卡逐一匹配 HTTP 的标题、文案和链接；3 张封面加载成功，每张 5 个反馈按钮。没有拦截或替换页面 API |
| 普通行为隔离 | required | PASS | `events`、`seen_items`、`profile_update_ledger`、`saved_memberships` 都为 0；没有点击反馈或执行上游收藏 / 点赞 / 关注 |
| 最新后端保留真实 partial | required | PASS | 临时观察层延迟真实最终回调；先收到 3 条 partial，180.85 s 后客户端返回 3 条及 `timeout`，任务为 `failed / wait_timeout`；迟到最终回调未覆盖失败，前后 3 个视频 ID 完全一致 |
| 后端自动回归 | required | PASS | plugin / strategy / queue 57、API ingest 15、task completion protocol 24、presence 18、producer 25 项通过，共 139 项 |
| 扩展相关回归 / 构建 | required | PASS | 113 项相关 Node 测试；Chrome build、TypeScript 声明编译、22 项 manifest / WAR asset preflight 通过 |
| 扩展全套旧前端断言 | N/A：未改对应 UI | FAIL | 1832 passed、2 failed；pending confirmation wiring 与 settings 6 列断言在未改 main 上均独立复现，未修改旧断言 |
| Ruff / MyPy | required | PASS | 全 `src/ tests/` Ruff；MyPy 363 文件；改动 Python 文件 format 与 diff check 通过 |

真实模型沿原路由出现 429 并正常 fallback。有效 usage 记录包括 DeepSeek / SenseNova 的评估与 SenseNova 文案调用；所有分数来自真实返回。第一轮 9 条宽泛搜索结果和 3 条热榜内容均低分拒绝，第二轮 10 条具体兴趣搜索有 3 条准入；没有为了出卡调低门槛。首次评估还执行了原配置的 embedding shadow 预过滤，因此包含较长的首次向量化时间，不能仅按模型 HTTP 耗时描述总时延。

取数由有界诊断入口触发：首轮使用生产 `DouyinDiscoveryService` → pipeline enqueue；独立热榜及具体兴趣搜索使用真实客户端、同一生产 normalizer / candidate storage API，再由同一常驻 worker 的统一 evaluator、文案及 `serve()` 处理。没有构造上游候选、伪造回调、注入推荐行或重写评分；也没有把这些调用描述成完整无人值守 full-worker 调度循环。

partial 故障验证单独使用最新 `3e706907` API 与客户端。第一次只延迟真实 `ok` 回调 30 秒，任务仍在 107.47 秒正常完成，这轮不是超时证据。第二次由临时 ASGI 观察层把真实最终 body 延迟到接收端预算到期：源站请求、partial / final body、180 秒客户端等待与评分规则均未替换。超时前实际采样到 `in_progress` + 3 条视频，超时后为 `failed / wait_timeout` + 同样 3 条；客户端也返回 3 条且保留 `timeout`。未将这批故障探针内容重复入队评估或作为新的正向推荐。观察层随后删除，不能当作生产实现的一部分。

自动回归曾与模型 / embedding 同时执行，两个 spawn 用例在进程冷启动 30 秒预算上失败。停止其它检查、模型空闲后串行重跑完整 `test_shared_presence.py`：18 passed，19.94 s。后端全仓长测试未在这些追加修复后重跑；前两阶段的 11969 passed 不能冒充最新代码的全仓结果。

### 构建与证据交付

本地测试目录：`outputs/issue332-extension-review/`，对应 `3e7069078c462d3887178b95c8e2cdfa4785011b`；版本标签仍为 0.3.231，标记为测试构建，没有发布商店更新。

- ZIP SHA-256：`941e607753afddf076a4588e0782bb7ae6565c20cb3ced5456e1cd2d93b16ff6`。
- content script SHA-256：`c56f51d768269efe5baa04995e8e8d845fcb9715c7a0904e63ab531b92791443`。
- Chrome 商店包身份与前阶段一致；测试包不能仅凭相同版本号当作已安装。

加载步骤已通过输入面板提供：临时停用商店版、加载该目录、将测试扩展后端地址设为 `http://127.0.0.1:18420`。未收到完成回执前，不执行依赖新扩展已安装的验收，不绕过浏览器接口限制。三张卡的截图保存在本机 `outputs/issue332-positive-cards.jpg`，包含个性化文案，不提交到公共仓库；脱敏任务、评分、HTTP 与逐卡 DOM 断言保存在 [search-hot-positive-evidence.json](assets/2026-10-25-issue-332/search-hot-positive-evidence.json)。

判定：**搜索 → 真实模型评估 → 文案 → HTTP → 桌面三张卡已通过；热榜小批次通过。追加热榜修复已经实现、自动验证并构建，修复版较大批次的安装实机门槛仍未通过，因此本阶段是 `incremental only`，不能宣称搜索 / 热榜所有模式全绿。** 新扩展 popup、移动 Web、Firefox / Windows 安装真机不是本阶段桌面截图的证明；共享 DTO 与 UI 未改，也没有声称这些面已新增实机验收。

### 第三阶段恢复与清理

最后一次故障任务与迟到回调收尾后，Chrome inventory 确认没有 `openbiliclaw_dy_task` 页面，本轮创建的内置浏览器页面已关闭。所有 7 个隔离任务均为终态，22 条业务候选都已评估完毕。最终 API 在最新源码下仍返回相同 3 条推荐，四个普通行为 sink 均为 0。

已通过未修改的 launch agent 恢复原服务：健康检查 200，画像 / embedding / 模型注册就绪；真实抖音 verify 请求返回 200，随后来源状态为 `ready / logged_in=true`。每次临时切换的恢复检查均确认原 `config.toml` SHA-256 一致。所有诊断 API、worker、guard 与取数探针均已退出，`/tmp/openbiliclaw-issue332-search-hot-20261025` 中的配置、凭据、画像副本、SQLite、原始响应、日志和故障观察脚本已删除。

## 第四阶段：自主加载修复版与后端热重载

用户要求自行加载，也允许换浏览器，并指出现有插件支持热更新。本轮沿用原修复分支，后端 HEAD 为 `1f1d66cc`（相对 `3e706907` 仅新增文档）；没有修改生产代码或构建内容。Ego Browser 因 onboarding 未完成不可用，因此使用 Playwright CLI 和本机已有 Chrome for Testing 运行独立浏览器，不复用或修改用户 Chrome profile。原 `18420` 服务持续运行，隔离 API 使用 `52445`，插件实际 endpoint 与配置 `api.port` 一致；原模型链、画像和准入规则保留。

### 安装产物与重载证据

实际加载目录为 `outputs/issue332-extension-review/`，扩展 ID 为 `oegfehdndnlolhgmbbiibkilonegcndi`、manifest 版本为 `0.3.231`；独立 profile 的浏览器安装记录指向该 worktree 绝对路径，`location=4 / from_webstore=false`。通过该扩展自己的 service worker 读取 `chrome.runtime.getURL("dist/content/douyin.js")` 并计算 SHA-256，结果为 `c56f51d768269efe5baa04995e8e8d845fcb9715c7a0904e63ab531b92791443`，与第三阶段构建一致。没有替换商店安装目录，未发布新版本。

现有 `POST /api/extension/reload` 广播会调用 `chrome.runtime.reload()`，能重读当前注册目录的代码；它不负责把新 worktree 代码部署进商店包。第一次调用确实关闭了旧 worker，但新 worker 在 20 秒内没有出现。独立测试浏览器的扩展页明确提示“开启开发者模式即可使用此扩展程序”，安装记录有 disable reason；这轮不能算成功重连。通过浏览器界面开启开发者模式后，重试同一真实 API，返回 `200 / ok=true / delivered=true`，观察到旧 worker 关闭、新 worker 出现，同一扩展 ID、版本和 `52445` endpoint 保留，并接受新的 background runtime-stream 连接。未修改浏览器的安全实现或用禁用安全检查的启动开关。

### 热榜复测与来源访问边界

| Gate | 适用性 | 状态 | 实测证据 |
|---|---|---|---|
| 修复版加载与实际脚本 | required | PASS | 浏览器安装路径、真实 service worker 身份及扩展资源 SHA-256 一致 |
| 后端触发完整热重载 | required | PASS | 开发者模式开启后，`delivered=true`、旧 worker 退出、新 worker 启动及重连 |
| 非默认端口真实领取 | required | PASS | `hot` 在 `2026-10-25 10:58:51 UTC` 入队并于同秒领取，无手工 claim / 回调 |
| 新构建 12 条热榜取数 | required | FAIL | 2.312 秒返回 0，任务在 `10:58:53 UTC` 终结为 `failed / hot_ui_not_triggered`，诊断含 related API JSON 空响应错误；没有当作正常 empty |
| 独立浏览器的真实源站访问 | required | BLOCKED | 随后同一 profile 导航至相同任务入口，页面标题为“验证码中间页”；已请求用户在该浏览器人工验证，没有自动解题或重试绕过 |
| 新构建 hot 正向评估与出卡 | required | NOT_RUN | 新一轮没有候选，不能进入真实评估 / 文案 / 出卡，也不能拿第三阶段的搜索卡替代 |
| 原服务与行为隔离 | required | PASS | 原服务健康检查 200、原配置 SHA-256 不变；隔离库候选、推荐及 events / seen / profile ledger / saved memberships 均为 0 |
| 后端相关回归 | required | PASS | 再次运行 queue / plugin client / direct strategy：57 passed，退出码 0 |
| 扩展相关回归 | required | PASS | 再次运行 content / dispatcher / fetch-tap 三个 Node 测试文件：113 passed、0 failed，退出码 0 |

后台任务页由 dispatcher 在终态关闭，未取得该已关闭任务页的 DOM。后续人工观察页确实显示验证码，但把前一次 JSON 空响应归因于同一验证码仍是推断；不能把后端复制凭据的 `ready / logged_in=true` 当作这个新浏览器已通过源站验证。观察页保留任务 marker，未执行点赞、收藏、关注、账号初始化或其它上游写操作。

脱敏证据见 [updated-extension-evidence.json](assets/2026-10-25-issue-332/updated-extension-evidence.json)。当前结论仍为 `incremental only`：**修复版自主加载和现有热重载能力已实测通过；热榜较大批次与正向卡依赖独立测试浏览器完成人工源站验证后继续。** 此前“必须由用户手工加载扩展”的安装障碍已经解除。

等待人工验证期间，临时 API 与诊断 worker 已退出，唯一热榜任务为终态；冗余 Cookie 导入文件和原始 API / worker 日志已删除。独立测试浏览器及当前验证页保留，0700 私有临时目录中的 profile、隔离数据与配置供回复后恢复，未写入仓库。原服务持续运行，最终健康检查仍为 200，原配置摘要未变。原商店版、用户 Chrome 页面和原工作区既有文件均未修改。

仅保留脱敏验收 JSON、本地可审阅的扩展测试目录 / ZIP 与三张卡截图。主工作区仍只有开始前已有的 `outputs/`、`promo/` 未跟踪内容。修复与文档在独立分支，未合并、长期部署或发布；Chrome 安装记录仍只有商店包路径，没有本轮测试目录的加载证据。

用户随后明确要求代为完成验证码。通过当前可见页面的正常拖动拼图流程尝试两次，第一次提交后图片更新，第二次提交后仍显示同一验证入口和拼图 iframe，没有观察到放行或源站正文。因此不记为验证成功；未继续反复重试，未调用或修改验证接口。独立测试浏览器的验证页继续保留，临时 API / worker 仍保持退出，热榜取数和正向出卡门槛未改变。
