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
