# 推荐导演：三策略与屏级运行时

> 默认关闭。显式启用后接入 API、插件、桌面 Web、移动 Web 与 CLI。真实 Jev / 中文用户效果尚未实测；离线测试不是质量或时延承诺。

## 边界与数据流

```text
候选池 → eligibility + 个性化分数 → topic 分层 shortlist（≤40）
                       ↓
画像视图 + 最近三屏真实经历 → DirectorRuntime / Controller
            ┌──────────┼──────────────┐
     jev_recurrent   agent_jev      agent_only
     二卡递推选择    Agent 多屏草稿   Agent 多屏草稿
     prefix→下一对  Jev 审查/局部换卡 不调用 Jev
            └──────────┼──────────────┘
                 tentative 屏提案
                       ↓
        最新 eligibility + state/fence/plan CAS
                       ↓
         DirectorStore 原子提交不可变 BatchInstance
                       ↓
      客户端实际可见 ACK / 点击 / 明确反馈 → 会话修订
                       └──────────→ 后续草稿
```

## 已实现能力

| 能力 | 合同 |
|---|---|
| `jev_recurrent` | 每次选中一个有序二卡组合，应用追加到 prefix 后再请求下一对；奇数屏允许单卡尾部，不读取 Agent 计划 |
| `agent_jev` | 一次 Agent 调用生成最多三屏具体卡片、配对和方向；Jev 审查未来草稿，替换中间单卡或整对，重验受影响后缀和跨屏重复 |
| `agent_only` | 执行 Agent 的具体多屏草稿，不调用 Jev；新偏好反馈／意图变化触发重新规划，正常 presentation 不等同偏好改变 |
| 幂等与提交 | request ID、stream、state revision、反馈游标、runtime epoch、generation fence、lease 和计划 CAS；整屏成功或全部不写 |
| NEXT / STAY | NEXT 推进体验屏序号；STAY 保留根屏方向，记录 parent / patch 链，不把换一批误算新章节 |
| 恢复与曝光 | GET 仅恢复精确顺序，不 top-up、不请求模型；提交消费库存但不是曝光，实际可见 ACK 才推进 presentation |
| 真实反馈 | 点击、like / dislike、dismiss→skip、comment→explicit_request 精确关联 recommendation ID / batch；引用原 canonical event，不重复写学习事件 |
| 失败与换代 | 模型故障、非法引用、超时或预算耗尽有因回退，取消传播；配置换代使旧草稿／在飞提交失效，已提交屏仍可恢复 |
| 独立过滤 | `discovery.semantic_filter` 借用独立 decision provider，与导演开关正交；见 [discovery](discovery.md) |
| 画像与短期上下文 | 三策略共用推荐画像、近期关注、最近三屏实际曝光／行为和卡片语义；画像内容变化使旧 Agent 计划失效 |

“两个两个”不是两张卡打擂台，也不是一次调用的两个独立 Choice。模型没有本项目的跨请求隐状态；应用显式递推。未来草稿不是用户行为，候选文本不是指令。

会话输入的反馈摘要固定最多 8 条：最近 4 条非 `presented` 的真实动作与最近 4 条曝光确认，合并后按发生时间排序。这是工程上下文容量，不是效果阈值；完整事件仍保留在数据库。逐卡 ACK 不会挤掉刚刚的 like/dislike；新偏好让纯 Agent 重排，已被计划捕获的动作不因后续正常 ACK 再次触发重排。

### 推荐画像 + 会话短期 context（2026-09-30）

`CompositionSession.prompt_context()` 是三种模型路径共同的输入投影：

- `profile_context.stable`：有效画像中的兴趣／领域、认知风格、核心特质、价值观、需求、内容深度／时长／节奏偏好、使用场景偏好、探索开放度、平台偏好和全部明确避雷。
- `profile_context.recent`：当前阶段、最新觉察、未被用户否定的洞察及探索假设。保留假设置信度／确认状态；这些不是明确要求，不凭空推断知识水平、观看时长或疲劳。
- `recent_screens`：同一 stream 最近三张**有实际曝光确认或交互**的屏，按提交先后排列。每张保留屏方向；仅保留实际确认曝光或有交互的卡片，携带原位置、标题、主题、风格、来源、曝光时间、近期行为与备注。纯生成／预加载、未触达卡片不算经历。每屏最多读取 80 条近期动作，每卡保留最近 4 条；曝光 ACK 独立读取，不被动作挤掉。窗口容量不代表完整行为历史。
- `real_feedback`：上述 4+4 最新事件附时间及对应卡片语义，内容来自不可变批次，而不是可能已经改动的候选缓存；事件不只剩裸 ID。历史引用不是新候选。
- 原有本次明确意图、本屏方向、已选前缀、合法组合及状态版本仍保留。只看到未操作的曝光不能视为不喜欢，点击不能当喜欢；只有交互而没有 ACK 时标记行为，不虚构 ACK。

所有画像序列化统一走 `soul.profile_views.build_director_profile_view()`，不传自由文本文学人格画像、聊天历史或核心记忆。Agent 慢路径保留最多 32 个兴趣、12 个领域、6 条近期信息；Jev 快路径压到 12／6／3，并省略历史卡摘要但保留标题、主题和行为。明确避雷不截断。`planning_profile_digest` 绑定慢路径视图，下一次请求发现画像变化会重排；历史已提交屏不改写，仍保留最新避雷复查。

Agent 将该投影中的 `profile_context` 提到 user input 顶层首字段，并把 `stable` 放在 `recent` 前；其余易变屏幕状态随后序列化，避免状态摘要提前破坏稳定前缀。system prompt 固定，不重复注入画像；这只是缓存友好的输入顺序，不保证提供商实际命中缓存。

上下文 schema 为 `director-context.v1`／`director-profile.v1`，Jev pair／audit／repair question 升为 v2；模型输入上限扩为 Jev 64,000、Agent 96,000 UTF-8 字节（工程预算，非 token / 质量标定）。超限明确回退，不能通过删除负偏好冒充成功。没有新增 UI／CLI 参数、依赖或远端调用入口；已有四表面和 OpenClaw 自动使用后端相同上下文。当前不补采 watch duration／完成率，也没有自动长期导演笔记。

## 公共 API

| 接口 | 职责 |
|---|---|
| `PairComposer.prepare_next(request)` | 纯内核：冻结输入、精确配额、合法 pair 枚举、逐对证据校验、tentative 提案 |
| `HorizonPlanner.plan(request, ...)` | 一次有界 LLM 调用产出 `HorizonPlan`；只接受已有卡片 refs，验证所有屏及跨屏唯一性 |
| `DirectorController.prepare_next(request, strategy, horizon)` | 三策略统一提案；不持久化、不曝光、不偷偷启动规划 |
| `DraftRepairer.repair(request, horizon)` | 草稿审查和局部修改，保存两阶段证据、补丁前后版本和状态绑定 |
| `DirectorRuntime.generate(profile, stream_ref, request_id, intent, ...)` | 准备候选、补齐缺失计划、执行、提交、滚动预排 |
| `DirectorRuntime.restore(stream_ref)` / `aclose()` | 精确恢复；关闭本实例的计划任务和共用 decision provider |
| `DirectorStore.begin/commit` | 短 SQLite 事务，模型调用不占数据库锁；提交前复查库存、已看、scope、特征指纹和版本 |
| `DirectorStore.present/feedback` | 幂等曝光和反馈投影；batch/item 错配拒绝，历史屏反馈不能改写历史屏 |
| `DirectorStore.save_plan/load_plan` | 未提交计划的 TTL / CAS；空计划 tombstone 防止旧任务复活已消费草稿 |
| `DirectorStore.session` | 同一短事务读取状态、4+4 语义反馈和最近三屏经历，不写曝光、不请求模型 |
| `CompositionSession.prompt_context(fast=False)` | 向 Agent / Jev 提供对应精度的画像和会话经历，历史卡不进入可选候选列表 |

`requested_strategy` 与 `realized_strategy` 分开；确定性回退不伪装 Agent-only。`SESSION_STEP` / `SESSION_STAY` 不虚构 plan ID；`PLAN_SLOT` / `STAY_PATCH` 绑定真实来源。已提交屏不能原地修补。

### 硬约束与软偏好

内核硬约束覆盖 canonical 去重／排除、合法引用、上游 eligibility、目标数、互斥 topic 的 minimum / maximum。缺货返回最大合法短屏；minimum 无法满足则 UNSATISFIABLE，不放松凑数。生产 shortlist 使用既有准入、发布日期／时效、自内容／已看和明确 topic 避雷；生产路由不凭空发明 topic 配额。同义词合并由上游负责。

节奏、对比、主题弧线和风格是软语义偏好。baseline score 不是点击率，Jev 概率不是已校准满意度或 propensity。确定性 fallback 不是旧 `serve()` 完整 MMR / 视觉 bonus 算法。

## 配置与四表面

字段取值见 [配置](config.md) 和 `config.example.toml`。`director.strategy` 为三策略之一，`director.mode` 独立控制启用／影子运行。只使用 Agent-only 且关闭过滤时无需 Jev 凭据。Agent 使用现有 LLMService，接收推荐画像视图、个性化 shortlist、明确意图和最近经历，不序列化完整 Soul，不注入 personality/core memory。前台规划走交互调用，后台预排走有截止时间的维护调用。

`shadow` 运行并留存导演提案，实际提交同一安全 shortlist 的确定性屏；**不是旧 MMR 的严格不干预实验**，不能据此证明 uplift。`off` 保持旧生产路径。40 张 shortlist、20 张目标、16 个 pair 备选、256 次探测及上述输入／调用预算都是工程上限，不是实测效果阈值。

```bash
openbiliclaw config-show
openbiliclaw recommend --request-id my-screen-1 --stream-ref cli --limit 10
openbiliclaw recommend --restore --stream-ref cli
openbiliclaw recommend --intent STAY --request-id replacement-1
openbiliclaw recommend --direction '接下来轻松一点，多看自然和旅行'
```

重试复用 request ID 和相同参数；恢复不消费新库存。CLI 成功打印后确认展示。三种 Web 表面共用幂等键／屏身份／实际可见 ACK／反馈关联协议；隐藏页和预加载不确认曝光，布局不变。设置仍用 TOML 和 `config-show`，没有图形模式切换或 Jev 探测按钮。

已提交批次仍不可变；如果最新明确屏蔽使其中任何卡不再允许展示，恢复／幂等回放会抑制整屏并提示需要新生成，而不是修改历史批次、混入新卡或继续展示已禁内容。原屏仍可用于内部审计和反馈归因。

Git / Docker / 桌面包无需新增依赖或下载模型；显式启用外部 Jev 时，将 `TYPESAFE_API_KEY`（或配置的变量名）提供给实际后端进程，不把 key 写入配置／日志。实现不会自动改用户配置、上线或进行付费探测。

## 验证与记录

`python scripts/replay_director_pairs.py` 演示离线递推和拒选回退。测试覆盖原生 parser/mock HTTP、三策略、真实 SQLite、运行时/API、客户端协议；最终结果见 [实施验收](../plans/2026-09-26-director-implementation-progress.md)。本地批次 trace 保存提案、证据、降级原因和来源，可能含有限候选／反馈文字，不是匿名遥测。真实中文质量、满意度、误拒、成本与时延仍需经授权实测；本轮未调用付费服务。

设计来源：[三模式合同](../plans/2026-09-26-director-jev-modes.md)、[原始规格](../plans/2026-08-31-recommendation-director-spec.md)、[Jev 官方研究](../plans/2026-09-26-jev-provider-research.md)。
