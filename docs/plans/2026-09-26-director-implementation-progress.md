# 推荐导演实施进度

更新：2026-09-26。工作分支：`feat/jev-decision-provider`。

架构依据：[三模式方案](2026-09-26-director-jev-modes.md)、[基础规格](2026-08-31-recommendation-director-spec.md)。

## 当前交付：三策略、独立过滤与整屏运行时

已完成本轮要求的可运行功能：Jev 两卡递推、Agent 多屏具体预排 + Jev 中间单卡／整对修补、纯 Agent 预排，以及独立 Jev 语义过滤。默认关闭，配置采用 TOML；更改后完整重启后端（主 API、推荐进程、发现 worker）。没有改用户真实配置、调用付费 API、合入 main 或发版。

- 同一 `DirectorRuntime` / `DirectorStore` 承担候选准备、状态版本、幂等、TTL / CAS、原子屏提交与不可变历史；配置换代拒绝旧进程提案，同配置重建保留未来计划。
- `NEXT` 推进屏，`STAY` 保留根方向；真实可见 ACK / click / 明确反馈与生成、预加载分离。复用 canonical event ID，重试不重复写学习事件。
- 三端（插件、桌面、移动）共用客户端协议；CLI 和 OpenClaw 接同一运行时，宿主工具返回不当曝光，需实际展示后明确 ACK。
- 独立过滤只判断明确稳定避雷并保留探索；拒绝仅延期一轮，下轮强制完整 LLM 评估，不承诺永久省掉评估成本。enforce 的人工校准标签不是效果证明。
- 预算覆盖排队／调用，provider 共用限流／熔断，取消与关闭回收资源；Agent-only 关过滤时不创建 Jev client、不读 key。
- `shadow` 展示同 shortlist 的确定性 pair baseline，不是旧 MMR 的严格不干预对照；真实中文效果、误拒率、长期满意度、费用和时延仍需授权实测。

完整使用合同见 [导演模块](../modules/director.md)、[配置](../modules/config.md)、[API](../modules/api.md)。

### 本轮验收记录

本功能验收使用临时 SQLite、合成画像、Fake Agent/Jev 或 MockTransport；浏览器测试使用真实 Chromium，没有真实 Jev / Agent 效果验收。全仓测试开始后的审计修复另跑最终定向集，两个统计不混加。

- 最终树定向 Python 集 **1,340 passed**（85.53 秒）：`tests/test_director_*.py`、`test_pair_*.py`、`test_decision_*.py`、`test_jev_*.py`、`test_semantic_filter.py`、`test_config.py`、`test_llm_concurrency.py`、`test_openclaw_*.py`、`test_recommendation_engine.py`、`test_recommendation_proxy_headers.py`。包含真实 SQLite / API / CLI / OpenClaw 和三端 Chromium 协议用例。
- 安装合同与安装器测试 **21 passed**；`bash -n scripts/install.sh` 通过。安装完成提示仅链接默认关闭的可选配置，不读取 Key 或探测服务。
- `node --test tests/js/*.test.mjs`：**77 passed**，其中共享导演客户端 16 项。扩展全套 `npm test`：**1,497 passed / 1 failed**；唯一失败是下述已复现的聊天旧断言。
- 扩展 `npm run typecheck`、`npm run build:bundle` 通过，Chrome 19 项 manifest / WAR 资源预检通过；构建未发布。
- 全仓 `ruff check src/ tests/`、新文件格式检查、`mypy src/`（**315 source files**）、`git diff --check` 通过。

已知基线例外均在干净 detached worktree **1253a0a8** 再次复现：

1. Python：`tests/test_desktop_web_list_stability_e2e.py::test_pool_refill_event_keeps_loaded_cards_and_scroll_position`，库存数字期望 `70`、实际 `40`（第 331 行）。
2. 扩展：`tests/popup-api.test.ts` 的 `startChatTurn posts durable chat turn metadata`，实现已发送 `streaming: false`，旧断言仍期望该字段不存在。

全仓 Python `pytest -q --disable-warnings --maxfail=12` 完整跑完：**10,088 passed / 65 skipped / 6 failed**，1,368.15 秒。该进程在最终审计修复前启动，不是最终树的一次性全绿结果：其中旧 CLI stub 的两个失败、OpenClaw 反馈 / CLI 各一个失败、Weibo 源码检查失败，已在最终树连同相关用例复跑 **32 passed**。补齐了 JSON CLI 测试对夹杂的 fixture 启动日志的隔离；源码检查在文件冻结后的新进程中通过。剩下的桌面库存断言为上述干净基线问题。

补跑既有 CLI 全文件时，另遇 `test_login_codex_import_uses_source_path` 的 `ConnectError`：该测试仅 mock 凭据导入，未 mock 登录后的联网探测；测试及登录探测路径均未在本分支修改。这项作为额外的非隔离测试失败记录，不将其算成通过。显式排除该项后，`test_cli.py` + `test_director_openclaw.py` + `test_weibo_wiring.py` 为 **289 passed / 1 deselected**。默认功能测试不依赖这条登录路径。

这些统计不是“全仓全绿”；未在导演分支夹带修改桌面库存、聊天协议或登录探测行为。

## 历史切片：离线逐对编排内核（e364b4e2）

以下是该次提交时的历史边界，不代表当前能力；后续待办已由上面的运行时交付覆盖。

已在同一独立 worktree 实现 `PairComposer.prepare_next()`；这次推进的是可回放内核，
**不是生产 shadow 或导演上线**。只有小 Interface 返回 tentative 提案，复杂的可行性、
递推、证据与故障处理留在内核里；没有直接接入旧 `serve()` 或混合历史列表。

- 冻结且版本化的 shortlist、session / state / feedback cursor、方向和约束合同。
  当前合同是离线输入，不冒充完整 SessionStepPolicy、请求幂等或 stream fence。
- 每步一个 Choice 选有序双卡组合，下一步携带已选 prefix；真实反馈不会随草稿增长。
- 精确处理互斥 topic minimum / maximum、canonical 去重、排除和 eligibility 快照。
  每个选项都必须保留整屏可完成性；保留确定性完整 witness，有界提供语义备选。
- 模型拒选、错绑 / 非法证据、超时、迟到、预算耗尽都带原因回退；调用者取消传播，
  包括 adapter 捕捉取消后仍试图返回成功的情况。
- 支持奇数尾部和最大合法短缺数量；硬 minimum 无法满足时明确 UNSATISFIABLE，
  不放松规则、不重复凑数。内部不变量失败抛出错误，不伪装成证明无解。
- 可执行合成回放：`python scripts/replay_director_pairs.py`，演示三次顺序选择与
  中间拒选后的确定性补完；不读真实画像 / 数据库 / 配置，不使用真实凭据或网络。
- shortlist 40、目标 20、每步最多 16 个候选组合、256 次组合探测、24,000 UTF-8
  字节、默认 2 秒整屏模型决策预算均为工程上限，不是质量或性能标定。

详见[内核合同与示例](../modules/director.md)。Agent 多屏草稿、混合修补、独立过滤、
屏提交 / 实际曝光 / 反馈桥和真实中文质量验收仍未完成。四表面推荐行为、配置字段、
依赖和安装流程都不变；未合入 main、未发布。

### 本切片验收

- 四个新增 `test_pair_*` 文件：**205 passed**；director 模块 **99%** 覆盖率
  （384 statements，5 未覆盖）。包括 120 组固定种子的穷举对照、prompt / 输入摘要
  golden、真实 Jev adapter 的 mock HTTP 串联、故障、取消和可执行回放。
- 上述用例与独立 decision / Jev、推荐引擎、PoolCurator、画像边界和 style 回归合跑：
  **578 passed**，32.88 秒。两次统计是重叠集合，不相加。
- 全仓 `ruff check src/ tests/`（另含新脚本）通过；`mypy src/` **306 files** 通过；
  新文件格式检查与 `git diff --check` 通过。
- 本切片未重跑耗时全仓 pytest。下方全仓 9517 / 65 / 1 属于前一 provider 切片，
  不能当作本次最终树的全量结果；已知桌面库存失败未在本切片修改或重新验证。
- 交叉审计发现并修复了“provider 吞掉外部取消仍返回成功”和超大 revision 整数
  序列化失败两个边界，分别加入回归用例。未调用真实 / 付费服务。

## 前一切片：独立 Jev provider（24f34424）

代码与模块验收已完成；全仓回归有一项已在干净基线复现的既有失败。本切片没有启用
任何用户推荐行为，未合入 main、未发布。

- `decision/`：类型化请求 / 证据、原生 HTTP adapter、独立工厂、可回放 fake。
- Choice / Score / Noul 的问题绑定、合法选项、概率、rubric、模型与输入归因。
- 总 deadline、有限重试、取消传播、固定模型漂移拒绝、错误与凭据脱敏。
- `[decision.providers.jev]`：默认关闭、环境变量凭据引用、保存 / 重载和 CLI 展示。
- 不新增依赖，不读取真实 key，不调用付费模型，不写候选、曝光或画像。
- 未实现全局并发 / 熔断、中文质量门槛、过滤业务策略或导演运行时接线。

这是可独立测试的 provider 底座，不表示基础规格的 Phase 0A 已完成，也不表示导演
三模式已可用。主工作区的设计稿保留；实现、配置、测试和文档变更均在独立 worktree。

## 独立 provider 验收记录（前一切片）

所有 Python 命令均在独立 worktree 使用 `PYTHONPATH=src` 与项目现有 `.venv`；没有
安装新依赖，也没有调用真实 Jev 服务。

- 新增五个 decision / Jev 测试文件：**144 passed**，`openbiliclaw.decision` 覆盖率
  **98%**（330 statements，7 未覆盖）。
- 上述五文件加既有 `tests/test_config.py`：**487 passed**。
- 既有 config-show 子集：6 passed；另行检查的桌面测试子集：19 passed。
- `ruff check src/ tests/`：通过；新增模块与测试 `ruff format --check`：通过。
- `mypy src/`：302 source files 通过；`git diff --check` 通过。
- `pytest -q --disable-warnings --maxfail=10` 全仓回归：**9517 passed, 65 skipped,
  1 failed**，耗时约 21 分 44 秒。运行期间补齐的新增用例已由最终定向集覆盖，两个
  统计口径分开记录，不将它们相加称为一次全量运行。

唯一失败：
`tests/test_desktop_web_list_stability_e2e.py::test_pool_refill_event_keeps_loaded_cards_and_scroll_position`，
第 331 行库存数字预期 `70`、实际 `40`。在功能 worktree 单独重跑失败；另建干净的
detached worktree，基于未改动的 **1253a0a8** 单独运行，同一断言也失败。该桌面
HTML/JS 与测试均不在本次改动中；作为既有回归例外留痕，不在 Jev 分支夹带修复。

## 历史规划：后续切片与先后依赖（已按上文推进）

1. **屏与反馈底座**：BatchInstance 身份、生成请求幂等、真实曝光确认、反馈归因与提交
   fencing。现有 GET top-up 混合历史行的问题必须先解决，才能上线“每屏”导演。
2. **过滤 shadow**：在模型预过滤之前冻结对照 cohort，复用 Jev provider；保留现有
   metadata 评估，排除现有 embedding filter 与 Jev 同时 enforce 的混杂。
   这是可与屏底座并行开发的独立接线，但需要自己的证据持久化与样本覆盖门槛。
3. **逐对编排接线**：离线合法 pair 提案、递归 prefix、两卡追加和整屏 topic 配额验证
   已实现；仍需真实供给映射、中文评测与生产 shadow，以及完整无 Plan 的
   SESSION_STEP / SESSION_STAY 合同。先完成屏底座，再接线上提交。
4. **Agent 多屏草稿**：有限候选引用、具体配对与语义意图分离、TTL / 失效、纯 Agent
   路径；模型输出只能作为提案，最终提交仍由程序校验。
5. **混合修补**：草稿批量审查、单卡 / 单对替换、中间修改后的后缀复核、修改预算、
   CAS 和整体重规划；禁止修改已 COMMITTED 的批次。
6. **效果验收与界面**：中文标注与受控实验；明确插件、桌面 Web、移动 Web、CLI 的
   配置与交互合同。未验证前不把 vendor 时延或概率当作产品 SLA / 满意度。

后续实现需要继续版本化 schema 与 fixture，不能把旧规格的单模式 JSON 原样用于
新无 Plan 路径，也不能用 provider 的成功测试代替候选过滤或三模式产品验收。
