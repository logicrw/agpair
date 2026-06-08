# AGPair Practical External-First V2.2 改造计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 AGPair 在真实 Codex / Claude Code 使用中可靠承担 external-agent-first 外派层：外部 agent 默认像用户在终端里正常调用一样拥有完整能力，主控负责派发、低噪等待、验收、重试和回退。

**Architecture:** AGPair 不做 MCP 架构、不做默认 capability bundle、不做默认 isolation/bare/restricted 模式。所有 active 外部 executor 默认 `managed-natural + skills/MCP inherit`；AGPair 只统一任务边界、授权 profile、receipt/evidence、wait/watch、health、smoke、routing、onboarding/offboarding 和 controller verification。自调用规避是 controller routing policy，不是 executor 特殊配置。

**Tech Stack:** Python 3.12, Typer, SQLite, local CLI executors, git worktrees, pytest, Codex/Claude Code skills/hooks, AGPair ignored smoke artifacts.

---

## 0. 本文定位

这份文档不是重新发明 AGPair，也不是继续扩展复杂架构。它只做一件事：把最近真实使用中暴露的问题收敛成可执行改造清单，确保后续代码改造不会再次出现“文档写了，但核心模型没落地”的情况。

实施前先按本文评估。真正改代码时必须逐项打勾，并用真实 executor smoke 证明结果可用。

执行方式必须范围化：先修会直接影响真实可用性的主路径，包括外派闭环、report-only/receipt、wait/watch/no-progress、auth/privacy/scope；再推进 registry/lifecycle/docs/smoke 扩展。不要把本文所有章节一次性塞进一个不可验收的大 PR。

## 1. 真实证据

本计划依据以下真实会话 / task / repo 状态，不只依据抽象设计：

- `TASK-694620470CD2`: `grok-cli` 在用户 home 目录这种过大 repo path 下长期 `acked + silent`，`stdout.log` 为 0，`stderr.log` 主要是 plugin/MCP 噪音，最后 report-only 任务仍出现 `Process died without committing` 这类误导文案。
- `TASK-1983C0CB37CB`: `grok-cli` 对本地配置审计长时间运行无可采纳输出，最终被人工 abandon，说明“进程活着”不等于“AGPair 有价值”。
- `TASK-2E0A55DD1A69`: `antigravity-cli` 实际产出了有用中文报告，但 terminal receipt 容错不足，被判为 `blocked(validation_failure)`，导致主控差点丢弃外部成果。
- `TASK-FE0B6F52534F` / `TASK-A95DD3FA5678` / `TASK-DC4F584120B6`: 类似 report-only 外派可以成功到 `ready_for_review`，证明路径有价值，但质量依赖 receipt parser、任务 brief、repo 范围和主控消费闭环。
- `TASK-8FDCD7072E58`: ready_for_review 被 Stop hook 重复拦截，后来用 `task accept` / `is_approved` 解决；这个回归必须长期保留。
- 2026-06-07 smoke: Codex controller 侧应测 `antigravity-cli,grok-cli,claude-code`；Claude Code controller 侧应测 `antigravity-cli,grok-cli,codex`。主控自己的 external self worker 只做显式诊断，不做默认 worker。
- 2026-06-07 代码状态：`managed-restricted`、`isolated-bare`、external `codex` 的 ignore-user-config 特殊待遇已经被移除或计划移除，当前方向是所有 active executor 配置等价。
- 2026-06-08 本次复核任务 `TASK-41B50549BAE2`: 对 AGPair repo 的只读 Antigravity 外派超过 180 秒仍无 stdout/stderr/receipt，最终 abandon。这再次证明 wait/watch/no-progress/adoptable-result 是核心价值指标，不是附属功能。

## 2. 产品判断

AGPair 要像“主控在终端里委托外部 agent”：

- 外部 CLI 默认继承它正常启动时的 skills、MCP、memory、plugins、provider config。
- AGPair 不默认筛 skills，不默认禁 MCP，不默认裸启动，不默认隔离配置目录。
- 如果外部 agent 因坏 skill/MCP、provider、quota、repo 范围、receipt 格式而失败，AGPair 应暴露信号、精确归因、支持换 executor 或回退主控原生子 agent。
- 主控不能把外派当作黑盒成功；`ready_for_review` 只是验收入口。
- external-first 不等于 external-only。敏感、极小、强交互、不可验证或外部无进展的任务可以直接由主控完成，但要有明确理由。

AGPair 的价值指标不是 `task start` 成功，而是：

- adoptable-result rate: 外部结果被主控实际采纳的比例。
- time-to-first-useful-signal: 多久能看到 report、diff、receipt、stdout/stderr 有效增长。
- fallback rate: 外部失败后转主控/native subagent 的比例。
- controller rework rate: 主控为了修外部产物又重做多少工作。
- abandoned/no-progress rate: 外部进程活着但无可用成果的比例。

## 3. 必须保持的核心决策

### 3.1 Executor 默认环境

所有 active executor 统一：

```text
environment_mode = managed-natural
skill_policy = inherit
mcp_policy = inherit
```

适用 executor：

- `antigravity-cli`
- `grok-cli`
- `claude-code`
- `codex`

这条要求的精确定义是：所有 active executor 的默认 dispatch profile 都是 `managed-natural + inherit + inherit`。历史 task、旧 receipt、deprecated executor 或 migration 代码里可以继续读到旧 mode 字段，但旧 mode 不能作为默认 routing fallback，也不能出现在新任务推荐路径里。

禁止再引入这些默认路径：

- `managed-restricted`
- `isolated-bare`
- external `codex` 专属 ignore-user-config
- external `claude-code` 专属 bare auth
- 按 provider 写散落特殊启动逻辑

如果将来确实需要限制模式，必须满足：

- 是显式诊断命令，不进入默认 routing。
- 有真实 executor smoke 证明它解决了具体问题。
- 文档写成“诊断工具”，不是“fallback 路由”。
- 不影响 active executor 等价配置。

### 3.2 Controller-aware routing

路由规则集中在一个 profile/policy 层：

| Controller | 默认外部 executor | 默认不用 | 最后保底 |
| --- | --- | --- | --- |
| Codex | `antigravity-cli` -> `grok-cli` -> `claude-code` | external `codex` | Codex native subagents |
| Claude Code | `antigravity-cli` -> `grok-cli` -> `codex` | external `claude-code` | Claude Code native subagents |
| diagnostic | all active executors with explicit allow-self | none | human/controller decision |

必须把概念写清：

- `codex` = AGPair 管理的 external Codex CLI worker，不是 Codex native subagent。
- `claude-code` = AGPair 管理的 external Claude Code worker，不是 Claude Code native subagent。
- self-executor suppression 是 controller 能力判断，不是 executor 配置低人一等。

### 3.3 Completion policy

AGPair success 不能再等同 commit。

| Policy | 成功证据 | 缺失时 blocker |
| --- | --- | --- |
| `report` | report path、stdout report、或 receipt payload report | `report_output_missing` |
| `evidence` | receipt + changed_files / validation / artifact paths | `evidence_output_missing` |
| `commit` | commit ref 或 repo evidence | `missing_commit_for_commit_policy` |
| `auto` | 从 body/auth/profile 推导出的 effective policy | 按 effective policy |

`local_readonly` 或 `Required changes: none/无/禁止写入` 绝不能因为没有 commit 被 blocked。

## 4. 暴露问题与解决方案

### 4.1 主控使用旧 CLI 参数

现象：

- 会话中出现过 `--repo`、`--title`、`--prompt`。
- 当前 CLI 实际使用 `--repo-path`、`--body` / `--body-file`。

解决：

- 更新 `skills/Codex/SKILL.md`、`skills/Claude/SKILL.md`、README、usage、本地 `~/.codex` / `~/.claude` skills。
- 在 skill 里只保留真实 `agpair task start --help` 支持的参数。
- 增加“错误参数回归扫描”测试或文档检查。

验收：

```bash
rg -n -- "--repo\\b|--title\\b|--prompt\\b" README.md README.zh-CN.md docs skills
```

结果应为空，除非在“错误示例/迁移说明”中明确标为不再使用。

### 4.2 Task body contract 对 report-only 任务不够友好

现象：

- 简短只读任务会被拒绝为缺少 `goal/scope/required changes/exit criteria`。
- `Required changes` 对只读审查语义不自然，但保留结构化 brief 是有价值的。

解决：

- 不新增复杂 `--kind` 系统作为 P0；先把模板和自动 policy 推导做好。
- 支持并推荐：

```text
Goal:
...

Scope:
...

Required changes:
None. This is report-only. Do not edit files.

Exit criteria:
Return a concise report and terminal receipt. Confirm no files were edited.
```

- 错误提示必须返回可复制最小模板。
- 对 `Required changes: none/无/禁止写入` 自动推导 `completion_policy=report`，除非用户显式覆盖。

验收：

- 缺字段错误含最小模板。
- report-only body 不触发 commit requirement。
- README/skills 示例覆盖 report-only 与 mutating 两类任务。

### 4.3 过大 repo path 导致外部 agent 扫爆

现象：

- 用户 home 目录作为 `repo-path` 会让 Grok 扫到 home、logs、plugins、MCP、cache，导致 `grep timed out`、插件噪音、stdout 为空。

解决：

- 默认拒绝 home、根目录、home 上层目录、明显非项目大目录。
- `--allow-broad-repo-path` 必须显式写入 task metadata/status/receipt。
- 文档要求：本地日志研究使用临时 focused workdir 或明确 scope 文件列表，不直接把 home 当 repo。

验收：

- `agpair task start --repo-path "$HOME"` 默认拒绝。
- status 显示 `broad_repo_path_override=true` 时，主控能看见风险。

### 4.4 running 观测不足与 no-progress 止损

现象：

- `acked + silent` 容易被主控误解为“模型还在认真干活”。
- 外部进程可能活着，但 stdout/stderr/receipt/report 没有有效增长。

解决：

`task status --json`、`task watch --json`、smoke harness 必须暴露：

- pid / session id
- stdout/stderr path
- stdout/stderr size
- stdout/stderr mtime
- tail excerpt
- last output time
- liveness_state
- active waiter
- no-progress threshold

`watch --json` 只发状态变化和小摘要，不流完整日志。

no-progress 规则必须进入 `task wait`、默认自动等待、daemon/poller、status/watch 和 smoke harness 主路径，不能只存在于 smoke 脚本。`TASK-744EFA905492` 这类任务超过 180 秒 stdout 仍为 0、stderr 只有 plugin discovery 噪音，但状态仍是 `acked` / `active_via_output`，说明 stderr bootstrap 噪音不能被当作有效进展。

no-progress 阈值必须 profile-driven，而不是全局硬编码：

- report-only / readonly review: 60 秒无有效 stdout/report/receipt 输出 diagnostic event，180 秒无可用信号触发 `no_progress_timeout` 或 `stuck(no_progress)`。
- tiny mutating smoke: 默认 60 秒 diagnostic，240 秒 timeout。
- bounded implementation slice: 默认 120 秒 diagnostic，600 秒 timeout。
- explicitly long-running: 只接受用户或 task profile 显式 timeout。

有效进展定义：

- stdout 出现 report、receipt、JSON event、明确工具进展，或 report/receipt/evidence artifact 生成。
- stderr 只有 plugin/MCP/bootstrap discovery、warning、debug line，不算有效进展。
- 进程仍存活但无有效 stdout/report/receipt，不算可采纳进展。

no-progress 默认行为：

- 如果 stderr 只有 plugin/MCP/bootstrap 噪音，标为低质量启动信号。
- 对 smoke 和主控 skill，超时后应 switch executor 或 fallback，不继续等。

验收：

- `TASK-41B50549BAE2` 这类 stdout/stderr 0 字节任务应被自动标出 no-progress，而不是靠人工发现。
- `TASK-744EFA905492` 这类 stdout 为 0、stderr 只有 plugin discovery 的任务也应被自动标出 no-progress，而不是被 `active_via_output` 误判为有效进展。
- `TASK-1983C0CB37CB` 这类长时间运行任务应能被 watchdog 终止并释放 waiter。
- `task active-waits` 不应出现 malformed JSON / SQLite stepping 错误。

### 4.5 Report-only 失败文案仍可能误导

现象：

- `TASK-694620470CD2` 是 report-only，但 status/report/receipt 说 `Process died without committing`。

解决：

- 所有 terminal arbitration 使用 effective completion policy。
- report-only 缺输出统一写：

```text
executor exited without report or terminal receipt
blocker_type=report_output_missing
```

- `process_crash` 可以作为 `reason_code`，但 blocker_type 必须服务于 controller 下一步。

验收：

- report-only 任务的失败 summary 不包含 commit。
- `receipt.json`、`report.md`、`task logs` 三处文案一致。

### 4.6 Receipt parser 不能丢弃有价值输出

现象：

- `TASK-2E0A55DD1A69` 中 Antigravity 产生了可用中文报告，但 final JSON 变体导致 blocked。

解决：

Parser normalization：

- 接受 `schema_version: "1"`、`"1.0"`、`"1.0.0"`。
- 接受 `ready_for_review`、`success`、`completed` 等低风险 status alias，统一为 `EVIDENCE_PACK`。
- 接受 Claude result envelope、Grok JSON envelope、Antigravity 混合文本 + JSON。
- `raw_log_path` / `receipt_path` 为空时，用 AGPair artifact path 回填。
- 对 report-only，stdout/report 有完整内容时，receipt 小瑕疵不能直接丢弃报告。

严重 malformed 时：

- 仍保存 stdout/stderr/report/evidence。
- phase 可为 `blocked(malformed_terminal_receipt)`，但 `recoverable=true`。
- status 给出 `report_path` / `stdout_path` / `executor_output_excerpt`，主控可人工采纳或 retry。

验收：

- 用 `TASK-2E0A55DD1A69` 类 fixture 复现，最终至少有可消费 report artifact。
- parser 单测覆盖 schema/status/envelope/empty path。

### 4.7 外部结果必须被主控消费

现象：

- 有些会话里主控 `task start` 后转头自己查资料/改代码，没有稳定 `wait/status/read report/accept`。

解决：

Codex/Claude skills 写死外派闭环：

1. `agpair task start ...`。
2. 如果同步 wait 返回 task id，继续到终态。
3. 如果 `--no-wait`，必须 `task watch --json` 或 `task wait` 到终态，除非明确后台化。
4. `task status --json`。
5. 读取 `report_path` / `receipt_path` / `stdout_path` / `stderr_path`。
6. 验证 diff/test/evidence。
7. `agpair task accept TASK_ID` 标记已处理。
8. 如果低质量：自然重试、换 executor、或 fallback native subagent/direct。

禁止：

- 派出后不消费。
- 只看自然语言 summary 就报告成功。
- 反复用模型 turn 手动轮询。

验收：

- skills 文档包含完整闭环。
- Stop hook 对 `is_approved=true` 不再重复拦截。
- `task accept` 有集成测试。

### 4.8 外派写代码太少

现象：

- 实践中 AGPair 常被用于 review/research，很少用于 implementation。

根因：

- 写代码涉及冲突、diff、测试、merge，主控倾向自己做。
- skills 对“implementation 也 external-first”的要求不够硬。
- real smoke 多验证 tiny file change，缺少可采用代码改动样本。

解决：

- 对非 trivial implementation/refactor/test-fix，默认先外派一个 bounded implementation slice。
- mutating 任务默认使用 isolated worktree，除非明确是单文件小改。
- 主控只负责验收和整合，不和外部 worker 同时改同一文件。
- 对外派实现任务的 brief 必须包含：
  - 文件范围
  - 禁止碰的路径
  - 预期测试
  - 是否允许 commit
  - receipt changed_files
  - scope_violations

验收：

- smoke harness 不只做 report，也要能做 tiny mutating change。
- 文档给出 implementation brief 模板。
- 主控未外派非 trivial 实现时，final 要说明原因。

范围控制：

- 这不是 Phase 1 的硬门槛。先通过 report-only、receipt、wait/watch/no-progress 证明外派闭环可靠，再逐步提高 implementation 外派比例。
- 实施任务可以先从 tiny mutating smoke 和机械改动开始，不要求第一轮就把复杂核心实现全部交给外部 worker。

### 4.9 Claude Code worker auth 与 CC Switch

目标：

- 有可用 Claude Code OAuth/subscription 时优先用 OAuth。
- 没有 OAuth 时复用 CC Switch 当前 Claude provider，例如 Kimi 或未来 DeepSeek。
- 不为 AGPair 单独维护第二套 Claude API key 配置，除非显式 `AGPAIR_CLAUDE_CODE_AUTH_MODE=api`。

解决：

- `agpair doctor --fresh` 对 `claude-code` 做真实 live probe。
- `auth_mode=auto` 顺序：
  1. OAuth live probe 成功 -> oauth。
  2. CC Switch 当前 provider probe 成功 -> ccswitch。
  3. 否则 `executor_auth_required`。
- status/doctor 显示 provider name/id/source，但不显示 secret。
- Invalid Authentication 不进入长时间 silent；dispatch 前或早期快速阻断。
- doctor/smoke 必须记录 Claude Code version、auth_mode、provider_source、effective context mode，以及 natural-mode 是否实际加载用户/project context。若上游 `claude -p` 默认行为变化为 bare，AGPair 必须在 preflight 中暴露并阻断或显式恢复 managed-natural 行为，不能把能力缺失的 worker 标成 healthy。

验收：

- OAuth、CC Switch Kimi、无效 provider 三类单测/集成测试。
- provider UI speed test 不能作为唯一可用证明，必须对齐 Claude Code CLI 实际 env/base URL/model/protocol。
- natural-mode health fixture 证明 AGPair 没有默认禁用 skills/MCP/provider config。

### 4.10 坏 skills/MCP 的伤害处理

决策：

- 不通过默认禁用 skills/MCP 来解决。
- 默认保留外部 agent 完整体能力，因为 AGPair 的目标是接近用户正常委托外部 CLI。

处理方式：

- doctor 暴露启动噪音、MCP broken pipe、plugin manifest warning、auth failure。
- no-progress 根据输出信号止损。
- 使用窄 repo path 和明确 scope。
- receipt/evidence 验收不信自然语言。
- 必要时换 executor 或 fallback。

验收：

- docs 不再建议默认 `--no-memory`、`--no-subagents`、禁 MCP、bare。
- doctor/status/watch 能显示噪音摘要和 artifact path。

### 4.11 Secret / privacy 防泄漏

现象：

- 会话日志里曾出现疑似 API key 被当作 command/process metadata 记录。
- executor stdout/stderr/report 也可能保存敏感值。

解决：

- AGPair artifact capture、status excerpt、logs include-output、smoke report 做 redaction。
- task body preflight 对常见 key pattern 给 warning/refusal：
  - `AIza...`
  - `sk-...`
  - `gsk_...`
  - `Bearer ...`
  - 常见 `*_API_KEY=...`
- skills 明确禁止把 secret 写入 `--body`、shell command、receipt、report。
- commit/push privacy gate 扫 diff，不扫或提交 raw `.agpair/tasks`。

验收：

- secret pattern fixture 被 redacted。
- review package 不包含 `~/.agpair/tasks` 原始日志、`~/.codex` / `~/.claude` 私有配置、API key。

### 4.12 Onboarding / offboarding 模块化

每个 executor 平等接入，不许胶水化。

新增 executor 只允许改这些层：

- adapter file
- central `ExecutorSpec`
- command construction tests
- health/doctor tests
- routing tests
- fake executor tests
- real smoke eligibility
- concise docs/skills mentions

禁止：

- 在 task state machine、completion evaluator、watch、doctor formatter 里到处写 provider id `if/elif`。

卸载/退出也要流程化：

- `active`
- `disabled`
- `deprecated`
- `removed`

历史 task/receipt/log 永远可读；新 dispatch 根据 lifecycle 精确拒绝。

验收：

- `test_executor_onboarding.py` 约束每个 active executor 必备 profile 字段。
- `test_executor_lifecycle.py` 覆盖 disabled/deprecated/removed。
- rg 核查 core orchestration 中 executor id 只出现在 registry/profile/adapter/test/doc 合理位置。

### 4.13 Real executor smoke 必须更接近真实工作

当前 smoke 已有价值，但还不够：

- 要覆盖 report-only。
- 要覆盖 tiny mutating change。
- 要覆盖 controller-specific matrix。
- 要覆盖 diagnostic all-registered。
- 要记录 adoptable result，不只记录 phase。

必须跑：

```bash
python scripts/smoke_real_executors.py \
  --repo-path /path/to/agpair \
  --controller codex \
  --executors antigravity-cli,grok-cli,claude-code \
  --timeout-seconds 300 \
  --no-progress-seconds 120

python scripts/smoke_real_executors.py \
  --repo-path /path/to/agpair \
  --controller claude-code \
  --executors antigravity-cli,grok-cli,codex \
  --timeout-seconds 300 \
  --no-progress-seconds 120

python scripts/smoke_real_executors.py \
  --repo-path /path/to/agpair \
  --controller diagnostic \
  --all-registered \
  --allow-self-executor \
  --timeout-seconds 300 \
  --no-progress-seconds 120
```

Smoke report 必须包含：

- outcome
- phase
- blocker_type
- time_to_first_useful_signal
- stdout/stderr byte growth
- receipt/report/evidence paths
- changed_files
- git status/diff summary
- whether result was adoptable
- whether controller needed rework
- cleanup result

Smoke artifacts 存在 `.agpair/smoke/`，默认 ignored，不能提交。

`adoptable_result` 初始可以是 `yes | no | partial | unknown`，不要为了自动化而伪造判断。report-only 可根据 report/receipt 是否可读、主控是否 accept 判定；mutating 任务还必须看 changed_files、validation、scope_violations 和 controller rework。

### 4.14 文档需要收敛

保留文档应说“现在是什么、怎么用、怎么验收”，不要先讲历史。

要保留 / 更新：

- `README.md`
- `README.zh-CN.md`
- `docs/usage.md`
- `docs/usage.zh-CN.md`
- `docs/executor-lifecycle.md`
- `docs/tech-debt-executor-reliability.md`
- `skills/Codex/SKILL.md`
- `skills/Claude/SKILL.md`

要归档或删除：

- 与 Gemini 新任务支持相关的活跃文档。
- Antigravity IDE 作为 executor 的活跃路径。
- AGPair MCP 架构/adapter 说明。
- `managed-restricted` / `isolated-bare` / capability bundle 作为默认方案的文档。
- 已完成但容易误导的旧 plan，保留时必须加 historical archive 标识。

文档措辞：

- AGPair 是 CLI/control-plane integration。
- MCP 不是 AGPair 自身架构。
- Gemini 只 legacy-readable，不是 active executor。
- Antigravity 指 `antigravity-cli`，不是 IDE。
- external `codex` / external `claude-code` 必须写全，避免和 native subagents 混淆。

### 4.15 本地配置同步是交付的一部分

repo push 不等于本机可用。

实施完成后必须同步并验证：

- `~/.codex/skills/agpair/SKILL.md`
- `~/.codex/skills/agpair-codex/SKILL.md`
- `~/.claude/skills/agpair/SKILL.md`
- `~/.codex/hooks.json`
- `~/.claude/settings.json`
- project-local `.codex/hooks.json` / `.claude/settings.json` 如适用
- `AGENTS.md` / `CLAUDE.md` 中 AGPair 路由规则如适用

要求：

- merge-not-overwrite，保留用户自己的 statusLine、OMC、通知、非 AGPair hook。
- uninstall 只移除 AGPair-managed entries。
- `cmp` 验证 skills 同步。
- dry-run 显示将变更的 hooks/settings。
- 本地配置同步永远不是 install/upgrade 的自动副作用。必须通过显式命令执行，先 dry-run，再 apply，并写出 backup/rollback 路径。
- skills 同步要成为一等交付，不能只靠手工复制或 `cmp`。如果继续复用 `agpair codex config` / `agpair claude config`，这些命令必须覆盖 skills；否则新增 `agpair skills sync --dry-run/--apply` 之类明确命令。

## 5. 代码落点

### 5.1 Core policy / registry

Files:

- `agpair/executors/policy.py`
- `agpair/executors/registry.py`
- `agpair/executors/lifecycle.py`
- `agpair/executors/routing.py`

Tasks:

- [ ] 保证所有 active executor 的默认 dispatch profile 是 `managed-natural + inherit + inherit`。
- [ ] legacy/historical mode 可读但不进入默认 routing。
- [ ] 保证 `SKILL_POLICIES` 的 active default 是 `inherit`。
- [ ] 保证 `MCP_POLICIES` 的 active default 是 `inherit`。
- [ ] 保证所有 active executor default mode/policy 一致。
- [ ] controller suppression 只在 profile/routing 层。
- [ ] lifecycle 状态不进入 task state machine。
- [ ] health snapshot 包含 binary、launch、auth、receipt capability、last_failure_type、provider source。

### 5.2 Local CLI execution / completion

Files:

- `agpair/executors/local_cli.py`
- `agpair/completion.py`
- `agpair/task_terminal.py`
- `agpair/artifacts.py`

Tasks:

- [ ] terminal arbitration 完全 policy-aware。
- [ ] process death + report policy -> `report_output_missing`。
- [ ] process death + commit policy -> `missing_commit_for_commit_policy`。
- [ ] 任何 terminal failure 都保存 stdout/stderr/receipt/report/evidence path。
- [ ] `rc.txt` 已存在时不再额外返回 running heartbeat。
- [ ] malformed receipt 不丢 stdout/report。

### 5.3 Receipt parsing

Files:

- `agpair/terminal_receipts.py`
- `tests/unit/test_receipt_validation.py`
- `tests/unit/test_local_cli_executor.py`

Tasks:

- [ ] schema version 接受 `1` / `1.0` / `1.0.0`。
- [ ] status alias normalize。
- [ ] wrapped JSON/envelope/mixed text extraction。
- [ ] empty artifact path 回填。
- [ ] report-only partial success artifact 保留。
- [ ] real fixture 覆盖 `TASK-2E0A55DD1A69` 类 Antigravity 输出。

### 5.4 Wait/watch/liveness

Files:

- `agpair/runtime_liveness.py`
- `agpair/cli/wait.py`
- `agpair/cli/task.py`
- `agpair/watch.py`
- `agpair/storage/waiters.py`

Tasks:

- [ ] `status --json` 暴露 active_attempt artifact metadata。
- [ ] `watch --json` throttled 输出 stdout/stderr 增长事件。
- [ ] no-progress 不依赖 retry_recommended，也不把 bootstrap stderr 噪音当有效进展。
- [ ] no-progress 阈值按 completion policy / authorization profile / task profile 分层。
- [ ] active-waits 不产生 malformed JSON。
- [ ] abandon/cancel/retry 正确处理 active waiter。
- [ ] abandon 终止外部 process group 并释放 waiter。

### 5.5 CLI / hooks / skills

Files:

- `agpair/cli/task.py`
- `agpair/cli/codex.py`
- `agpair/cli/claude.py`
- `skills/Codex/SKILL.md`
- `skills/Claude/SKILL.md`

Tasks:

- [ ] task start 错误提示输出最小 body 模板。
- [ ] skills 删除旧参数 `--repo` / `--title` / `--prompt`。
- [ ] skills 明确 start -> wait/watch -> status -> read artifacts -> verify -> accept。
- [ ] Stop hook 只阻塞未 approved 的 actionable terminal task。
- [ ] UserPromptSubmit 强化 non-trivial implementation 也 external-first。
- [ ] self-executor 文案写成 external worker，不写成 native subagent。

### 5.6 Claude Code auth

Files:

- `agpair/executors/claude_auth.py`
- `agpair/executors/claude_code.py`
- `agpair/cli/doctor.py`
- `tests/unit/test_claude_code_executor.py`
- `tests/unit/test_executor_health.py`

Tasks:

- [ ] auto auth 顺序 OAuth -> CC Switch -> explicit API。
- [ ] CC Switch provider env 不泄漏 secret。
- [ ] Invalid Authentication 快速变成 `executor_auth_required`。
- [ ] doctor 显示 auth_mode/provider id/source，不显示 key。
- [ ] doctor/smoke 显示 Claude Code version、effective context mode、natural-mode context inheritance evidence。

### 5.7 Smoke harness

Files:

- `scripts/smoke_real_executors.py`
- `tests/integration/test_real_executor_smoke_harness.py`

Tasks:

- [ ] 支持 report-only smoke。
- [ ] 支持 tiny mutating smoke。
- [ ] 支持 controller matrix。
- [ ] 支持 diagnostic all-registered。
- [ ] 记录 adoptable_result、time_to_first_useful_signal、fallback suggestion。
- [ ] no-progress 自动 terminate + abandon。
- [ ] smoke artifacts ignored 且 privacy-safe。

### 5.8 Privacy gate

Files:

- `agpair/redaction.py` 或现有 artifact/log helper
- `agpair/cli/task.py`
- `scripts/privacy_scan.py` 如已有则复用
- README / docs release checklist

Tasks:

- [ ] redaction helper 统一用于 excerpts/status/logs/smoke reports。
- [ ] key-like task body preflight warning/refusal。
- [ ] commit 前 privacy scan。
- [ ] review zip 不包含 raw runtime artifacts 或 local config。

## 6. 测试计划

必须新增或保留以下测试：

- `tests/unit/test_executor_onboarding.py`
  - 所有 active executor default mode/policies 相同。
  - 每个 executor 有 profile/health/lifecycle fields。
  - controller suppression matrix 正确。
- `tests/unit/test_executor_lifecycle.py`
  - active/disabled/deprecated/removed。
  - direct selection 不 silent fallback。
  - old task readability。
- `tests/unit/test_receipt_validation.py`
  - schema/status alias/envelope/mixed JSON。
- `tests/unit/test_local_cli_executor.py`
  - report-only process death wording。
  - malformed receipt with report artifact。
  - rc.txt terminal race。
- `tests/integration/test_task_start_and_status.py`
  - active attempt artifact metadata。
  - broad repo guardrail。
  - task body template error。
- `tests/integration/test_task_wait.py`
  - no-progress watchdog。
  - active waiter release。
- `tests/integration/test_codex_cli.py`
  - Stop hook accepted receipt 不重复阻塞。
- `tests/integration/test_claude_cli.py`
  - Claude hook 同上。
- `tests/integration/test_real_executor_smoke_harness.py`
  - matrix selection。
  - suppressed self executor。
  - diagnostic all-registered。

最小验证命令：

```bash
PYTHONPATH=. pytest -q
git diff --check
rg -n -- "--repo\\b|--title\\b|--prompt\\b|managed-restricted|isolated-bare|managed-isolated|AGPAIR_CODEX_IGNORE_USER_CONFIG|Antigravity IDE.*executor|Gemini.*new" README.md README.zh-CN.md docs skills agpair tests || true
```

真实 smoke：

```bash
PYTHONPATH=. python scripts/smoke_real_executors.py --repo-path /path/to/agpair --controller codex --executors antigravity-cli,grok-cli,claude-code --timeout-seconds 300 --no-progress-seconds 120
PYTHONPATH=. python scripts/smoke_real_executors.py --repo-path /path/to/agpair --controller claude-code --executors antigravity-cli,grok-cli,codex --timeout-seconds 300 --no-progress-seconds 120
PYTHONPATH=. python scripts/smoke_real_executors.py --repo-path /path/to/agpair --controller diagnostic --all-registered --allow-self-executor --timeout-seconds 300 --no-progress-seconds 120
```

## 7. 实施顺序

### Phase 1: 收敛核心模型

- [ ] 删除或降级所有默认 fallback/isolation/capability bundle 文案。
- [ ] 锁定 active executor 等价 profile。
- [ ] 锁定 controller suppression matrix。
- [ ] 测试阻止 regression。

Stop rule:

- 如果任何 active executor 的默认 dispatch profile 不是 `managed-natural + inherit + inherit`，停止继续做 smoke，先修模型。
- 如果历史/legacy mode 为了可读性仍存在，不算阻塞；但它们不能进入新任务默认 routing。

### Phase 2: 修外派闭环

- [ ] skills/hooks 改成强制消费闭环。
- [ ] task accept / Stop hook 回归。
- [ ] task body 模板错误提示。
- [ ] CLI 参数文档同步。

Stop rule:

- 如果主控 skill 仍可能派出后不 wait/status/read artifact，停止。

### Phase 3: 修 completion / receipt / report-only

- [ ] completion policy 决定 terminal semantics。
- [ ] receipt parser 容错。
- [ ] report-only 失败文案。
- [ ] malformed receipt 保留可采纳 report。

Stop rule:

- 如果 `TASK-2E0A55DD1A69` 类输出仍会丢 report，停止。

### Phase 4: 修 wait/watch/no-progress

- [ ] active_attempt metadata。
- [ ] output-based liveness。
- [ ] no-progress watchdog。
- [ ] abandon/cancel/waiter release。
- [ ] active-waits JSON 修复。

Stop rule:

- 如果 `acked + silent` 仍只能人工 ps/ls 判断，停止。

### Phase 5: auth / privacy / broad scope

- [ ] Claude auth auto probe。
- [ ] CC Switch provider 复用。
- [ ] broad repo guardrail。
- [ ] redaction/privacy scan。

Stop rule:

- 如果 provider 无效会进入长时间 silent，停止。
- 如果 task body/artifact 可能记录 key，停止。

### Phase 6: real smoke 和本地部署

- [ ] Codex matrix smoke。
- [ ] Claude Code matrix smoke。
- [ ] Diagnostic all-registered smoke。
- [ ] 本地 skills/hooks/settings sync。
- [ ] privacy gate。
- [ ] docs concise cleanup。

Stop rule:

- 如果 smoke 只是 dispatch 成功但没有 adoptable output，不算通过。

## 8. 完成定义

改造完成必须同时满足：

- repo 文档只表达当前产品定位。
- Codex / Claude Code skills 都 external-first，且 implementation 也外派优先。
- 所有 active executor 默认自然继承完整能力。
- self-executor 只被 controller suppression 控制。
- report-only 不再要求 commit。
- malformed receipt 不会丢 report。
- `acked + silent` 有可见 telemetry 和自动止损。
- broad repo path 默认拒绝。
- Claude Code worker 可复用 OAuth 或 CC Switch provider。
- onboarding/offboarding 模块化。
- real smoke matrix 跑通或给出精确 blocker。
- 本地 `~/.codex` / `~/.claude` 已同步。
- privacy scan 无 secrets、无 raw runtime artifacts。
- final report 说明 adoptable-result rate、fallback cases、remaining risks。

## 9. 不做什么

明确不做：

- 不把 AGPair 改成 MCP 架构。
- 不恢复 Gemini 作为新任务 executor。
- 不恢复 Antigravity IDE bridge 作为新任务 executor。
- 不默认筛选 skills/MCP。
- 不默认 `bare` / `restricted` / `isolated`。
- 不做运行中授权暂停/恢复状态机。
- 不把 Codex/Claude native subagents 禁用。
- 不把 task started / process alive 当成功。
- 不提交 `~/.agpair`、`~/.codex`、`~/.claude`、smoke raw logs、private receipts。

## 10. 给执行 agent 的硬性要求

执行这份计划时：

- 每个问题都必须有代码落点、测试落点、文档落点。
- 不允许只改 README 或 skills 就算完成。
- 每次外部 executor 失败都要分类为 unavailable/auth/no-progress/malformed-receipt/report-missing/low-quality/adopted。
- 不允许把失败 executor 靠默认禁能力来“修好”。
- 不允许新增 executor 特权路径。
- 不允许漏同步本地配置。
- 不允许在 GitHub commit/PR 里泄漏本地绝对私密路径之外的敏感内容、tokens、raw logs。
