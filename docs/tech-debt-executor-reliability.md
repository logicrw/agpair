# Tech Debt: Executor Reliability & Lifecycle

> Source: 2026-04-06 code review by two independent reviewers.
> All HIGH/MEDIUM bugs identified have been fixed and regression-tested in `ab414e3`.
> This document records **forward-looking architectural improvements** that are
> correct in direction but not urgent enough to justify immediate implementation.

---

## TD-1: `attempt_start_head` — commit range 级别的 evidence 校验

**Current state:** `auto_close_evidence_ready_tasks` 用 `--after=task.last_activity_at`
做时间窗口约束，已经能防止旧 attempt 的 commit 误判。

**Improvement:** 在 tasks 表增加 `attempt_start_head` 字段，`apply_retry_dispatch`
和 `mark_acked` 时记录当前 `git rev-parse HEAD`。`detect_committed_task_in_repo`
可以改为 `git log attempt_start_head..HEAD --grep=<task_id>`，比纯时间戳更精确，
不受时钟偏移影响。

**Trigger:** 当出现时间戳不准导致 auto-close 误判的 bug report 时。

**Effort:** S — 加一个 DB 字段 + 改两处调用点。

---

## TD-2: `execution_id` 一等公民

**Current state:** 用 `task_id + attempt_no + session_id` 三元组做唯一标识，
synthetic receipt 的 msg_id 已包含这三个维度。

**Improvement:** 抽出显式的 `execution_id`（UUID），所有 receipt、journal、
state.json、repo evidence 统一引用。好处是解耦 attempt 编号与执行体身份，
为未来竞速执行（同一 attempt 多个 executor 并行）留接口。

**Trigger:** 当需要支持同一 attempt 内多个并行执行体（如 Codex + Gemini 竞速）时。

**Effort:** M — DB schema migration + 全链路 receipt/journal/state 引用改造。

---

## TD-3: 完成信号分层（receipt > repo_evidence）

**Current state:** receipt 优先于 repo_evidence（`skip_task_ids` 机制），
repo_evidence 已被 `--after` + HEAD-only 约束为兜底角色。

**Improvement:** 显式定义信号优先级枚举：
1. Executor structured terminal receipt（权威）
2. Commit SHA from receipt payload（次权威）
3. `repo_evidence` via git log（兜底恢复）

每层有明确的 trust boundary 和 fallback 条件，而不是靠 `skip_task_ids`
的隐式互斥。

**Trigger:** 当引入新的 evidence source（如 CI pipeline callback）时。

**Effort:** S — 主要是逻辑重组，不改 schema。

---

## TD-4: Executor 生命周期显式状态机

**Current state:** `_ensure_process_dead` + `cleanup` 两步推进，状态通过
`state.json` 里的 `termination_signal`、`termination_requested_at`、
`is_process_alive` 等字段隐式表达。

**Improvement:** 收敛为显式枚举状态机：

```
running → terminating → killed → reaped → cleaned
                ↘ permission_denied (需人工处理)
```

每个状态转换有明确的前置条件和副作用。消除 `cancel` 和 `cleanup` 之间的
语义重叠，让 sweep 逻辑可以根据状态直接决策而不用重新探测进程。

**Trigger:** 当引入远程 executor 或需要人工干预流程时。

**Effort:** M — 需要重构 `cancel`/`cleanup`/`sweep` 三条路径，加 enum + 状态转换校验。

---

## TD-5: `attempt_start_head` + 当前 ref/worktree 信息持久化

**Current state:** auto-close 只知道 `repo_path`，不知道任务启动时的分支和 HEAD。

**Improvement:** 在 task 表额外记录 `attempt_start_ref`（分支名）和
`attempt_start_head`（commit SHA）。auto-close 时可以校验：
- commit 在 `attempt_start_head..HEAD` 范围内
- 当前 checkout 的 ref 与任务启动时一致

防止用户中途切分支导致 evidence 对不上。

**Trigger:** 与 TD-1 合并实现。

**Effort:** S — 与 TD-1 同批。

---

## TD-6: Executor 能力矩阵下沉到类型系统/元数据

**Current state:** `task start` / `task retry` 之前的 `review_then_commit` 路径已被移除（仅保留 `direct_commit`）。目前 local CLI 的逻辑判断仍然是 controller 侧的 `is_local_cli_backend(...)` 分支。

**Improvement:** 给 executor 增加声明式 capability，例如：

- `supports_local_retry_dispatch`
- `requires_commit_message_task_id`

这样 CLI、daemon、doctor、docs 都可以读取同一份能力定义，而不是各自
硬编码 backend 特判。

**Trigger:** 当引入第 3 个 CLI executor 时。

**Effort:** M — 需要给 executor metadata 扩字段，并把 CLI/doctor/docs 的判断统一。

---

## TD-7: 用户文档显式暴露 local CLI commit contract

**Current state:** runtime 已经把 `task_id` 和 “commit message 必须包含 task_id”
注入到 local CLI prompt，但 README / usage 文档还没有把这条 contract 说清楚。

**Improvement:** 在 `docs/usage*.md` 和 getting-started 文档里补一段简短说明：

- `codex` / `gemini` backend 的成功判定依赖可验证 commit
- commit message 应包含 task id
- 如果任务只是分析/验证而不提交，应返回失败或切换到适配的 completion policy / executor

这样用户在手写 brief 或做外部集成时，不会以为 “exit 0” 就代表任务成功落地。

**Trigger:** 下次文档更新批次。

**Effort:** S — 纯文档改动。

---

## Review audit trail

| Date | Reviewer | Findings | Status |
|------|----------|----------|--------|
| 2026-04-06 | Reviewer A | 3 bugs (2 HIGH, 1 MEDIUM) | All fixed in `ab414e3` |
| 2026-04-06 | Reviewer B | 4 bugs (3 HIGH, 1 MEDIUM) | All fixed in `ab414e3` |

Regression tests: `tests/integration/test_high_risk_regressions.py` (7 tests).
