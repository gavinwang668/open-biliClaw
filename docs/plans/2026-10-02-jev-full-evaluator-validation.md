# 纯 Jev 完整候选评估：实施与验收记录

日期：2026-10-02。
分支：`feat/jev-full-evaluator`，基于 `fix/director-real-e2e-1002` 的 `a94c1401`。
开发位于独立 worktree；本轮实现未主动修改生产配置、生产数据库或主工作区，没有合回 main。旧 CLI 测试触及本机凭据的隔离边界另见下文。

## 交付范围

- `discovery.eval_scorer="jev"` 是完整评估器选项，与默认 `llm` 互斥，不是又加一层预过滤，也不只覆盖相关性。
- 原生 Score / Choice 组合既有 13 项 metadata。主题 / IP 来自受控词表，无法识别时保留未知；解释是受控模板，不是自由生成。时效证据必须来自输入原文，字段组合冲突整条失败。
- 单条、批量、候选 claim / commit / admission、旧推荐池补评统一接线；API 启动 / 热重建、CLI 与 OpenClaw 共用选择。候选评估无隐式 LLM fallback，推荐表达和搜索词生成仍是独立 LLM 任务。
- 桌面 Web、扩展、移动 Web 的设置页新增 Jev 选项；provider 仍在 TOML / 后端环境配置。页面只展示配置是否就绪，不做远端探测、不接收密钥。没有修改外部 Flutter 项目。
- 仅文本。封面多模态评估、独立 Jev semantic filter 与完整 Jev 评估互斥；不静默丢弃图像。默认仍为 LLM。

`codebase-design` 指导本轮把选择收口在共享 evaluator 边界；`ui-ux-pro-max` 指导沿用原设置表单和原生选择控件，并明确展示实验性、纯文本、未知分类及配置未就绪状态。

## 上下文与失败合同

保留调用方命名 compact 画像、负偏好、负例和既有 prompt-visible 候选文本合同。适配器不再截断这些输入；不等于上游所有平台原始长文均无限制传入。

| 限制 | 默认值 | 行为 |
| --- | --- | --- |
| state＋最长单题的 UTF-8 序列化字节 | 24,000 | 含词表、instructions 和 JSON 包装；超限优先按候选拆批 |
| 完整请求序列化字节 | 48,000 | 同时校验，不能只检查 state |
| 单次 evaluate 的总调用数 / 总预算 | 32 / 30 秒 | 同一绝对截止时间，外层不额外重试重置预算 |
| 单候选或画像自身超限 | 明确 missing | 不发远端请求，不删画像重试，不转 LLM，不缓存失败 |

这些是保守**工程字节护栏，不是 Jev 精确 token 计数**。官方 32k / 64k token 双重限制及未找到公开 tokenizer 的核实见[研究记录](2026-10-02-jev-full-evaluator-research.md)。成功的已验证响应记录模型、候选数、两种字节量及远端实际 input/output token；不记录标题、画像、词表或密钥。aggregate usage 不能反推最长单题的精确 token。

模型固定版本、schema、评分 rubric、词表、预算进入独立缓存命名空间；缓存键覆盖 Jev 实际可见的完整候选对象，含日期标签与 URL。失败保持候选待评估，旧缓存补评失败不覆盖既有数据。

## 已完成验证

Python 命令使用项目 `.venv`，`PYTHONPATH=src`；扩展命令在 `extension/` 运行。

| 验证 | 结果 |
| --- | --- |
| `pytest tests/test_jev_evaluation_config.py tests/test_jev_evaluator_http_pipeline.py tests/test_jev_evaluator_wiring.py tests/test_jev_full_evaluator.py tests/test_director_runtime.py -q` | **121 passed**，超时测试稳定化后的最终复跑 |
| adapter + HTTP 入池测试单独复核 | **61 passed**，包括隐私安全 usage 日志 |
| `pytest tests/test_agent_bootstrap.py tests/test_director_cli.py tests/test_director_cli_recovery.py -q` | **124 passed** |
| `pytest tests/test_cli.py -k config_show -q` | **6 passed** |
| `pytest tests/test_desktop_web_settings_save_e2e.py -k jev -q` | **2 passed**：保存并刷新保留选择、provider 未就绪禁选 |
| 配置、原生 HTTP 流水线及既有 CLI filter wiring | **35 passed** |
| 发现引擎 / pipeline / runtime / Jev 早期联合回归 | **296 passed**；后续新增测试以上述 121 项为准 |
| `ruff check src/ tests/` | 通过 |
| `mypy src/` | **320 个源文件通过** |
| `git diff --check` | 通过 |
| 扩展 `npm run typecheck`、`npm run build:bundle` | 通过，产物不入提交 |
| 扩展 `npm test` | **1497 passed / 1 failed**，已在基线复现，见下文 |
| Playwright CLI 人工检查 | 真实桌面资源，隔离配置 fixture：选择 Jev → 保存 → 刷新后仍选中；截图保留在本机 `output/playwright/jev-evaluator.png`，不入提交 |

原生 HTTP 流水线测试使用真实 `JevDecisionProvider`、有界 provider、完整 evaluator、发现引擎、候选 pipeline 和临时 SQLite，**只有远端响应由 httpx.MockTransport 代替**：

1. 200 成功后完整 metadata 写入候选及内容缓存并准入，无 LLM 调用。
2. 503 只发一次该测试配置允许的 HTTP，请求失败保留 pending，无缓存 / LLM 兜底。
3. 超长中文候选原文落库保留完整，评估前拒绝超预算请求，不调用 HTTP / LLM。

Playwright 检查不是生产后端 / 真实模型端到端验收；配置 API 的字段、保存拒绝与 round-trip 另有 API 自动化测试。fixture 未提供的图标、活动接口与 WebSocket 会产生 404，与新评估选项无关；未把这类 fixture 错误作为生产健康证明。

### 基线问题与全量回归

扩展失败项是 `tests/popup-api.test.ts` 的 `startChatTurn posts durable chat turn metadata`：现有实现多带 `streaming:false`，旧断言未包含此字段。在未修改的基线 worktree `director-real-e2e-1002`、提交 `a94c1401` 上运行同一用例也失败。本轮不修改无关聊天协议。

全量 Python `pytest -q --maxfail=5` 运行超过半小时，进度约 84% 时主动中止以核对已有失败，**未完成、不计作全量通过**。中断后的 pytest fixture teardown 另报 `KeyError`，没有可靠的最终通过数。已定位三个失败项并分别复核：

- `tests/test_desktop_web_list_stability_e2e.py::test_pool_refill_event_keeps_loaded_cards_and_scroll_position`：`#metricPool` 实际 40、预期 70；当前分支与未改基线 `a94c1401` 同样失败。未改无关补货 UI。
- `tests/test_cli.py::test_login_codex_import_uses_source_path`：当前分支和未改基线各复跑一次，均因 `ConnectError` 导致 exit code 1。该旧测试只 mock 凭据导入，没有 mock 导入后的 `_run_codex_probe`，意外进入真实 Codex 网络探测路径；发现隔离缺口后不再执行。不能将此项称为纯离线测试，也不据此认证远端健康。它不涉及 Jev provider。
- `tests/test_jev_full_evaluator.py::test_timeout_is_one_total_deadline_not_per_item_retry`：独立运行通过，但全量负载下同步打包可用完 0.2 秒测试预算，正确的 0 HTTP 次数与测试硬编码的 1 次冲突。已改用模块本地可控单调钟分别验证 timeout 与晚到响应均不得继续后续批次，不修改生产预算；同时去除吞取消测试对预打包 20ms 壁钟的假设，保留真实 asyncio 取消路径。最终 adapter / HTTP 61 项、配置与运行时联合 121 项均通过。

CLI 隔离问题补充：按源码异常边界推断，原样 `ConnectError` 来自初始 OAuth 刷新请求，而不是已捕获一般异常的模型目录 / 能力探测；原输出没有完整内部堆栈。这条旧测试路径可以读取并在刷新成功后保存本机凭据；连接失败分支在保存之前，但本轮没有做文件写入监控或前后指纹，不能声称“已验证真实凭据未改变”。未为调查而打开凭据文件。

## 尚未完成的真实模型验收

当前运行进程未配置 `TYPESAFE_API_KEY`；本轮没有向 Jev 服务发送真实请求，没有读取或记录 Jev 密钥。上述 CLI 基线测试暴露了与 Jev 无关的远端探测隔离缺口，因此不宣称整轮所有测试都无网络访问。未验证 Jev 中文内容质量、入池阈值、误拒率、真实时延 / 成本、服务端上下文拒绝形态或 byte-to-token 误差。现有产品阈值不是 Jev 标定结果。

启用前，由用户在实际后端进程配置 `decision.providers.jev` 及对应环境变量，并关闭封面评估 / 独立 Jev 过滤。随后设置 `discovery.eval_scorer="jev"`；建议先用隔离候选集验证字段一致性、中文评分分布、未知率、失败率及实际 usage，再决定生产切换。不要把密钥发到聊天中。

## 文档和安装范围

同步 README 中英版、架构 / spec、changelog、配置 / CLI / API、发现 / 决策 / 推荐 / 导演、三端 UI 与 Agent Bridge 文档、画像消费者清单。未改依赖声明、安装流程、数据库 schema 或发布入口，因此无需安装器变更；provider 复用已有 HTTP 依赖。未自动启用 Jev、执行 Jev 付费探测、推送分支或发布。
