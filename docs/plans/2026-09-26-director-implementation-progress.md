# 推荐导演实施进度

更新：2026-09-26。工作分支：`feat/jev-decision-provider`。

架构依据：[三模式方案](2026-09-26-director-jev-modes.md)、[基础规格](2026-08-31-recommendation-director-spec.md)。

## 当前切片：独立 Jev provider

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

## 验收记录

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

## 后续切片与先后依赖

1. **屏与反馈底座**：BatchInstance 身份、生成请求幂等、真实曝光确认、反馈归因与提交
   fencing。现有 GET top-up 混合历史行的问题必须先解决，才能上线“每屏”导演。
2. **过滤 shadow**：在模型预过滤之前冻结对照 cohort，复用 Jev provider；保留现有
   metadata 评估，排除现有 embedding filter 与 Jev 同时 enforce 的混杂。
   这是可与屏底座并行开发的独立接线，但需要自己的证据持久化与样本覆盖门槛。
3. **逐对编排内核**：合法 pair 提案、递归 prefix、两卡追加、整屏约束验证、无 Plan 的
   SESSION_STEP / SESSION_STAY 合同；先离线与 shadow，再接提交底座。
4. **Agent 多屏草稿**：有限候选引用、具体配对与语义意图分离、TTL / 失效、纯 Agent
   路径；模型输出只能作为提案，最终提交仍由程序校验。
5. **混合修补**：草稿批量审查、单卡 / 单对替换、中间修改后的后缀复核、修改预算、
   CAS 和整体重规划；禁止修改已 COMMITTED 的批次。
6. **效果验收与界面**：中文标注与受控实验；明确插件、桌面 Web、移动 Web、CLI 的
   配置与交互合同。未验证前不把 vendor 时延或概率当作产品 SLA / 满意度。

后续实现需要继续版本化 schema 与 fixture，不能把旧规格的单模式 JSON 原样用于
新无 Plan 路径，也不能用 provider 的成功测试代替候选过滤或三模式产品验收。
