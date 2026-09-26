# 推荐导演：离线逐对编排内核

> `src/openbiliclaw/recommendation/director/` 是可显式调用的开发者能力，
> **不是已上线的推荐模式**。当前只准备 tentative 整屏提案，不读取或修改候选池、
> 推荐历史、曝光账本或用户画像；现有 `serve()`、API 与 runtime 均未接入。

## 模块边界

```text
调用者冻结 shortlist + 真实反馈摘要 + 方向 + topic quota
  → PairComposer.prepare_next
  → 确定性可行性检查 / 有界合法 pair alternatives
  → 可选 DecisionProvider（选择 pair_id 或 none_suitable）
  → 校验证据 → 追加两张到 tentative prefix → 下一次请求
  → 整屏复核 → ScreenProposal(tentative=true) + 逐步 trace

真实反馈摘要 ── 本次调用内不被 prefix 改写
生产 serve / 过滤 / BatchInstance commit / presentation ── 尚未接线
```

“两个两个”指每次选中一组有序的两张卡，再把它们追加到同一屏的前缀，
不是拿两张卡作胜负比较。下一次决策看到增长后的前缀；模型没有跨请求隐状态，
也不会自动把同一次调用的多个独立 Choice 串成递推步骤。奇数目标的最后一步
允许单张尾部，不能把普通二卡选择降成两个独立的单卡选择。

## 已实现范围

| 能力 | 边界 |
|---|---|
| 冻结请求合同 | 明确 session / stream / state revision / feedback cursor、候选版本、方向、特征 schema 与约束版本；不授予提交权限 |
| 有界 shortlist | 最多 40 张，目标 1–20 张；输入超限直接拒绝，不暗中截取高分头部 |
| Canonical 去重 | 候选 `ref` 和 `item_key` 分别唯一；排除集与已选集合按 canonical identity 处理 |
| 精确配额 | 对每张卡唯一的 `topic_group` 执行 minimum / maximum；供给不足明确返回，不静默放松 |
| 有界 pair 提议 | 每步最多 16 个候选 pair，加 `none_suitable` 退出选项；不让模型创造候选身份 |
| 递推决策与降级 | 可注入独立 `DecisionProvider`；未注入时走确定性策略。服务失败、预算耗尽或无合适选项保留原因并降级 |
| 可回放 trace | 保存每步前缀、备选项、实际选择、decision / deterministic 来源以及可用的请求、输入摘要和证据 |
| Tentative 整屏结果 | `COMPLETE` / `SHORTAGE` / `UNSATISFIABLE` 都是离线提案状态，不是生产提交或曝光状态 |

40 / 20 / 16、最多 10 次 provider 调用、默认 2 秒整屏决策预算，以及字段长度上限，
都是限制计算和外部请求规模的**工程边界**，不是经过用户效果实验校准的质量阈值，
也不表示真实 Jev 服务能在该预算内完成。调用者须先提供有代表性的 shortlist；
本模块没有检索、召回覆盖率或 token 精确计量能力。

每个备选 pair 都通过“选后仍可补完整屏”的精确可行性检查；第一项始终来自完整的
确定性 completion witness，其余来自最多 256 次探测的 topic 分层扫描。备选枚举不
穷举所有排列，也不宣称全局效用最优，但不会因为枚举截断而把一个可行屏误判成短缺。
确定性排序按 `baseline_score` 降序、`ref` 升序，重放不依赖输入列表的偶然顺序。

## 公共合同

| 接口 | 内容 |
|---|---|
| `PairComposer(provider=None)` | 注入可选 `DecisionProvider`；调用者负责 provider 的生命周期，composer 不关闭借用的 provider |
| `await composer.prepare_next(request)` | 重新冻结并校验 `CompositionRequest`，返回 tentative `ScreenProposal`；取消直接传播，不伪装成成功结果 |
| `CompositionCard` | request-local `ref`、canonical `item_key`、有界标题 / 摘要、唯一 `topic_group`、风格 / 平台、baseline score 和 upstream `eligible` |
| `TopicQuota` / `ScreenConstraints` | 互不重复的 topic minimum / maximum、canonical 排除集与约束版本 |
| `RealFeedback` | 调用者提供的 event / batch 引用与动作摘要；只做归因，不证明真实曝光 |
| `CompositionSession` | 冻结的会话状态、反馈游标、配置版本、显式意图与最多 8 条真实反馈摘要 |
| `CompositionRequest` | 版本化的完整输入快照、目标数量、整屏预算及可选 absolute monotonic deadline |
| `PairAlternative` / `PairTrace` | 有序二卡或最后的单卡尾部；完整保留逐步选择的前缀和归因 |
| `ScreenProposal` | 提案状态、选中 refs、pair traces、输入 / 候选 / 约束摘要、调用次数及实际策略 |

输入摘要覆盖语义快照和预算，但不包含 `request_id` / absolute deadline。
它便于重放对比，不是持久幂等键、跨模型缓存授权或生产 fencing token。
`requested_strategy="jev_recurrent"` 与 `realized_strategy` 分开；实际结果可能是
`jev_recurrent`、`deterministic`、`mixed` 或 `none`。确定性 fallback **不是**
`agent_only`，它没有执行 Agent 的多屏提前编排。

### 调用与验证边界

开发者显式调用 `PairComposer.prepare_next()`，输入上述快照，取得 `ScreenProposal`。
使用 `provider=None` 或 [ScriptedDecisionProvider](decision.md#公共-api) 可以做
不联网的确定性 / fixture 回放；注入真实 Jev adapter 才可能产生外部请求和费用。
关闭 `[decision.providers.jev]` 不会自动执行 composer，开启也不会启动生产路由。

最小不联网例子（合成卡片，非真实曝光或模型质量评测）：

```python
import asyncio

from openbiliclaw.recommendation.director import (
    CompositionCard, CompositionRequest, CompositionSession,
    PairComposer, ScreenConstraints, TopicQuota,
)

request = CompositionRequest(
    request_id="fixture-screen-1",
    session=CompositionSession(
        session_ref="fixture-session", stream_ref="fixture-stream",
        state_revision=0, feedback_cursor="fixture-no-feedback",
        configuration_revision=0,
    ),
    candidate_set_ref="fixture-candidates", candidate_revision=0,
    feature_schema_version="fixture-features-v1",
    direction_ref="fixture-direction", direction="技术之后放一张轻松的内容",
    cards=(
        CompositionCard(ref="a", item_key="bilibili:fixture-a",
                        title="理解一个技术原理", topic_group="技术"),
        CompositionCard(ref="b", item_key="web:fixture-b",
                        title="山间散步", topic_group="自然"),
    ),
    constraints=ScreenConstraints(
        version="fixture-quotas-v1",
        topic_quotas=(TopicQuota(topic_group="自然", minimum=1, maximum=1),),
    ),
    target_count=2,
)
proposal = asyncio.run(PairComposer(provider=None).prepare_next(request))
assert proposal.tentative and proposal.realized_strategy == "deterministic"
print(proposal.status, proposal.card_refs)  # COMPLETE ('a', 'b')
```

这个例子只检验程序配额与确定性路径；没有 provider 时，方向文本不会自行变成语义规划。
注入 `ScriptedDecisionProvider` 才能按脚本走决策证据路径；fixture 成功不等于真实 Jev
理解了推荐叙事。

仓库还提供可直接执行的双场景回放：

```bash
python scripts/replay_director_pairs.py
```

输出合成卡片的三对序列、递增长度的 prefix、保持不变的反馈事件数，以及中间一次
`none_suitable` 后的回退归因。脚本复用原生响应 fake 与生产 parser，不联网、不读
真实配置 / 数据库 / 画像；one-hot 概率和 0 token 用量都是 fixture 值，不是实测数据。

`COMPLETE` 表示达成原请求数量；`SHORTAGE` 返回原目标以内满足所有硬配额的最大
可行数量（允许为 0），可能由供给、排除或 maximum 限制造成。`UNSATISFIABLE`
表示没有任何不超过目标的数量能满足 minimum / maximum，此时没有选中 refs。
数量缩减不降低 minimum；奇数最终数量只在尾部产生 singleton。Provider 返回
`none_suitable`、服务错误、无效 / 错配证据、超时、迟到或预算耗尽时，程序使用当前
完整可行 witness 的首 pair 并在 trace 留因，而不是把它当作用户不喜欢这些内容。
24,000 UTF-8 字节输入上限只控制外部请求规模，超限同样确定性回退；预算耗尽后
本地确定性补完仍可继续，因此默认 2 秒不是整个函数的严格完成时限。

每次请求里，候选标题 / 摘要、方向文本和反馈描述都属于数据；候选文本不获得工具、
数据库或业务规则修改权限。结构化选项和程序校验限制输出范围，但不意味着文本诱导
风险或中文理解质量已经得到验证。Trace 中的 evidence JSON 包含选项答案与模型归因，调用者
应按开发数据管理，不能把它当成可无条件上传的匿名遥测。

## 硬约束与质量偏好的区别

本阶段硬约束只覆盖：快照内的 `eligible`、canonical 排除和去重、合法选项绑定、
目标数量，以及**精确字符串匹配的互斥 topic 配额**。Topic alias / 同义词合并必须
由上游在构造快照前完成；不能据此声称“AI”与“人工智能”已被视作同一主题。
`content_style`、标题语义、节奏、叙事和卡片搭配是决策信息，不是已校验的硬约束。
单条 baseline score 不是点击率，provider 的概率也不是生产曝光策略的 propensity。

`eligible=True` 只是调用者的快照断言。Composer 不读数据库、不重新拉取来源，
不验证实时库存、URL 可用性、时效、最新屏蔽、已看状态或跨请求并发消费。
原生产排序器的 MMR / style / topic 放宽逻辑与本内核分开，不能把两者的规则
或线上效果混为一谈。

## 后续生产接线前必须补齐

- BatchInstance、请求幂等、stream / generation fencing、取消和状态更新归属。
- 最新 eligibility 复核与整屏 all-or-nothing 提交；已提交屏不可修改。
- 客户端真实 presentation ACK、精确到屏的反馈归因；生成前缀不能冒充行为反馈。
- Agent 多屏草稿、Agent-only 模式、Jev 中途换卡 / 换 pair 与受影响后缀重验。
- 独立 Jev 过滤、生产 shadow / enforce、覆盖率与真实质量评估、runtime 并发及成本治理。

当前 `recommendations` 历史行与旧 GET top-up 可以混合多次生成结果；现有写入也可能
因最终 eligibility 复核只保留部分候选。因此不能直接把这些旧接口当作新的整屏提交
或真实曝光协议。

本阶段不新增配置字段、CLI 命令、API 路由、安装依赖或后台任务。插件 popup / side panel、
桌面 Web、移动 Web 与 CLI 的推荐行为均不变，四个表面都没有启用导演的入口。
Git / Docker / 桌面安装流程不变。完整设计与剩余工作见
[三模式增补](../plans/2026-09-26-director-jev-modes.md)及
[实施进度](../plans/2026-09-26-director-implementation-progress.md)。
