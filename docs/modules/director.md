# 推荐导演：三策略与屏级运行时

> 默认关闭。显式启用后接入 API、插件、桌面 Web、移动 Web 与 CLI。真实 Jev / 中文用户效果尚未实测；离线测试不是质量或时延承诺。

## 边界与数据流

```text
候选池 → eligibility + 个性化分数 → topic 分层 shortlist（≤40）
                       ↓
画像视图 + 最近三屏真实经历 → 共享屏目标 / 证据 / 角色策略
                       ↓
              DirectorRuntime / Controller
            ┌──────────┼──────────────┐
     jev_recurrent   agent_jev      agent_only
     二卡递推选择    Agent 多屏草稿   Agent 多屏草稿
                    └── 目标覆盖 / 合法短尾校验 ──┘
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
| `jev_recurrent` | 一次 Choice 联合选择有序二卡组合与软角色，应用追加到 prefix 后再请求下一对；奇数屏允许单卡尾部，不读取 Agent 计划 |
| `agent_jev` | 一次 Agent 调用生成最多三屏具体卡片、brief 和配对角色；Jev 带原 intent / role 与剩余草稿审查，替换中间单卡或整对，重验受影响后缀和跨屏重复 |
| `agent_only` | 执行 Agent 的具体多屏草稿，不调用 Jev；持久化反馈水位证明只有曝光／弱点击新增时可保留计划，偏好反馈或意图／画像变化重排；无权威水位的离线输入遇证据缺口保守重排 |
| 幂等与提交 | request ID、stream、state revision、反馈游标、runtime epoch、generation fence、lease 和计划 CAS；整屏成功或全部不写 |
| NEXT / STAY | NEXT 推进体验屏序号；STAY 保留根屏方向，记录 parent / patch 链，不把换一批误算新章节 |
| 恢复与曝光 | GET 仅恢复精确顺序，不 top-up、不请求模型；提交消费库存但不是曝光，实际可见 ACK 才推进 presentation |
| 真实反馈 | 点击、like / dislike、dismiss→skip、comment→explicit_request 精确关联 recommendation ID / batch；引用原 canonical event，不重复写学习事件 |
| 失败与换代 | 模型故障、非法引用、超时或预算耗尽有因回退，取消传播；配置换代使旧草稿／在飞提交失效，已提交屏仍可恢复 |
| 独立过滤 | `discovery.semantic_filter` 借用独立 decision provider，与导演开关正交；见 [discovery](discovery.md) |
| 画像与短期上下文 | 三策略共用推荐画像、近期关注、最近三屏实际曝光／行为和卡片语义；画像内容变化使旧 Agent 计划失效 |
| 共享屏幕策略 | `policy.py` 统一证据区分、屏任务、内容角色、逐对增量价值和整屏自检指令；简短决策结果入 trace，不保存长篇推理过程 |
| 有界诊断 | `screen_review` 仅报告规范化标题重复，固定 `semantic_quality=not_certified`；不是语义质量认证 |
| 预排覆盖 | Agent 以请求屏数为目标；仅末屏可短，必须说明未用候选并通过剩余容量检查；语义短尾在执行／修补时不自动补满 |

“两个两个”不是两张卡打擂台，也不是一次调用的两个独立 Choice。模型没有本项目的跨请求隐状态；应用显式递推。未来草稿不是用户行为，候选文本不是指令。

会话输入的反馈摘要固定最多 8 条：最多 4 条近期非 `presented` 真实动作与最近 4 条曝光确认。动作槽内保护按状态版本最新的非 `presented / click` 回执；它若被点击挤出最近四条，就替换动作槽中最旧的一条，保持三条最新动作加这条反馈。最后合并按实际发生时间排序，保留原类型、引用、备注与卡片语义，不增加容量或伪造事件。这样重排时模型仍能读到原因，而不只看到程序的偏好水位；保护 `skip` 不代表将其认定为不喜欢。完整事件仍在数据库，窗口是工程容量而非效果阈值；已被计划捕获的动作不因后续正常 ACK 再次触发重排。

### 屏幕编排策略（2026-09-30）

三模式使用同一份静态 `DIRECTOR_POLICY_INSTRUCTIONS`：**区分事实／假设／未知 → 确定本屏任务 → 分配软角色 → 比较每对的增量价值 → 整屏自检 → 根据新证据修订未来草稿**。画像是先验，不能压过当下明确意图；点击只是弱线索、skip 含义不确定、无操作不是不喜欢。负反馈先作用于该内容或用户说明的原因，不自动扩大成整个主题禁令。每张卡应独立有用，不假定用户从左到右阅读或已经学会前屏内容。

`ScreenBrief`（`screen-brief.v1`）记录本屏 `task`、`objective`、最多 8 个已有事件 `evidence_refs`、最多 3 条未知点、4 条软避让项及 `revise_when`。任务限于 `follow_intent / deepen / change_angle / browse / explore / redirect`；配对 `role` 限于 `anchor / complement / exploration / open`，另有简短 `intent`。角色数量只是状态摘要，**没有固定探索比例、强制角色覆盖或新增硬配额**。`revise_when` 是计划的描述性说明，不是模型可编程的运行时触发器。

- **纯 Jev**：程序无模型调用地建立保守 brief；有明确意图则 `follow_intent`，否则 `browse`，不从点击推测心理状态。每步最多 16 个合法 pair × 4 个角色，共 64 个联合选项，加一个 `none_suitable`；仍只调用一次 Choice，而非先选角色再选卡。原始 option ID 表示 `open`，带角色前缀的 ID 表示同一卡对的相应角色。拒选／故障走已有可诊断的确定性回退，不增加调用预算。
- **Agent 预排**：一次调用输出各屏 brief 和配对角色；枚举、字段上限、已有证据引用、合法卡片与跨屏去重都验证后才形成计划。显式输出的 brief 归因 `source=agent`；兼容旧输出时，缺失 brief 用保守视图补齐、缺失角色用 `open`，不能伪称 Agent 做过完整判断。
- **Jev 修补**：同时看到本屏 brief、当前 pair 的 `intent / role`、已选角色、剩余 pair 角色与卡片语义，以保留原职责并避免抢走后续角色所需素材。失效引用单列 `unavailable_card_refs`，不能借用新候选的同名 ref 冒充旧内容。局部替换保留原 role / intent；没有适当替代时可拒选并请求 Agent 重排，角色本身不能放松硬约束。

整屏自检是**同一次模型调用中的指令**：检查重复信息、主线覆盖、独立入口及有依据的探索，最后一对比较选完后的整屏。没有新增独立审查调用，也没有算法保证语义质量。程序侧 `review_screen()` 只对选中标题做 NFKC、空白折叠与大小写归一化后报告完全相同组；它不是语义相似度检测，不自动据此否决整屏，`semantic_quality` 始终为 `not_certified`。

纯 Agent 的生产路径读取 `latest_preference_revision`：Store 在同一事务中从**全部持久化的非 presented / click 回执**读取最新状态版本。水位未超过规划状态时，连续曝光／弱点击即使挤出 4+4 prompt 窗口也可保留计划；新增 like / dislike / skip / 明确要求等其它反馈或状态版本回退则重排。该水位只供程序判断，不发给模型。无权威水位的离线输入则严格要求新增事件完整解释 state revision 增量、且全为 `presented / click`，窗口截断或事件缺口保守重排。**重排不代表把 skip 或点击写成强偏好**，游标也不能单独证明发生了什么。

两种 Agent 模式都在明确意图、画像摘要或 `planning_policy_version` 变化时重排。当前策略版本为 `director-screen-policy.v2`，也纳入 runtime context digest，经既有 epoch / fence 阻止旧策略的在飞提交或后台计划写入；旧计划 JSON 仍可读，旧版本的未来计划不复用，已提交屏不重写。

### 多屏目标与提前结束（2026-10-01）

`planning_contract` 区分「本屏目标」与「整段预排目标」：请求屏数、每屏卡数、合法库存数、物理卡数上界和名义屏长度。`screen_policy` 是每屏重复使用的模板，其中的 `remaining_count` 不是整段计划的剩余预算。物理上界不证明 topic 配额或语义上有足够供给。

一次调用先确定整段可用内容，再组织为完整屏＋可选的一个短尾；当下明确要求贯穿所有未来屏。每屏仍应独立有用，不假定用户读过或掌握前屏，也不能为了凑满第三屏偷偷换到画像里的其他兴趣。屏方向、brief、pair intent 使用简短结果，不反复抄写相同未知点。

- 新模型输出必带 `completion`：满目标用 `target_reached`；不足用 `supply_exhausted`。语义上不适合的未选卡用最多四组 `omissions` 引用说明，原因限 `off_intent / redundant / style_mismatch / insufficient_evidence`。不允许引用未知卡、已选卡或重复引用。
- 所有屏必须非空，只有末屏可不足目标。去掉明确说明的语义遗漏后，短尾若还能合法添加一卡就拒绝；末屏已满但未达到请求屏数，剩余仍能形成合法屏（包括短屏）也拒绝。硬 minimum / maximum 不放松。程序只证明**给定已选前缀无法继续扩展**，不证明全局最优分配，更不认证语义判断。
- `HorizonPlan.planning_completion` 保存初始屏长、请求目标、说明与程序归因：`target_reached / candidate_shortage / residual_constraints / semantic_shortage`。固定 `semantic_quality=not_certified`；这是历史快照，不是当前剩余屏数。消费首屏、重启、ref 重绑定和局部修改不能把历史快照误算成非法计划。旧持久化 JSON 缺字段仍可读为 `None`，但不能以旧输出格式绕过新生成校验。
- 遗漏 refs 属于 `planning_input_digest` 对应的**最初候选命名空间**；修补重绑定后不可用当前 `horizon.cards` 反查其身份或标题。当前没有据此渲染卡片的 UI；后续若要展示，应使用原规划输入记录，而不是将同名 ref 当成同一内容。
- 纯 Agent 与混合模式执行已验证的语义短尾时，以 `ScreenDraft.initial_card_count` 的初始分配数为容量上限，仍复核最新硬约束；不会用硬准入合格但偏题的卡机械补满。初始数独立保存，避免把原本满屏、后来因硬约束缩水的草稿误认成有意短尾。遗漏说明不是全局黑名单、长期负偏好或禁止未来重规划使用这些卡。
- 容量保护不保证替换内容的语义质量：原卡失效时，既有确定性硬修补仍可能选中曾被遗漏的卡，并明确归因为修补、请求重排；它不冒充 Agent 已认证的新编排。
- 前台 trace 区分新规划、缓存计划与不适用，保留安全的规划原因、调用数及初始覆盖；规划失败后兜底不再只剩 `agent_plan_missing`。已验证的语义短尾本身不触发每屏重排；计划耗尽或硬失效仍沿用原触发，物理供给／硬约束不足的短尾也保留既有补货重排行为。

无新配置、数据库列、模型调用或安装依赖；默认 20 秒规划预算和 4096 输出 token 请求保持不变。空候选仍在调用前返回无候选；全候选均被语义拒绝的零屏输出暂不支持，按非法计划显式回退，不伪称成功。

### 推荐画像 + 会话短期 context（2026-09-30）

`CompositionSession.prompt_context()` 是三种模型路径共同的输入投影：

- `profile_context.stable`：有效画像中的兴趣／领域、认知风格、核心特质、价值观、需求、内容深度／时长／节奏偏好、使用场景偏好、探索开放度、平台偏好和全部明确避雷。
- `profile_context.recent`：当前阶段、最新觉察、未被用户否定的洞察及探索假设。保留假设置信度／确认状态；这些不是明确要求，不凭空推断知识水平、观看时长或疲劳。
- `recent_screens`：同一 stream 最近三张**有实际曝光确认或交互**的屏，按提交先后排列。每张保留屏方向；仅保留实际确认曝光或有交互的卡片，携带原位置、标题、主题、风格、来源、曝光时间、近期行为与备注。纯生成／预加载、未触达卡片不算经历。每屏最多读取 80 条近期动作，每卡保留最近 4 条；曝光 ACK 独立读取，不被动作挤掉。窗口容量不代表完整行为历史。
- `real_feedback`：上述最多 4 条动作（保护最新非点击反馈）+ 4 条最新曝光确认，附实际时间及对应卡片语义；内容来自不可变批次，而不是可能已经改动的候选缓存，事件不只剩裸 ID。历史引用不是新候选。
- 原有本次明确意图、本屏方向、已选前缀、合法组合及状态版本仍保留。只看到未操作的曝光不能视为不喜欢，点击不能当喜欢；只有交互而没有 ACK 时标记行为，不虚构 ACK。

所有画像序列化统一走 `soul.profile_views.build_director_profile_view()`，不传自由文本文学人格画像、聊天历史或核心记忆。Agent 慢路径保留最多 32 个兴趣、12 个领域、6 条近期信息；Jev 快路径压到 12／6／3，并省略历史卡摘要但保留标题、主题和行为。明确避雷不截断。`planning_profile_digest` 绑定慢路径视图，下一次请求发现画像变化会重排；历史已提交屏不改写，仍保留最新避雷复查。

Agent 将该投影中的 `profile_context` 提到 user input 顶层首字段，并把 `stable` 放在 `recent` 前；其余易变屏幕状态随后序列化，避免状态摘要提前破坏稳定前缀。system prompt 固定，不重复注入画像；这只是缓存友好的输入顺序，不保证提供商实际命中缓存。

上下文 schema 为 `director-context.v1`／`director-profile.v1`，当前 Jev pair／audit／repair question 为 v3；模型输入上限保持 Jev 64,000、Agent 96,000 UTF-8 字节（工程预算，非 token / 质量标定）。超限明确回退，不能通过删除负偏好冒充成功。没有新增 UI／CLI 参数、依赖或远端调用入口；已有四表面和 OpenClaw 自动使用后端相同上下文。当前不补采 watch duration／完成率，也没有自动长期导演笔记。

## 公共 API

| 接口 | 职责 |
|---|---|
| `PairComposer.prepare_next(request)` | 纯内核：冻结输入、精确配额、合法 pair / role 联合选择、逐对证据校验、tentative 提案 |
| `HorizonPlanner.plan(request, ...)` | 一次有界 LLM 调用产出带 brief / roles / policy version / planning_completion 的 `HorizonPlan`；只接受已有证据和卡片 refs，验证所有屏、跨屏唯一性及提前结束理由 |
| `build_horizon_contract(request, horizon_screens)` | 无模型调用地描述整段目标和物理库存上界，不声称配额可行 |
| `validate_horizon_coverage(request, horizon_screens, screen_refs, stop)` | 验证紧凑屏形、遗漏 refs 与剩余硬可扩展性，产生不可变初始 `PlanningCompletion` |
| `DirectorController.prepare_next(request, strategy, horizon)` | 三策略统一提案；不持久化、不曝光、不偷偷启动规划 |
| `DraftRepairer.repair(request, horizon)` | 携带当前及剩余角色的草稿审查／局部修改，保存 intent、两阶段证据、补丁前后版本和状态绑定 |
| `DirectorRuntime.generate(profile, stream_ref, request_id, intent, ...)` | 准备候选、补齐缺失计划、执行、提交、滚动预排 |
| `DirectorRuntime.restore(stream_ref)` / `aclose()` | 精确恢复；关闭本实例的计划任务和共用 decision provider |
| `DirectorStore.begin/commit` | 短 SQLite 事务，模型调用不占数据库锁；提交前复查库存、已看、scope、特征指纹和版本 |
| `DirectorStore.present/feedback` | 幂等曝光和反馈投影；batch/item 错配拒绝，历史屏反馈不能改写历史屏 |
| `DirectorStore.save_plan/load_plan` | 未提交计划的 TTL / CAS；空计划 tombstone 防止旧任务复活已消费草稿 |
| `DirectorStore.session` | 同一短事务读取状态、4+4 语义反馈、最近三屏经历及完整历史偏好水位，不写曝光、不请求模型 |
| `CompositionSession.prompt_context(fast=False)` | 向 Agent / Jev 提供对应精度的画像和会话经历，历史卡不进入可选候选列表 |
| `build_screen_brief(request)` / `validate_brief(brief, request)` | 无调用保守起点／验证事件引用来源；不证明模型目标正确 |
| `build_policy_context(request, *, brief, ...)` | 三模式共用屏目标、事件证据强度、已选／剩余角色和候选供给摘要 |
| `pair_role_choices(alternatives)` | 把 ≤16 个合法 pair 展开为 ≤64 个 pair / role 选项，不改变硬可行性 |
| `review_screen(cards, refs)` | 仅返回规范化标题重复诊断；不认证语义质量、不产生数据库副作用 |

`requested_strategy` 与 `realized_strategy` 分开；确定性回退不伪装 Agent-only。`SESSION_STEP` / `SESSION_STAY` 不虚构 plan ID；`PLAN_SLOT` / `STAY_PATCH` 绑定真实来源。已提交屏不能原地修补。

### 硬约束与软偏好

内核硬约束覆盖 canonical 去重／排除、合法引用、上游 eligibility、目标数、互斥 topic 的 minimum / maximum。缺货返回最大合法短屏；minimum 无法满足则 UNSATISFIABLE，不放松凑数。生产 shortlist 使用既有准入、发布日期／时效、自内容／已看和明确 topic 避雷；生产路由不凭空发明 topic 配额。同义词合并由上游负责。

节奏、对比、主题弧线和风格是软语义偏好。baseline score 不是点击率，Jev 概率不是已校准满意度或 propensity。确定性 fallback 不是旧 `serve()` 完整 MMR / 视觉 bonus 算法。

## 配置与四表面

字段取值见 [配置](config.md) 和 `config.example.toml`。`director.strategy` 为三策略之一，`director.mode` 独立控制启用／影子运行。只使用 Agent-only 且关闭过滤时无需 Jev 凭据。Agent 使用现有 LLMService，接收推荐画像视图、个性化 shortlist、明确意图和最近经历，不序列化完整 Soul，不注入 personality/core memory。前台规划走交互调用，后台预排走有截止时间的维护调用。

`shadow` 运行并留存导演提案，实际提交同一安全 shortlist 的确定性屏；**不是旧 MMR 的严格不干预实验**，不能据此证明 uplift。`off` 保持旧生产路径。40 张 shortlist、20 张目标、16 个 pair 备选（最多展开 64 个角色联合选项）、256 次探测及上述输入／调用预算都是工程上限，不是实测效果阈值。

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

`python scripts/replay_director_pairs.py` 演示离线递推和拒选回退。测试覆盖原生 parser/mock HTTP、三策略、真实 SQLite、运行时/API、客户端协议；最终结果见 [实施验收](../plans/2026-09-26-director-implementation-progress.md)。本地批次 trace 保存提案、brief、每对 role / intent、`screen_review`、证据、降级原因和来源，可能含有限候选／反馈文字，不是匿名遥测。新增决策字段存在原有 JSON 载荷中，不增加表／列，仅增加偏好水位查询索引。实现阶段未调用付费服务；后续获授权的配置模型试跑遇服务失败，临时 Codex CLI / GPT-6 Luna 则完成了六次合成场景调用。小样本结果不能认证真实用户满意度、Jev 误拒、生产成本或时延。

`scripts/run_director_effect_eval.py` 是独立的开发者评测入口，不是产品 CLI，也不启用导演。必须显式传 `--real`；配置模型路径读取指定项目的现有推荐路由，只在测试进程内固定一个已配置实例、关闭跨模型 fallback，使用临时 memory 与合成画像／候选／历史，不读写真实推荐库存、曝光或画像。v2 默认且仅接受 `screen_policy` 单臂，记录策略版本和初始覆盖快照；旧 prompt 不输出新 completion 合同，因此在联网前拒绝 `legacy_prompt`，避免把格式不兼容误算成质量退步。旧版需使用历史 checkout，当前与冻结的 v1 JSON 只能做描述性前后对照，不是并行 A/B。确定性 pair baseline 不是生产 MMR。`--instance` 只能选择既有推荐链中的实例；`--case`、`--horizon`、`--repeats` 控制测试范围，最多两次重复、每调用最多 60 秒。

```bash
PYTHONPATH=src python scripts/run_director_effect_eval.py --real \
  --config-root /absolute/path/to/configured/project \
  --output artifacts/director-effect-eval/new-run.json \
  --budget-seconds 20
```

脚本保留输入、预注册标签、模型最终正文、调用时延／成功用量和错误类／HTTP 状态；不记录密钥、端点、原始 HTTP 对象、异常正文或模型思维链。记录的是 service 调用，不等于底层 HTTP 次数；失败调用用量可能不可得，不能据此汇总完整费用。服务不可用、超时或迟到时立即停止剩余比较并标记 `stopped_early`，不让上一条件的冷却影响下一条件的效果归因。非法结构不计质量分，不以兜底冒充 Agent 输出。候选高分入门重复项是刻意设置的压力场景，风格元数据也可见；即使跑通，也只说明模型是否使用给定上下文，不能直接推导真实满意度。首次试跑及未完成项目见实施记录。

临时 `--transport codex-cli` 通过 `scripts/director_codex_transport.py` 调用用户已登录的 Codex CLI，固定 `gpt-6-luna` / `low`；可用 `--codex-binary` 明确选择已安装的二进制。不读取 OpenBiliClaw 配置、不构造 HTTP provider／业务 memory，也不增加生产 provider 或 UI 设置。默认 `configured` 路径保留原行为，仍需 `--config-root`；CLI 路径禁止混用 `--instance`。

```bash
PYTHONPATH=src python scripts/run_director_effect_eval.py --real \
  --transport codex-cli \
  --codex-binary /Applications/ChatGPT.app/Contents/Resources/codex \
  --output artifacts/director-effect-eval/luna-new-run.json \
  --budget-seconds 60
```

该 macOS 路径只是此次本机实例，其他环境应指定其实际二进制。适配器使用临时空 cwd、`--ignore-user-config`、`--ephemeral`、只读权限、禁用工具／插件／MCP／浏览及 hooks；生产 JSON 指令通过 `model_instructions_file` 传入，输入走 stdin。CLI 自行使用既有登录并正常维护认证／模型缓存，脚本不复制认证材料、不修改用户配置。取消时终止并回收该 CLI 进程组。

只接收唯一成功 `turn.completed` 且与 `--output-last-message` 文件一致的最终正文；拒绝工具事件、致命 error／turn.failed、非零退出、未知事件和不完整输出。新版 CLI 的恢复性 `item.type=error` 仅计诊断标记，不保存原文；它不能代替成功终态。CLI 不提供本评测所需的 temperature／max_tokens 精确控制，记录明确标为未施加；模型身份记录来自 `--model`，不伪称 JSONL 回传了服务端模型名。时延包含启动／CLI 附加上下文，tokens 是 CLI 报告值，不可与生产 HTTP 成本／延迟混算。实现依据[官方非交互文档](https://learn.chatgpt.com/docs/non-interactive-mode)与[配置文档](https://learn.chatgpt.com/docs/config-file/config-reference)，并以本机 `exec --help` 验证可用参数。

设计来源：[三模式合同](../plans/2026-09-26-director-jev-modes.md)、[原始规格](../plans/2026-08-31-recommendation-director-spec.md)、[Jev 官方研究](../plans/2026-09-26-jev-provider-research.md)。
