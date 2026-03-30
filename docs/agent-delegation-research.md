# agpair Agent Delegation 改进：核实后的结论与可执行工作流

> 文档状态：执行规范文档
> 最后核实日期：2026-03-29
> 适用对象：维护者、AI 执行器、AI reviewer、后续接手本项目的人
> 核心目标：让后续 AI 在不重新考古上下文的前提下，能够基于当前事实顺利实施高价值改进，并避免按过时或失真的前提做错事

---

## 0. 如何使用这份文档

这不是一篇“行业综述”或“观点文章”，而是一份**已核实、可执行、面向实施**的工作流文档。

如果你是后续 AI 执行器，请按下面的顺序使用它：

1. 先读 **第 1 节执行摘要**
2. 再读 **第 2 节当前事实基线**
3. 再读 **第 5 节优先级路线图**
4. 开始实施前，严格遵守 **第 6 节统一执行规则**
5. 需要查外部背景时，只使用 **第 8 节已核实来源**

如果这份文档与仓库当前代码冲突：

- **代码行为优先**
- 先更新本文档，再继续实施
- 不要在“文档说了什么”和“代码现在做什么”之间自行脑补折中方案

---

## 1. 执行摘要

### 1.1 一句话结论

`agpair` 曾经最值得做的（并且**现已大部分实现**），不是优先追 A2A / SSE / 通用协议化，而是先把**机械接口做成稳定、结构化、可被其他 AI 可靠消费的执行面**。

### 1.2 已核实的高层判断

1. `agpair` 的核心优势是真实存在的：
   - 本地 SQLite 持久化任务状态
   - receipt 幂等消费
   - 低 token 成本
   - 面向 Antigravity 的垂直链路足够清晰

2. 旧版研究文档方向总体正确，但存在几类问题：
   - 把 `skills/agpair/SKILL.md` 的**操作策略**写成了产品**底层架构**
   - 部分外部协议信息已经过时
   - 几个建议本身成立，但优先级顺序不够经济
   - 几个“缺失能力”其实项目已经部分实现，不应重复设计

3. 对 `agpair` 来说，当前最有价值的改动顺序应该是：
   - **P0：结构化任务输出与结果契约**
   - **P1：发起端幂等与提交结果上下文**
   - **P2：失败诊断结构化与 SQLite 并发准备**
   - **P3：MCP / A2A 等外部集成面**

4. 不要把这些东西当成当前最优先：
   - 直接上 A2A server
   - 直接上 SSE 给 CLI agent 用
   - 并发执行同一 worktree 中的多个任务
   - 把 Rule 5（直接 commit）固化成产品唯一流程

### 1.3 这份文档要达到的效果

后续 AI 应该能基于本文档做到：

- 不误判当前系统真实行为
- 不重复实现已经存在的能力
- 不把低收益的大改动排到前面
- 不在验证阶段只做“看起来像对了”的检查
- 每次交付都能给出明确证据，而不是空泛完成宣言

---

## 2. 当前事实基线（已与仓库实现核对）

本节是整个文档最重要的部分。后续所有设计和优先级都必须以这里为前提。

### 2.1 `agpair` 当前不是“只有 60 秒轮询”

已核实事实：

- `agpair task start`、`continue`、`approve`、`reject`、`retry` 默认都支持 **auto-wait**
- `agpair task wait` 已经存在，并且是独立命令
- 核心 wait 逻辑的默认轮询间隔是 **5 秒**
- `waiters` 表已经存在，用于记录阻塞等待状态，避免多个控制端互相踩踏

关键含义：

- “固定 60 秒轮询”是 `skills/agpair/SKILL.md` 给外部 AI 工具的**操作建议**
- 它不是 `agpair` CLI 或 daemon 的底层产品事实
- 如果未来要优化等待模型，应该修改**产品层行为和接口**，不是继续放大 skill 中的 60 秒规则

仓库依据：

- `agpair/cli/task.py`
- `agpair/cli/wait.py`
- `agpair/storage/schema.sql`

### 2.2 当前状态机和等待语义

当前任务相位：

- `new`
- `acked`
- `evidence_ready`
- `blocked`
- `committed`
- `stuck`
- `abandoned`

当前 wait 语义：

- 默认 terminal phases：`evidence_ready` / `blocked` / `committed` / `stuck` / `abandoned`
- `approve` 的 terminal phase 特殊处理，不把 `evidence_ready` 当完成
- `acked + retry_recommended=true` 时，wait 逻辑会**提前失败退出**，而不是盲等到硬超时

关键含义：

- “长轮询阻塞等待”不是待设计功能，而是**已经存在**
- 真正缺的是：
  - 机器可读的结果格式
  - 结构化错误上下文
  - 更适合外部 AI 消费的输出契约

### 2.3 Watchdog 与恢复责任边界

当前已实现：

- daemon 负责：
  - ingest receipts
  - heartbeat / workspace liveness 采集
  - soft watchdog：标记 `retry_recommended`
  - hard timeout：标记 `stuck`

- companion extension 负责：
  - receipt file 轮询
  - terminal delivery 持久化与 crash-after-send 安全
  - stuck session recovery，最多有限次数重试

关键含义：

- “daemon 会自动恢复 session”是不准确的
- 真正的 session recovery 逻辑在 extension，而不是在 daemon
- 后续任何“增强恢复能力”的设计，都必须明确改动的是哪一层

### 2.4 Receipt 幂等性已经做得不错

当前已实现的接收端幂等：

1. `receipts.message_id` 主键去重
2. `(task_id, delivery_id)` 唯一索引去重
3. companion extension 的 terminal delivery 有稳定 `delivery_id`
4. receipt watcher 具备 crash-after-send 恢复语义

关键含义：

- “接收端幂等性不足”不是当前主要问题
- 当前真正缺的是**发起端幂等**
- 也就是：`task start` 或 retry 发出后，如果调用端超时/崩溃，如何避免重复派发

### 2.5 `blocked` / `stuck` 并不是“完全无诊断信息”

当前已经能看到的诊断信息：

- `task status` 会输出 `stuck_reason`
- `task logs` 会输出 journal 中的事件和 body
- extension 在 continuation / approval 失败时，会主动回写 `BLOCKED`

但当前仍存在的问题：

- 错误信息还不是稳定 schema
- 上层 AI 需要从自由文本中提取语义
- `COMMITTED` 与 `BLOCKED` 的正文格式也没有被约束成版本化协议

结论：

- 问题是“**信息半结构化**”，不是“完全没有信息”

### 2.6 SQLite 当前状态：`busy_timeout` 默认在，但 WAL 没开

已核实事实：

- 通过 Python `sqlite3` 默认连接，`PRAGMA busy_timeout` 为 `5000`
- 当前 `agpair/storage/db.py` 没有主动设置 `PRAGMA journal_mode=WAL`
- 当前 schema 和连接层也没有显式持久化 WAL 模式

关键含义：

- 文档里“必须加 busy_timeout”这个判断不够准确
- 更准确的说法是：
  - **busy_timeout 已有默认值**
  - **WAL 尚未开启**
  - 如果后续要增强并发读取/多控制端稳定性，WAL 仍然是值得做的

### 2.7 Rule 5 是操作策略，不应被误写为产品事实

`skills/agpair/SKILL.md` 当前推荐：

1. 所有 shell 命令加 `timeout`
2. 只做静态 / 语法检查
3. 不跑集成测试，不启动服务
4. `timeout 124` 跳过
5. 完成后直接 commit

这套规则的现实意义：

- 它能降低 Antigravity 执行过程中的挂死概率
- 它也能绕开 `evidence_ready -> approve` 这条链路上的 session 死亡风险

但它不应该被误写成：

- `agpair` 产品层只支持 direct commit
- `approve/reject/evidence_ready` 路径已经不重要
- 后续设计可以默认删掉审批门控

结论：

- Rule 5 目前是**高实用性的操作折中**
- 不是产品层终局架构

---

## 3. 外部生态：只保留与本项目真正相关的事实

本节刻意压缩。只保留会影响 `agpair` 决策的外部事实。

### 3.1 MCP、A2A、AG-UI 的正确定位

- **MCP**：agent ↔ tools / data
- **A2A**：agent ↔ agent
- **AG-UI**：agent ↔ user-facing UI

对 `agpair` 的现实意义：

- `agpair` 当前最像的是一个**专用 handoff transport + local task state layer**
- 它不需要为了“概念完整”立刻同时承担 MCP、A2A、AG-UI 三层职责
- 更现实的路线是：
  - 先把本地 CLI / daemon / extension 接口稳定好
  - 再决定是否对外暴露 MCP tools
  - 最后再评估 A2A 兼容端点是否真的值得

### 3.2 A2A 当前最相关的事实

已核实：

- Google 在 **2025-04-09** 公布 A2A
- A2A v1.0 当前是 Linux Foundation 体系下的公开标准
- 标准 Agent Card 路径是 `/.well-known/agent-card.json`
- A2A TaskState 比旧文档里列出的更丰富，至少包括：
  - `submitted`
  - `working`
  - `completed`
  - `failed`
  - `canceled`
  - `input-required`
  - `rejected`
  - `auth-required`

对 `agpair` 的现实意义：

- A2A 适合作为**未来语义映射层**
- 不适合作为当前第一优先级改造方向
- 最适合现在做的是：在术语和输出字段上避免与 A2A 明显冲突，而不是立刻做完整 A2A 实现

### 3.3 ACP 当前最相关的事实

已核实：

- ACP 官方站现在明确写明：**ACP 已并入 A2A under the Linux Foundation**

对本文档的约束：

- 不再使用“ACP 与 A2A 正在讨论合并”这种过时表述
- 如果后续文档要提 ACP，应明确写成：
  - 历史背景
  - 或 A2A 融合路径的一部分

### 3.4 AG-UI 对 `agpair` 的价值是“低优先级但有方向感”

AG-UI 的价值主要在：

- 可观测性
- frontend streaming
- human-in-the-loop 事件化交互

但 `agpair` 当前主要问题不是“没有一个漂亮的 agent UI 协议”，而是：

- CLI 输出不够机器可读
- commit / blocked 结果契约不稳定
- 上游 AI 难以可靠消费结果

因此 AG-UI 对本项目当前阶段的建议是：

- 作为参考词汇和未来 UI 层方向保留
- 不排入 P0 / P1

---

## 4. 设计原则与决策规则

后续所有改造都必须遵循下面这些原则。

### 4.1 先优化“可被 AI 稳定消费”，再优化“协议看起来先进”

优先级原则：

- 结构化输出 > 更花哨的传输协议
- 可靠结果契约 > 更复杂的 orchestration 概念
- 可验证行为 > paper-style 架构完美性

### 4.2 不把 skill 中的临时策略写死成产品语义

具体要求：

- `skills/agpair/SKILL.md` 可以继续给外部 AI 提供保守操作建议
- 但产品层不得默认：
  - 永远 `--no-wait`
  - 永远 direct commit
  - 永远只允许 60 秒轮询

### 4.3 先做“低风险、高杠杆、可回滚”的改动

优先顺序：

1. 输出契约
2. 幂等
3. 结果上下文
4. 错误上下文
5. SQLite 并发准备
6. 外部集成面

### 4.4 只有在隔离前提成立时，才讨论并发

如果未来讨论并发任务，必须先满足：

1. 不同 repo，或者
2. 同 repo 但不同 git worktree

在没有隔离前提之前，不允许把“同一 worktree 并发多任务”当成可接受方案。

### 4.5 任何状态字段新增，都必须优先面向“自动化判定”

新增字段不要为了“看起来信息更多”，而要为了让上层 AI 能稳定做决定。

好的字段示例：

- `result_schema_version`
- `commit_sha`
- `changed_files`
- `blocker_type`
- `recoverable`
- `recommended_next_action`

差的字段示例：

- 一整段没有结构的自由文本
- 同时混合“现象、原因、建议”的单字符串

---

## 5. 优先级路线图（这是后续实施的主顺序）

本节定义实际执行顺序。后续 AI 不要自行重排，除非仓库现实已经改变。

## P0：把任务结果做成稳定、机器可读的契约

这是当前最高优先级。

### 为什么 P0 最高

`agpair` 目前最大的短板不是不能派任务，而是：

- 能派出去
- 能等到结果
- 但**结果对下一跳 AI 来说仍然不够稳定**

这会直接降低自动化效率，并让后续 AI 在：

- 继续修改
- 选择重试
- 精准跑测试
- 汇报用户

这些环节里反复“猜”。

### [已实现/Landed] WP-P0.1：为 terminal receipts 定义版本化结构
> 状态：✅ 已在仓库中实现 (2026-03-30)

#### Goal

把 `EVIDENCE_PACK` / `BLOCKED` / `COMMITTED` 的正文从“约定俗成文本”升级成“版本化结构化 payload”。

#### Non-goals

- 不要求一夜之间删除所有人类可读文本
- 不要求改成完整 A2A artifact 模型
- 不要求先改传输层

#### Scope

至少覆盖三类 terminal 结果：

- `EVIDENCE_PACK`
- `BLOCKED`
- `COMMITTED`

建议统一 envelope：

```json
{
  "schema_version": "1",
  "status": "COMMITTED",
  "summary": "short human summary",
  "payload": {}
}
```

其中：

- `summary` 保留给人类阅读
- `payload` 保留给程序稳定消费

#### Required changes

- 在 companion extension 中明确要求写入结构化 receipt body
- 在 daemon ingest 时识别 schema version
- 在 CLI 中优先展示结构化字段，必要时回退到原始文本
- 兼容旧版纯文本 receipt

#### Required payload fields

`COMMITTED.payload` 至少应包含：

- `commit_sha`
- `branch`
- `diff_stat`
- `changed_files`
- `validation`
- `residual_risks`

`BLOCKED.payload` 至少应包含：

- `blocker_type`
- `message`
- `recoverable`
- `suggested_action`
- `last_error_excerpt`

`EVIDENCE_PACK.payload` 至少应包含：

- `diff_stat`
- `changed_files`
- `validation`
- `residual_risks`

#### Forbidden shortcuts

- 不要只在 prompt 里“建议”结构化，而不在代码里解析和验证
- 不要只加一段 JSON 示例，却继续把系统行为建立在自由文本解析上
- 不要要求上层 AI 正则提取核心字段

#### Tests

- unit：结构化 receipt 解析
- unit：旧文本 receipt 回退
- integration：daemon ingest `COMMITTED` / `BLOCKED` / `EVIDENCE_PACK`
- integration：CLI 正确展示结构化结果

#### Exit criteria

- 新版结构化 receipt 可被 ingest
- 旧版文本 receipt 不会把系统打挂
- 至少一个 integration test 覆盖每种 terminal 状态

### [已实现/Landed] WP-P0.2：为 `task status` / `task wait` / `task logs` 提供 `--json`
> 状态：✅ 已在仓库中实现 (2026-03-30)

#### Goal

让后续 AI 不需要解析人类排版文本，即可稳定读取状态与结果。

#### Why now

这是把 `agpair` 从“人类可用 CLI”升级为“AI 可可靠集成 CLI”的最低成本动作。

#### Scope

为以下命令增加 `--json`：

- `agpair task status`
- `agpair task wait`
- `agpair task logs`
- 如有必要，也可扩展到 `task list` / `active-waits`

#### Required changes

- 默认文本输出保持不变，兼容现有用户习惯
- `--json` 输出必须稳定、字段顺序可预期、不要混入人类解释性文案
- `task wait --json` 需要返回：
  - `task_id`
  - `phase`
  - `timed_out`
  - `watchdog_triggered`
  - 如果已到 terminal，则附带结构化 terminal payload

#### Forbidden shortcuts

- 不要只把当前文本输出塞进一个 JSON 字段里
- 不要让 `--json` 与默认输出字段不一致

#### Tests

- unit / integration：每个命令的 `--json` 输出
- 回归：默认文本输出不变

#### Exit criteria

- 上层 AI 可仅通过 `--json` 完成状态机判断
- 无需正则或自然语言解析

### [已实现/Landed] WP-P0.3：把 `COMMITTED` 变成下一跳 AI 可直接消费的结果上下文
> 状态：✅ 已在仓库中实现 (2026-03-30)

#### Goal

任务一旦进入 `committed`，主控 AI 应立即知道：

- 提交哈希
- 改了哪些文件
- 做了哪些验证
- 还有哪些 residual risks

#### Scope

实现方式可以有两种，任选其一或组合：

1. receipt payload 中直接携带
2. tasks 表中冗余缓存关键字段

建议组合：

- 以 receipt payload 为 source of truth
- 将核心字段缓存到 `tasks` 表，方便 `status` / `wait` 快速读取

#### Required changes

建议在 `tasks` 表增加：

- `result_schema_version`
- `commit_sha`
- `diff_stat`
- `changed_files_json`
- `validation_json`
- `residual_risks_json`

#### Forbidden shortcuts

- 不要只告诉主控 AI “任务完成了”
- 不要把“请自行 git log -1”当成正常工作流

#### Exit criteria

- `task status --json`
- `task wait --json`

在 `phase=committed` 时都能直接给出完整结果上下文

---

## P1：修复最危险的控制面缺口

## [已实现/Landed] WP-P1.1：发起端 idempotency key
> 状态：✅ 已在仓库中实现 (2026-03-30)

### Goal

解决“任务已经成功派发，但调用方没拿到返回值，重试后重复派发”的问题。

### Why this is high value

当前接收端幂等已较强，但发起端仍可能双发。对于 AI 调用链来说，这是真实风险，不是理论问题。

### Scope

为 `agpair task start` 增加：

- `--idempotency-key`

如果 key 重复：

- 返回同一个 `task_id`
- 不重复创建任务
- 不重复向 `agent-bus` 派发

### Required changes

建议新增持久化字段：

- `client_idempotency_key`

并建立唯一约束。

需要定义：

- key 生效窗口
- 与不同 `repo_path` 的关系
- 对 retry 是否复用相同键

建议规则：

- `task start` 使用 caller-generated key
- `retry` 默认是新 attempt，应使用新 key

### Forbidden shortcuts

- 不要只在内存里做 dedupe
- 不要把“调用方自己别重试”当作设计解决方案

### Exit criteria

- 重复调用同一 key 的 `task start` 不会重复派发
- 有测试覆盖“第一次调用成功、返回丢失、第二次重试”场景

## [已实现/Landed] WP-P1.2：把 `BLOCKED` / `STUCK` 升级为结构化失败契约
> 状态：✅ 已在仓库中实现 (2026-03-30)

### Goal

让主控 AI 在失败后能做出稳定的下一步判断，而不是靠猜。

### Scope

区分以下几类 blocker：

- `session_transport_failure`
- `validation_failure`
- `workspace_conflict`
- `bridge_unavailable`
- `executor_runtime_failure`
- `unknown`

建议输出字段：

- `blocker_type`
- `recoverable`
- `recommended_next_action`
- `last_error_excerpt`
- `details`

### Important note

当前系统已有 `stuck_reason` 和 journal，不是完全没有诊断；本 work package 的目标是**标准化**，不是从零开始发明错误信息。

### Exit criteria

- 后续 AI 仅看 `BLOCKED.payload` 就能区分：
  - 应 `continue`
  - 应 `retry`
  - 应 `abandon`
  - 应提示用户修环境

---

## P2：为未来扩展做底层准备

## [已实现/Landed] WP-P2.1：启用 SQLite WAL，并明确连接约定
> 状态：✅ 已在仓库中实现 (2026-03-30)

### Goal

提高多读单写场景下的稳定性，为后续更多控制端 / 更密集状态读取做准备。

### Why not P0

- 它重要
- 但不直接解决当前“别的 AI 很难稳定消费输出”的核心问题

### Scope

- 在 DB 初始化时设置 `journal_mode=WAL`
- 明确连接层行为
- 如有必要，显式设置 `busy_timeout`

### Required changes

- `ensure_database()` 中加入 pragma 初始化
- 考虑迁移老数据库
- 记录并验证 WAL 实际启用成功

### Forbidden shortcuts

- 不要只在 README 写“建议开启 WAL”
- 不要假设所有 SQLite 环境都会自动持久化同样的 pragma 结果

### Exit criteria

- 新库默认 WAL
- 老库可迁移到 WAL
- 有测试或可重复验证步骤证明 WAL 已生效

## [已实现/Landed] WP-P2.2：并发准备，但不立刻开放“同 worktree 并发”
> 状态：✅ 已通过 doctor 并发策略实现 (2026-03-30)

### Goal

为未来并发打地基，但不在当前阶段引入高风险并发行为。

### Scope

本阶段只做：

- 文档和状态层准备
- worktree-based isolation 方案设计
- CLI / daemon 对“并发任务”的约束声明

本阶段不做：

- 默认开放同 repo 同 worktree 并发编辑

### Exit criteria

- 文档明确并发前提
- 后续工作可以在不同 repo 或 worktree 下安全推进

---

## P3：外部集成面与生态兼容

这些工作有价值，但属于后置收益。

## [已实现/Landed] WP-P3.1：MCP wrapper
> 状态：✅ 已在仓库中实现 (2026-03-30)

### Goal

把 `agpair` 的核心操作封装成 MCP tools，降低与其他 agent runtime 的接入成本。

### Why this is earlier than A2A

- 实现更轻
- 对本项目当前形态更贴近
- 立刻能服务“别的 AI 想用 `agpair`”这一场景

### Scope

建议最小工具集：

- `agpair_start_task`
- `agpair_get_task`
- `agpair_wait_task`
- `agpair_get_logs`
- `agpair_continue_task`
- `agpair_approve_task`
- `agpair_retry_task`

### Exit criteria

- 任何 MCP client 能稳定发起并读取任务
- 不依赖解析人类文本 CLI 输出

## [部分实现/Landed] WP-P3.2：A2A 语义对齐或适配层
> 状态：✅ 已部分实现，CLI 输出了基于 A2A 的状态提示 (a2a_state_hint) (2026-03-30)

### Goal

让 `agpair` 在术语和未来适配路径上与 A2A 保持兼容，而不是现在就完整实现 A2A。

### Current recommendation

先做下面这些轻量动作：

- 在文档中标注状态映射
- 结果 payload 字段避免与 A2A 明显冲突
- 如未来需要，再单独做 `agent-card.json`、task endpoint、stream / push 支持

### Do not do now

- 不要在 P0 / P1 阶段把完整 A2A server 当主线
- 不要为了“标准感”牺牲当前本地 CLI 体验和交付节奏

---

## 6. 统一执行规则（后续 AI 必须遵守）

本节是给实施者的工作合同。

对于每一个 work package，都必须遵守以下流程。

### 6.1 实施前必做

1. 阅读相关源码，而不是只看本文档
2. 先确认当前测试覆盖范围
3. 确认这次改动属于哪个 work package
4. 不跨 package 混做无关重构

### 6.2 统一任务简报模板

每个 work package 实施前，都应先写清楚：

- `Goal`
- `Non-goals`
- `Scope`
- `Invariants`
- `Required changes`
- `Forbidden shortcuts`
- `Required evidence`
- `Exit criteria`

如果执行器没有先形成这 8 项，就不应开始改代码。

### 6.3 验证顺序

优先验证顺序：

1. focused unit / integration tests
2. changed-area lint / typecheck
3. 关键运行路径验证
4. 需要时再跑更大范围测试

不要只做这些就宣称完成：

- 代码看起来没问题
- 单纯 `pytest` 或 `tsc` 过了
- 只读源码推断行为正确

### 6.4 证据包最低要求

每次交付至少要提供：

- `git diff --stat`
- 关键改动文件列表
- 跑过的验证命令
- 每个命令的结果摘要
- 至少一个“这次改动真实生效”的行为证据

如果改了以下内容，还要追加证据：

- receipt schema：旧版兼容证据
- `--json` 输出：稳定字段示例
- idempotency：重复调用不双发的测试或演示
- WAL：数据库 pragma 生效证明

### 6.5 文档和代码冲突时怎么处理

如果实施过程中发现本文档与代码冲突：

1. 先确认代码当前事实
2. 修改本文档对应章节
3. 在提交说明里写明“修正文档前提”
4. 再继续后续实现

不要无声跳过。

---

## 7. 明确禁止事项

以下事项在当前阶段明确禁止：

1. 不要把 60 秒轮询写入产品层默认行为
2. 不要把 Rule 5 变成唯一官方流程
3. 不要默认删除 `evidence_ready -> approve` 审批路径
4. 不要在没有 worktree 隔离的前提下启用同 repo 并发编辑
5. 不要把 A2A / SSE / webhook 当作当前最优先交付
6. 不要新增只能被人类阅读、不能被程序稳定解析的核心输出字段
7. 不要在没有兼容策略的情况下破坏旧 receipt 文本流
8. 不要把“文档中的二手文章”当成高置信实施依据

---

## 8. 来源使用规则与已核实来源

### 8.1 来源分级

后续实施只允许按这个优先级使用来源：

1. **本仓库代码与测试**
2. **官方规范 / 官方文档 / 官方仓库**
3. **高质量一手实现文档**
4. **社区文章 / 博客 / 第三方分析**

如果 4 与 1/2/3 冲突，直接忽略 4。

### 8.2 与本项目最相关的高置信来源

#### 本地源码

- `agpair/cli/task.py`
- `agpair/cli/wait.py`
- `agpair/storage/db.py`
- `agpair/storage/tasks.py`
- `agpair/storage/receipts.py`
- `agpair/daemon/loop.py`
- `companion-extension/src/services/delegationReceiptWatcher.ts`
- `companion-extension/src/services/agentBusDelegationService.ts`
- `skills/agpair/SKILL.md`

#### 官方 / 一手外部来源

- A2A v1.0 specification:
  - <https://a2a-protocol.org/latest/specification/>
- A2A v1.0 announcement:
  - <https://a2a-protocol.org/latest/announcing-1.0/>
- Google A2A announcement, 2025-04-09:
  - <https://developers.googleblog.com/en/a2a-a-new-era-of-agent-interoperability/>
- ACP official site:
  - <https://agentcommunicationprotocol.dev/>
- MCP official docs:
  - <https://modelcontextprotocol.io/docs/getting-started/intro>
  - <https://modelcontextprotocol.io/docs/learn/architecture>
- AG-UI official docs:
  - <https://docs.ag-ui.com/introduction>
  - <https://docs.ag-ui.com/concepts/events>
- Claude Code subagents:
  - <https://code.claude.com/docs/en/sub-agents>
- Claude Code agent teams:
  - <https://code.claude.com/docs/en/agent-teams>
- AutoGen handoffs:
  - <https://microsoft.github.io/autogen/stable/user-guide/core-user-guide/design-patterns/handoffs.html>
- LangGraph:
  - <https://www.langchain.com/langgraph>

### 8.3 已确认需要替换或降级使用的旧来源

以下来源不是不能看，而是不应再作为高置信主依据：

- `https://a2aprotocol.ai/docs/guide/a2a-mcp-ag-ui`
  - 可读，但属于二手整理，不是官方标准文本
- `https://shashikantjagtap.net/openclaw-acp-what-coding-agent-users-need-to-know-about-protocol-gaps/`
  - 可读，但属于第三方评论
- `https://shipyard.build/blog/claude-code-multi-agent/`
  - 可读，但不应覆盖官方 Claude Code 文档

### 8.4 已发现的失效或迁移链接

需要更新引用的典型例子：

- `https://github.com/sst/opencode/issues/3023`
  - 旧地址，当前应使用 `anomalyco/opencode`
- `https://github.com/sst/opencode/issues/5887`
  - 旧地址，当前应使用 `anomalyco/opencode`
- `https://github.com/google-a2a/A2A`
  - 当前跳转到 `a2aproject/A2A`
- `https://github.com/anthropics/anthropic-quickstarts/tree/main/autonomous-coding`
  - 当前跳转到 `anthropics/claude-quickstarts`

---

## 9. 状态映射（仅作未来兼容参考，不构成当前实施要求）

当前 `agpair` 状态与 A2A 语义的近似映射：

| agpair | 近似 A2A 语义 | 备注 |
|--------|---------------|------|
| `new` | `submitted` 前后阶段 | `agpair` 更偏本地创建状态 |
| `acked` | `working` | 已被 executor 接受 |
| `evidence_ready` | `input-required` | 需要主控方继续决策 |
| `committed` | `completed` | 已成功完成并提交 |
| `blocked` | `failed` / `rejected` | 需结合 blocker_type 才能细分 |
| `stuck` | 无直接一一对应 | 更像本地 watchdog 异常态 |
| `abandoned` | `canceled` | 本地放弃任务 |

使用原则：

- 可以作为未来术语兼容参考
- 不要为了映射漂亮而强行扭曲现有状态机

---

## 10. 最终判断

### 10.1 对旧版研究结论的最终评价

旧版结论可以保留的核心判断：

- `agpair` 作为 Antigravity 专用 handoff 管道，工程价值是真实存在的
- 它的长期天花板不在“能不能派任务”，而在“是否要变成更通用的标准入口”

旧版结论需要修正的关键点：

- 不应把 60 秒 polling 写成产品底层事实
- 不应把 daemon 写成 session recovery 主体
- 不应把 BLOCKED 诊断说成几乎不存在
- 不应把 Rule 5 写成终局架构
- 不应把 A2A / SSE 排在结构化输出之前

### 10.2 当前最值得执行的改进顺序

最终推荐顺序：

1. **P0：结构化 terminal receipts + `--json` 输出**
2. **P1：发起端 idempotency + committed result context**
3. **P1：结构化 blocked / stuck 失败契约**
4. **P2：WAL 与并发准备**
5. **P3：MCP wrapper**
6. **P3：A2A 语义对齐 / 适配层**

### 10.3 给后续 AI 的最后一句话

如果你只能记住一件事，请记住：

> 对 `agpair` 来说，当前最有价值的不是“再发明一个更完整的 agent protocol”，而是把已有机械链路的输出、错误、结果和重试语义做成**稳定、结构化、可验证、可被下一跳 AI 直接消费**的接口。

