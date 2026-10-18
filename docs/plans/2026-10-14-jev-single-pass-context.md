# Jev 完整评估的单轮上下文适配

## 已确认的范围

本次只完善 `discovery.eval_scorer="jev"` 的完整候选评估，不扩展推荐导演或
独立 semantic prefilter。保留用户选择的精确模型，不自动切换，不额外调用摘要
LLM。源画像和候选原文完整保留；输入允许使用可追溯的按需画像和原文摘录，
不承诺摘录后的判断与全文一次输入完全等价。

一条候选的完整题组属于同一个请求。多个候选可以合批，问题并行求值；不能为
适配上下文而拆题多轮求值或分段反复评估同一候选。批次超限时优先减少候选数，
不为凑大批删除关键证据。最小必需信息仍装不下时留为待评估，不伪造零分淘汰。
传输层有限重试与业务多轮评估不同；返回的 usage 不代表失败尝试的完整账单。

## 官方能力与约束

以下事实核实于 2026-10-13。

`POST /v1/systemone` 接受 `model`、`state` 和 `questions`。所有问题共享 state，
彼此独立、并行；一道题的答案不会成为另一题的输入。question ID 仅用于结果
关联，不送入底层模型，所以 instructions 必须明确目标候选和画像路径。
[API](https://docs.typesafe.ai/api) 与 [State](https://docs.typesafe.ai/concepts/state)。

| 限制 | 官方 Jev 1.13 |
| --- | --- |
| 整批输入 | state 加全部 questions 不超过 64k tokens |
| 单题窗口 | 整份 state 加最长 question 不超过 32k tokens |
| Choice | 每题最多 255 个选项 |
| Score | 最多 10 档，至少使用 2 档 |
| question 总数和独立长度 | 未公布独立最大值，仍受组合窗口约束 |
| 输入模态 | 文字和 JSON，不支持原生图像、音频、视频 |

容量来自 [Models](https://docs.typesafe.ai/models)，题型限制来自
[API](https://docs.typesafe.ai/api)。本项目可设置更小运行预算和题数/题长工程护栏，
但不能靠配置扩大官方服务的真实容量。其它精确模型 ID 必须显式登记容量。
公开模型列表不提供可直接用于自动容量发现的字段；未见官方 tokenizer 或
count-tokens 接口。[OpenAPI](https://api.typesafe.ai/openapi.json)。

共享 state 在一个请求中只摄入一次，可以减少重复输入费用。官方 13 题示例是
同一文档、Jev 1.12 的实验，不能把它的节省比例或答案一致性推广为“增加不同
候选仍不影响结果”的保证。[Parallel questions](https://docs.typesafe.ai/cookbooks/parallel_questions)。

官方支持结构化 instructions 和明确的嵌套路径引用；并行结果由代码组合。
路径约束不是候选之间的硬隔离，无关 state 过多可能降低准确率。
[Advanced structure](https://docs.typesafe.ai/primitives/advanced)、
[Fan-out](https://docs.typesafe.ai/patterns/fan-out)、
[已知局限](https://docs.typesafe.ai/model-jaggedness/jev-1.13)。

## 实现契约

1. 配置解析集中维护双容量和工程护栏，不把 UTF-8 字节计量标为精确 token 数。
   请求快照冻结容量与打包策略；配置重载后的新任务使用新设置，不混合新旧结果。
2. 本地整理保留显式负偏好、限定条件、负例和必要来源语境。画像只通过
   `soul/profile_views.py` 的具名视图选择，不新增调用端画像序列化分叉。
3. 单条内容先去重、避免正文在各题反复出现。只有单条请求超限时才按完整原文
   片段选取证据，保留必要的邻接条件、否定和转折；不可安全拆开的片段不截字。
   摘录标识、源摘要和覆盖情况必须可追溯，不能声称没有遗漏语义。
4. 每条候选完整题组作为不可拆的合批单元。所有批次都验证完整请求及最长单题
   窗口，题型选项和人工题数/题长护栏。合批数量同时受质量保护上限约束，不能
   只追求填满窗口。候选选择不依赖同批同行，避免批次变化改写缓存身份。
5. 返回结果必须通过模型、schema、输入摘要、完整题集及原文证据校验后才能
   入池和缓存。超限、缺题、证据冲突、不可确定的压缩结果保留待评估。失败不能
   调用生成式 LLM 补全。容量、词表或压缩策略变化使相关旧缓存失效。

## 验收范围

测试入口覆盖配置保存/读取、`JevContentEvaluator.evaluate()` 和原生 HTTP
请求到候选入池的端到端链路；不增加面向私有函数的测试。

需要覆盖中文长内容、短内容原样通过、否定及例外保留、超长单段待评估、
显式禁忌不可删除、来源不变、每候选只有一套完整题、合批与单条上下文一致、
不同配置预算和模型隔离、题数和题长护栏、无隐式 LLM 调用、失败不入池/缓存。

模拟 HTTP 只能证明协议和链路行为。真实 Jev 验收需要本机环境变量凭据，使用
独立测试数据和临时数据库，不改生产配置或候选池。需分别记录请求是否成功、
实际输入 token、延迟、中文判断和合批串扰；高 confidence 不证明被摘掉的
证据无关，也不能替代质量验收。

## 2026 年 10 月 18 日实现和实测入口

实现位于 `feat/jev-single-pass-context`，基于原有按需画像分支；尚未合并 main。
原生输入合同升为 `jev-full-evaluation-v3`，源片段策略为 `jev-source-spans-v1`，
使旧缓存失效。`candidate_text_view()` 保存原句和源字段/字符位置，按完整片段选择，
不会把摘录写回源正文。公共规则、相同负偏好/场景只传一次。

2026-10-18 官方模型文档新增 Jev 1.14.0，默认仍是 64k/32k；本分支增加该精确 ID
登记，不改变选定模型。官方现在明确无独立题数上限。实验长上下文 header 可扩大
窗口，但会更慢且影响准确率，本实现不发送它，亦不允许仅改数字扩大官方标准容量。
[Models](https://docs.typesafe.ai/models)。

免费额度核查：官方协议支持酌情发放 Promotional Credits，未公开保证新注册赠送
固定额度；Playground 需登录。当前进程和项目配置没有可用 Jev key，无法完成原生
真实请求验证。未注册账号、申请额度或充值。
[官方协议 8.2](https://typesafe.ai/legal/mca)、
[官方余额说明](https://help.typesafe.ai/articles/5505198974-what-s-a-402-error-and-how-can-i-add-more-credits)。

本地预览运行：

```bash
PYTHONPATH=src python scripts/validate_jev_single_pass.py
```

真实运行前在官方控制台确认所在组织的免费余额、有效期和自动充值关闭，再仅在本机
设置 `TYPESAFE_API_KEY`。运行：

```bash
PYTHONPATH=src python scripts/validate_jev_single_pass.py --live --confirm-free-credits
```

脚本只用合成画像/候选和临时 SQLite；比较标准与更小运行预算，每条候选每个测试场景
只属于一个请求，总计最多 6 次 POST，transport 不自动重试，第一次 provider 失败后
停止远端调用。输出安全计数、原文摘要/覆盖、实际模型和 usage；缺 key 或未确认免费
余额时返回 `not_attempted`，不发送网络请求。`preview` 的 UNAVAILABLE 是主动阻止
远端调用，不代表真实服务失败。只有两组真实链路完整完成、原文保留且无 pending 才
报告 `passed`，仍需人工判断中文评分与误拒。

验证记录：2026-10-18，Jev / decision / director / profile 相关回归 1016 项通过，
`test_cli.py -k config_show` 6 项通过；全量 `ruff check src/ tests/` 和 `mypy src/`
通过（322 个源文件）。另补压缩结果不随请求内 ref 长度变化、重复句按原始位置
保留实际邻句的公共入口回归；最终评估/画像/HTTP 核心 83 项复验通过。
本地 runner 两种预算均生成完整题组，源正文保留；显式 live 命令因缺少本机 key
返回 `not_attempted`，没有进行远端推理。未运行整个仓库的 pytest、浏览器交互或
桌面/安装器测试；三端 UI 继续沿用既有评估模式切换，新增细粒度字段仅 TOML/API。
