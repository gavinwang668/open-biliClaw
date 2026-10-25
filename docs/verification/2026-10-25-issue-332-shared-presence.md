# Issue #332：跨进程扩展在线状态修复验收

- 范围：`capability-increment`，修复已有浏览器任务的共享在线准入；不扩展平台取数、账号权限或 UI。
- 基线：`be8714c2449ba43c4d193cbff4c285fdc16ecf8a`，与 0.3.231 的相关 presence / producer 实现一致。
- 分支：`fix/issue-332-shared-presence`。
- Worktree：`/Users/white/workspace/OpenBiliClaw/.worktrees/issue332-shared-presence`；主工作区既有 `outputs/`、`promo/` 未动。
- Python：共享开发环境的 Python 3.12；每条命令显式设置 `PYTHONPATH="$PWD/src"`，已确认 `openbiliclaw.__file__` 指向该 worktree。
- 隔离：测试数据库、配置和在线状态均位于 pytest 临时目录；未启动或替换用户现有服务、浏览器或扩展，未调用真实模型或变更平台账号。

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

四个产品面继续共用原后端与协议，没有 UI 变更，不要求扩展重装。来源取数、真实账号/LLM 全链路、已安装 Chrome/Firefox、Windows PyInstaller 安装包及 Docker 真机均 **NOT_RUN**；本机进程集成使用 Windows 同款 `spawn`，不能当作这些部署的实机证据。没有依赖、配置或安装流程变更，不需要修改安装器。

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
