# AGPair 1.0 命令参考

这份文档是 AGPair 1.0 命令参考。

如果你是第一次使用，建议先看：

- [README.zh-CN.md](../README.zh-CN.md)
- [getting-started-zh.md](getting-started-zh.md)

---

## 1. 环境变量

`agpair` 默认把本地状态放在：

- `~/.agpair/`

如果你要自定义根目录：

```bash
export AGPAIR_HOME=/path/to/custom/root
```

本地 CLI executor 不需要旧桌面 bridge。`agent-bus` 只用于旧 companion /
bridge 安装以及对应的 receipt ingestion 诊断：

```bash
agent-bus
```

如果你要指定别的位置：

```bash
export AGPAIR_AGENT_BUS_BIN=/absolute/path/to/agent-bus
```

---

## 2. `doctor`

### 基础健康检查

```bash
agpair doctor
```

会输出：

- 本地配置目录
- DB 是否存在
- `db_error`
- daemon 状态
- 最新 receipt id
- 注册 executor 健康状态，包括 binary、启动、receipt 能力、生命周期状态和路由资格
- 传入 repo path 时的 Codex / Claude hook 安装状态
- 旧 companion bridge 诊断（仅旧安装路径需要）

### 针对具体 repo 的预检

```bash
agpair doctor --repo-path /absolute/path/to/repo
```

会额外输出：

如果目标 repo 仍使用旧 companion bridge，`doctor` 会额外显示 bridge marker /
端口、`/health`、`ls_bridge_ready`、`workspace_paths`、`receipt_watcher_running`
和 `repo_bridge_warning` 等诊断字段。

### 什么时候该跑 `doctor`

建议在这些时候跑：

- 开始新任务前
- 切到另一个 repo 前
- daemon 重启后
- 任务卡住需要排查时

---

## 3. `daemon`

### 启动

```bash
agpair daemon start
```

### 查看状态

```bash
agpair daemon status
```

### 停止

```bash
agpair daemon stop
```

### 前台调试

```bash
agpair daemon run --once
agpair daemon run --interval-ms 1000 --timeout-seconds 1800
```

后台 daemon 日志现在会写到：

- `~/.agpair/daemon.stdout.log`
- `~/.agpair/daemon.stderr.log`

### `--force`

```bash
agpair daemon start --force
agpair daemon run --once --force
```

注意：

- local CLI executor 不竞争桌面 receipt。
- `--force` 只影响旧 Antigravity companion bridge 的桌面 reader 预检。
- `--force` 只会绕过预检告警，**不会**绕过真正的共享锁。

---

## 4. `task start`

```bash
agpair task start \
  --repo-path /absolute/path/to/repo \
  --task-kind implementation \
  --wait-policy lease \
  --authorization-profile local_mutating \
  --completion-policy evidence \
  --isolated-worktree \
  --body "Goal: 修复 smoke 问题。Scope: 只处理相关仓库文件。Required changes: 做最小代码或测试改动。Exit criteria: 运行聚焦测试并返回证据。"
```

如果要显式使用默认外部 CLI executor：

```bash
agpair task start \
  --executor antigravity-cli \
  --repo-path /absolute/path/to/repo \
  --task-kind quick_review \
  --wait-policy lease \
  --authorization-profile local_readonly \
  --completion-policy report \
  --body "Goal: 审查指定区域。Scope: 仅限已点名文件。Required changes: None. This is report-only. Do not edit files. Exit criteria: 返回带证据的结论。"
```

推荐写法仍然是 `--repo-path`、`--body` 和 `local_readonly` 这类完整 profile。
`task start` 兼容 `--repo`、`--prompt`、`readonly` 等常见别名，也会把过短 body
自动补成结构化任务体；这些只是兜底，controller skill 仍应直接发送完整的
`Goal` / `Scope` / `Required changes` / `Exit criteria` 合同。

`--repo-path` 应该指向具体项目目录。AGPair 默认拒绝文件系统根目录、用户 home 目录，以及用户 home 上层目录，因为外部 executor 可能扫到私人日志、缓存和无关项目。如果确实要这么做，显式传 `--allow-broad-repo-path`；该 override 会写入 task，并在 `task status` 中可见。

实现 / 重构 / 修测试这类有边界的工作，用 isolated worktree 的 evidence 任务：

```bash
agpair task start \
  --executor antigravity-cli \
  --repo-path /absolute/path/to/repo \
  --task-kind implementation \
  --wait-policy lease \
  --authorization-profile local_mutating \
  --completion-policy evidence \
  --isolated-worktree \
  --body "Goal: 有边界的修改。Scope: 允许文件。Required changes: 修改内容。Exit criteria: 聚焦验证。"
```

isolated 的 mutating evidence/commit 任务默认是 `--dirty-snapshot tracked`，会把主控 worktree 里 tracked 的 staged/unstaged 改动复制进 executor worktree。ignored 和 untracked 文件不会复制。需要让 worker 只基于已提交 HEAD 时，传 `--dirty-snapshot off`。

新任务可用 executor id：

- `antigravity-cli`：默认外部实现 executor
- `grok-cli`：低成本外部候选 / 复核 executor
- `claude-code`：AGPair 管理的外部 Claude Code CLI executor
- `codex`：AGPair 管理的外部 Codex CLI executor

历史 executor 记录仍可为兼容性读取。新的 `task start` 和 `task retry` 只使用 active registered executor id。

当你省略 `--executor` 时，解析顺序是：

1. target 级 `default_executor`
2. `AGPAIR_DEFAULT_EXECUTOR`
3. 产品回退 `antigravity-cli`

主控侧推荐默认值：

- Codex 和 Claude Code 默认都优先走 AGPair 外部 executor。
- Codex 主控默认抑制 AGPair 管理的外部 `codex`；先用 `claude-code`，再把 Codex 原生 subagent 作为 fallback / review。
- Claude Code 主控默认抑制 AGPair 管理的外部 `claude-code`；先用 `codex`，再把 Claude Code 原生 subagent 作为 fallback / review。
- `ready_for_review` 只是验收门槛，不是自动完成；主控仍要检查 receipt、diff 和测试证据，然后运行 `agpair task accept TASK_ID` 标记这个 receipt 已处理。

这意味着跨主控 worker 分工是显式的：`codex` 给 Claude Code 主控使用，`claude-code` 给 Codex 主控使用；各自主控的原生 subagent 只作为自己的 fallback / review lane。

Executor launch environment：

| Executor | 默认 mode | Skills/MCP |
| --- | --- | --- |
| `grok-cli` | `managed-natural` | inherit |
| `antigravity-cli` | `managed-natural` | inherit |
| `claude-code` | 认证健康时 `managed-natural` | inherit |
| `codex` | `managed-natural` | inherit |

`managed-natural` 表示 AGPair 管任务状态、授权 profile、receipt/log 捕获、
wait/watch、retry 和验收证据；外部 CLI 保留它正常启动时的 skills、MCP、memory、
plugins 和 provider 配置。如果外部 attempt 不够好，就自然模式重试、换另一个
外部 executor，或者让主控回到自己的原生 subagent fallback / review lane。

AGPair 的 external-first hooks 只面向主控会话。AGPair 自己启动的 executor、
probe、smoke 和 retry 进程会被标记为 internal，让 Codex / Claude hooks no-op，
避免递归注入委派提示，或被无关的 `ready_for_review` 任务拦停。

本地 CLI 的 approval 模式可以通过环境变量调整：

- `AGPAIR_ANTIGRAVITY_CLI_BIN=/absolute/path/to/agy`
  旧别名：`AGPAIR_ANTIGRAVITY_CLI`
- `AGPAIR_ANTIGRAVITY_APPROVAL_MODE=default|yolo`
  默认：`yolo`
- `AGPAIR_ANTIGRAVITY_MODEL="Gemini 3.1 Pro (Low)"`
  可选；当 Antigravity 默认模型在 `--print` 模式超时时使用。
  这是 Antigravity 模型标签，不是已经退场的 Gemini CLI executor。
  旧别名：`AGPAIR_ANTIGRAVITY_CLI_MODEL`
- `AGPAIR_ANTIGRAVITY_PRINT_TIMEOUT=30m0s`
- `AGPAIR_GROK_CLI_BIN=/absolute/path/to/grok`
  旧别名：`AGPAIR_GROK_CLI`
- `AGPAIR_GROK_OUTPUT_FORMAT=json|streaming-json`
  默认：`json`
- `AGPAIR_GROK_MAX_TURNS=12`
  默认值刻意收紧，适合 AGPair 后台 bounded 任务；只有明确的大任务才建议调高。
- `AGPAIR_CLAUDE_CODE_BIN=/absolute/path/to/claude`
  旧别名：`AGPAIR_CLAUDE_CODE_CLI`
- `AGPAIR_CLAUDE_CODE_AUTH_MODE=auto|oauth|ccswitch|api`
  默认：`auto`。Auto mode 会先使用有效的本机 Claude Code 订阅 / OAuth 登录；
  如果没有登录或 live probe 失败，就回退到 CC Switch 当前选中的 Claude
  provider。设为 `oauth` 可禁用 provider 回退，设为 `ccswitch` 可直接使用 CC
  Switch provider，设为 `api` 则使用单独 worker credential。
- `AGPAIR_CC_SWITCH_HOME=/absolute/path/to/.cc-switch`
  可选，默认 `~/.cc-switch`。AGPair 读取 CC Switch 当前 Claude provider 的
  settings，并把它们作为 worker 进程 env 注入；provider secret 不会写进 AGPair
  command 文件或 health JSON。
- `AGPAIR_CLAUDE_CODE_MAX_RETRIES=<integer>`
  默认：`0`。AGPair 会给 worker 设置 `CLAUDE_CODE_MAX_RETRIES`，让无效
  OAuth / API credential 快速失败，而不是静默重试。
  `agpair doctor --fresh` 的 live auth probe 也使用同一个 managed-natural
  Claude Code surface；不会使用 bare mode，也不会禁用 skills/MCP。health JSON
  会为这条路径报告 `auth_satisfied`、`auth_probe_environment_mode`、
  `auth_probe_skill_policy`、`auth_probe_mcp_policy`、`auth_state` 和
  `last_failure_type`。`executor_probe_timeout` 和
  `executor_hook_interference` 不是 credential 失败；只有
  `executor_auth_required` 表示需要处理 OAuth 或 CC Switch provider credential。
- `AGPAIR_CLAUDE_CODE_PROBE_CWD=/tmp-like-neutral-path`
  可选。live auth probe 默认跑在中立临时目录，避免项目 hooks、MCP 和 repo
  上下文把 provider 检查变成主控任务。
- `AGPAIR_CLAUDE_CODE_SETTINGS=/absolute/path/to/settings.json`
  API mode 下可选 Claude Code settings JSON 或路径。
  可用下面的命令生成安全模板：
  `agpair claude worker-settings > ~/.agpair/claude-worker-settings.json`
  然后把 `AGPAIR_CLAUDE_CODE_SETTINGS` 指向该文件，并让 helper 返回有效 API key，
  通常是通过 `ANTHROPIC_API_KEY`。
- `AGPAIR_CLAUDE_CODE_PERMISSION_MODE=<claude --permission-mode 支持的值>`
  默认：`bypassPermissions`
- `AGPAIR_CODEX_BIN=/absolute/path/to/codex`
  旧别名：`AGPAIR_CODEX_CLI`
- `AGPAIR_CODEX_APPROVAL_MODE=default|full_auto|bypass_all`
  默认：`bypass_all`

以下是 AGPair 自动设置的内部 launch markers，不要放进全局 shell：

- `AGPAIR_INTERNAL_ROLE=probe|executor|smoke`
- `AGPAIR_SUPPRESS_CLIENT_HOOKS=1`
- `AGPAIR_NONINTERACTIVE=1`
- `AGPAIR_ALLOW_NESTED_DELEGATION=1`

executor 启动出来的进程默认禁止再次发起 AGPair 嵌套委派。
`--allow-nested-delegation` 不能由 executor 自己授权；它还必须同时拿到
controller 环境里的 `AGPAIR_ALLOW_NESTED_DELEGATION=1`。这样可以防止
Claude Code 或其他继承来的 skill 把一个 worker 任务再次变成 controller loop，
除非这本来就是主控显式授权的编排任务。

这些开关都是 adapter-local 的诊断/兼容入口。所有 executor 的共享合同仍以
registry profile 为准，测试会要求 profile 声明的非交互和隔离 flag 与 adapter
默认命令保持一致。

`generic` 任务默认会等到终态；带 `--task-kind` 的任务会使用对应 wait policy。
要立即返回：

```bash
agpair task start \
  --repo-path /absolute/path/to/repo \
  --body "Goal: 做一次快速审查。Scope: 仅限已点名文件。Required changes: None. This is report-only. Do not edit files. Exit criteria: 返回带证据的结论。" \
  --no-wait
```

自定义 task id：

```bash
agpair task start \
  --task-id TASK-001 \
  --repo-path /absolute/path/to/repo \
  --body "Goal: 运行 smoke 检查。Scope: 只处理相关仓库文件。Required changes: None. This is report-only. Do not edit files. Exit criteria: 返回命令输出和证据。"
```

### Completion policy

不是所有任务都需要 commit。终态语义由 completion policy 决定：

- `report`：需要捕获报告、stdout 报告输出，或携带报告证据的有效结构化 receipt。
- `evidence`：需要可验证 evidence，例如 receipt payload、artifact、changed files 或测试输出。
- `commit`：需要可验证 commit。
- `auto`：根据授权 profile 和任务 brief 解析有效策略。

`local_readonly` 以及明确写了 `Required changes: none`、`no changes`、`无`、
`禁止写入` 的任务，应按 report/evidence 语义验收，不能因为没有 commit 就被阻塞。

### Task kind 和 wait policy

`--task-kind` 用来让主控选择合适的默认等待预算和执行预算：

| Task kind | 默认 wait | 主控 lease | 硬预算 |
| --- | --- | ---: | ---: |
| `quick_review` | `lease` | 120s | 900s |
| `deep_review` | `lease` | 240s | 1800s |
| `implementation` | `lease` | 300s | 3600s |
| `test_fix` | `lease` | 300s | 3600s |
| `research` | `lease` | 300s | 5400s |
| `smoke` | `strict` | 300s | 600s |
| `generic` | `terminal` | 无 | 无 |

`--wait-policy lease` 会让主控在有界窗口里低噪等待。如果 executor 仍在运行，
AGPair 会返回结构化的 background-running 结果，而不是让主控浪费模型轮次轮询，
或过早杀掉任务。`terminal` 和 `strict` 保留旧语义：timeout、watchdog 和终态失败
都会让命令失败。

所有发单命令（`start`、`retry`）支持：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--task-kind` | `generic` | `quick_review`、`deep_review`、`implementation`、`test_fix`、`research`、`smoke` 或 `generic` |
| `--wait-policy` | 由 task-kind 决定 | `terminal`、`lease`、`background` 或 `strict` |
| `--controller-wait-seconds` | 由 task-kind 决定 | 主控等待多久后可以得到 background-running 结果 |
| `--execution-budget-seconds` | 由 task-kind 决定 | daemon 标记 stuck 前的硬执行预算 |
| `--background-ok / --no-background-ok` | 由 task-kind 决定 | lease 到期时是否允许 executor 继续在后台跑 |
| `--wait / --no-wait` | `--wait` | 发单后等待，或立即返回 |
| `--interval-seconds` | `5` | 本地轮询最大间隔；等待初始完成窗口内会自动更快轮询 |
| `--timeout-seconds` | `3600` | terminal/strict wait 的本地最长等待时间 |

`implementation` 和 `test_fix` 任务会把 `--completion-policy auto` 默认解析为
`evidence`。代码写入任务仍应显式传 `--isolated-worktree`，避免 worker 静默修改主控
正在使用的 worktree。

### 任务元数据（编排提示）

你可以为任务附加编排元数据，帮助主控器计划并行与隔离执行。

- `depends_on`: 在此任务开始前必须完成的前置任务 ID 列表。
- `isolated_worktree`: 对本地 CLI executor，在 AGPair 能创建或解析 worktree 时，会从独立 git worktree 运行。
- `worktree_boundary`: 任务或 worktree 的预期执行边界。
- `setup_commands`: 持久化给主控看的前置提示；AGPair 不执行任意 setup 脚本。
- `teardown_commands`: 持久化给主控看的后置提示；AGPair 不执行任意 teardown 脚本。
- `env_vars`: 单任务环境提示；只有 executor 明确支持的环境变量会自动应用。
- `spotlight_testing`: 布尔值，表示优先运行局部焦点测试而非全量测试的意图。

**并发建议：** 默认使用有价值的并行度。同一个 executor 可以同时开多个 task，
包括多个 `grok-cli`，只要每个 task 有不同的 task id、prompt、文件切片、
审查角度或验收标准。会写文件的任务必须跨 isolated worktree 或互不重叠的
scope 并发，不能在同一个主控 worktree 内互相踩文件。

---

## 5. `task status`

```bash
agpair task status TASK-001
agpair task status TASK-001 --json
```

会显示：

- `task_id`
- `phase`
- `repo_path`
- `session_id`
- `attempt_no`
- `retry_count`
- `retry_recommended`
- `stuck_reason`

`status --json` 还会暴露当前 attempt、executor id、实际 binary 名称、pid
（如果可用）、stdout/stderr 路径、日志大小、最后输出时间、小段 tail
excerpt、liveness state、effective completion policy 和精确 blocker metadata。
完整 raw logs 默认留在磁盘上，只有显式请求才读取。

## 5.1 审查并采纳 isolated 代码改动

对于 isolated 的 implementation 或 test-fix 任务，先看 executor diff，再触碰主控
worktree：

```bash
agpair task diff TASK-001
agpair task diff TASK-001 --stat
agpair task diff TASK-001 --json
```

然后检查这个 diff 是否能干净应用到主控 repo：

```bash
agpair task apply TASK-001 --check
```

如果检查通过且主控认可这个方案，再应用 diff：

```bash
agpair task apply TASK-001
```

`task apply` 使用 isolated worker 的 baseline，会排除主控 dirty snapshot baseline，
并把改动以未 staged 状态留在主控 worktree，方便正常 review 和测试。它不会自动
accept AGPair 任务；只有主控验证完成后才运行 `task accept`。

---

## 6. `task list`

```bash
agpair task list
agpair task list --phase acked
agpair task list --repo-path /绝对/仓库路径 --json
```

适合快速看本地 SQLite 里还挂着哪些任务。

现在也支持：

- `--repo-path` / `--target`：只看某一个 repo 的任务
- `--json`：输出机器可读 JSON，适合给 status line、hooks 或 controller 端筛选逻辑直接消费

## 6.1 Claude Code 辅助命令

`agpair` 现在还带了一组面向 Claude Code 的轻量集成命令：

```bash
agpair claude config
agpair claude statusline
agpair claude hook session-start
agpair claude hook precompact
agpair claude hook user-prompt-submit
agpair claude hook stop
agpair claude hook subagent-start
```

`agpair claude config` 会直接输出一段可粘贴到 Claude Code `settings.json` 的配置，默认接好：

- `statusLine.command` → `agpair claude statusline`
- `SessionStart` hook → `agpair claude hook session-start`
- `PreCompact` hook → `agpair claude hook precompact`
- `UserPromptSubmit` hook → `agpair claude hook user-prompt-submit`
- `Stop` hook → `agpair claude hook stop`
- `SubagentStart` hook → `agpair claude hook subagent-start`
- `SubagentStop` / `TaskCreated` / `TaskCompleted` observability hooks

配置管理参数：

- 默认行为：只打印这段受 AGPair 管理的 JSON 片段
- `--install` / `--merge`：把 AGPair 管理片段写入 Claude Code settings
- `--scope project|user`：选择当前 repo 下的 `.claude/settings.json` 或 `~/.claude/settings.json`；默认 `project`
- `--dry-run`：只打印 unified diff，不写盘
- `--uninstall`：只移除 AGPair 自己管理的条目
- `--sync-skill/--no-sync-skill`：管理 `.claude/skills/agpair/SKILL.md` 或 `~/.claude/skills/agpair/SKILL.md`；安装/卸载时默认同步
- `--force`：显式覆盖非 AGPair 管理的 `statusLine`

安全约束：

- 遇到非 AGPair 管理的 `statusLine`，默认保留原值并继续同步 AGPair hooks；只有显式 `--force` 才会替换它
- hook 按 AGPair command identity 追加 / 去重，保留其他 Claude Code hook
- `--uninstall` 只移除 AGPair 自己的条目，不碰无关配置
- skill sync 只管理 AGPair skill 路径，遇到非 AGPair skill 会拒绝覆盖

设计取舍：

- `statusline` 会读取 Claude Code 通过 stdin 传来的 JSON，解析当前 repo / worktree，并输出简短 AGPair 状态。
- `session-start` 会给当前 repo 注入一段很短的 AGPair 提示上下文，提醒主控优先用外部 executor。
- `precompact` 只会在 AGPair 任务处于 `acked` 或 `evidence_ready` 时阻止 compact；其他可见状态可能仍显示在 status line，但不会因此拦截 compact。
- `user-prompt-submit` 注入 external-first 路由上下文。
- `stop` 只在未接受的 `ready_for_review`、`approval_required` 等需要主控决策的状态阻止过早结束。
- `subagent-start` 只做 advisory；Claude Code 原生 subagent 仍是 fallback / review 资源。
- 默认**不**提供 `InstructionsLoaded` 提示 hook，因为 Claude Code 官方把这个事件定义为 observability-only，不能可靠地做上下文提醒。
- 默认**不**提供 `WorktreeCreate` hook，因为这个 hook 会完全替换 Claude Code 内建的 git worktree 行为，默认启用太重。

## 6.2 Codex 辅助命令

AGPair 可以输出或安装 Codex hook 配置，让 Codex 对非平凡任务优先使用外部 CLI executor，并避免用模型轮询外部任务：

```bash
agpair codex config
agpair codex config --install --scope project --repo-path "$REPO" --sync-skill
```

AGPair 管理的 hooks：

- `UserPromptSubmit`：注入简短 external-first 上下文。
- `Stop`：只在未接受的 `ready_for_review`、`approval_required` 等需要 Codex 决策的状态阻止过早结束。
- `SubagentStart`：只给 advisory context；Codex native subagents 仍是 fallback / review 资源。

`--install`、`--uninstall` 和 `--dry-run` 默认同步 Codex AGPair skill，只管理
`.codex/skills/agpair-codex/SKILL.md` 或 `~/.codex/skills/agpair-codex/SKILL.md`
这一条 AGPair skill 路径。需要只管理 hook 时传 `--no-sync-skill`。AGPair 遇到非 AGPair skill 会拒绝覆盖。

### 如何判断 AGPair 是否真的有价值

不要把 dispatch 成功或进程还活着当成价值。重点看 completion rate、
可用 `agent_result` rate、time-to-first-useful-signal、fallback
recommendation rate、controller rework rate，以及 abandoned/no-progress rate。
主要入口是 `agpair task status --json`、`agpair task list --json` 和
`scripts/smoke_real_executors.py` 的 `summary_metrics`。

异步任务使用低噪等待：

```bash
agpair task watch TASK-123 --json
```

---

## 7. `task logs`

```bash
agpair task logs TASK-001
agpair task logs TASK-001 --raw stdout
agpair task logs TASK-001 --raw stderr
```

日志会显示最近的：

- 创建
- 发单
- ACK
- EVIDENCE_PACK
- BLOCKED
- COMMITTED
- retry 事件

---

## 8. `task retry`

表示换一轮 fresh session 重新执行。

```bash
agpair task retry TASK-001 --body "Retry with a fresh session."
```

适合：

- 当前 session 明显坏了
- 卡住了

如果是授权阻塞，用 structured blocked context 开新 attempt：

```bash
agpair task retry TASK-001 \
  --from-block \
  --authorization-profile local_mutating
```

---

## 9. `task abandon`

如果你只是想在本地停止跟踪一个悬挂任务，可以直接：

```bash
agpair task abandon TASK-001 --reason "manual cleanup"
```

这个命令只改本地状态，不会联系 executor。

---

## 10. `task wait`

如果你发单时用了 `--no-wait`，可以之后再挂起等待：

```bash
agpair task wait TASK-001
agpair task wait TASK-001 --timeout-seconds 600 --interval-seconds 10
```

退出码 `0` 可以表示终态成功，也可以表示允许后台继续的 lease outcome。用
`--json` 查看 `outcome`、`agent_result`、`recovery_decision`、
`controller_lease_expired` 和 `background_ok`。终态任务优先看
`recovery_decision.action`：报告通常是 `use_result`，隔离实现 diff 通常是
`review_then_apply`，后台继续等待是 `wait_background`，失败恢复可能是
`switch_executor`、`native_fallback`、`repair_executor` 或 `retry_same_executor`。

退出码 `1` 表示终态失败、strict timeout / watchdog、任务不存在，或当前 wait
outcome 不允许后台继续。

现在对于“repo 里其实已经有 commit，但最终 terminal receipt 没回来”的部分 `evidence_ready` 任务，系统可以基于强 repo 证据自动收口。遇到这类情况时，优先查看 `task status --json` / `inspect --json`，而不是默认手动 `abandon`。

当 terminal/strict watchdog 触发时，`task wait` 和默认自动等待会提前退出并提示
你执行 `agpair task retry <TASK_ID>`。对于 `background_ok=true` 的 lease 任务，
`soft_no_progress` 表示应查看 `task status --json`、稍后 watch，或让 executor 在
后台继续。

---

## 11. 自动等待选项

所有发单命令（`start`、`retry`）使用上文的 task-kind 和 wait-policy 控制。

`status`、`logs`、`wait` 命令**不**带 `--wait/--no-wait`。

---

## 12. 工作流

普通工作使用 `agpair task start`。当非平凡任务适合多个 `grok-cli` 复核、
竞争实现候选，或需要额外 `antigravity-cli` / `claude-code` 验证时，直接启动
多个 task id。高价值 panel 工作使用 `agpair workflow fanout`，让主控一次拿到
多个 lane card 和一个 synthesis/gate evidence pack。只有 preset fanout 表达不
够时，才用 manifest 版 `agpair workflow start`。

```bash
agpair workflow fanout \
  --controller codex \
  --mode review \
  --topic "Review terminal receipt salvage and workflow synthesis risks" \
  --lane grok-cli:primary \
  --lane grok-cli:adversarial \
  --lane antigravity-cli:second-opinion \
  --repo-path /absolute/path/to/repo \
  --wait --json
agpair workflow fanout \
  --controller codex \
  --mode implementation \
  --topic "Implement a bounded parser fix" \
  --scope "agpair/workflows/*.py and focused tests only" \
  --lane grok-cli:candidate-a \
  --lane claude-code:candidate-b \
  --isolated-worktree \
  --repo-path /absolute/path/to/repo \
  --dry-run --json
agpair workflow validate --file templates/workflows/fanout-synthesize.json
agpair workflow start --file templates/workflows/fanout-synthesize.json --controller codex --repo-path /absolute/path/to/repo --json
agpair workflow status WF-ABC123DEF456 --json
agpair workflow watch WF-ABC123DEF456 --json --cursor '<cursor>'
agpair workflow retry-node WF-ABC123DEF456 scan-routing --authorization-profile local_mutating
agpair workflow cancel WF-ABC123DEF456 --reason 'operator requested'
```

工作流清单是声明式的。AGPair 会拒绝任意脚本字段，并派发普通 AGPair 子任务；子任务仍使用 durable artifacts、completion policies、结构化 receipt 和 controller-aware executor routing。

Workflow `ready_for_review` 表示 AGPair 已生成 evidence pack 等待主控验收，不是最终用户侧完成。`workflow watch --json` 只输出低噪状态变化和 artifact 路径，不输出完整 raw logs。

Fanout workflow 会在 status/watch/evidence payload 里暴露 `lane_cards`、`synthesis_result` 和 `panel_result`。Synthesis 是主控要检查的证据，不是最终答案。部分 lane 输出即使 receipt 不完美也会被保留，但 AGPair 会标成 `needs_review`，不会把 salvage 伪装成成功。

---

## 13. 失败姿态

`agpair` 故意偏保守：

- daemon 不会自动发 semantic message
- daemon 不会自动帮你 fresh retry
- soft no-progress 后可能建议 retry
- terminal/strict wait 会在 watchdog 后提前失败，而不是盲等到硬超时
- `background_ok=true` 的 lease wait 会返回结构化 background-running outcome，同时 executor 继续运行
- 只有到了硬超时，才会标成 `stuck`

## 14. Executor 生命周期

所有外部 executor 都是注册模块。新增、禁用、弃用或移除 executor，要走共享 profile
contract，而不是在任务状态机里散落 provider 特判。详见
[Executor Lifecycle](executor-lifecycle.md)。

当前 active executor id 是 `grok-cli`、`antigravity-cli`、`claude-code` 和
`codex`。`codex` 指 AGPair 管理的外部 Codex CLI worker，不是 Codex 原生
subagent；`claude-code` 指 AGPair 管理的外部 Claude Code worker，不是 Claude
Code 原生 subagent。

## 15. 发布与隐私检查

发布、提交 PR 或 push 前：

- 跑目标测试和完整 unit/integration suite。
- 跑 controller matrix 的真实 smoke，要求 `all_success=true`，且每个尝试过的
  executor 都有可用的 `agent_result.state` 和 `recovery_decision.action`；smoke report 只留本地。
- 跑 `git diff --check`。
- 检查 `git status --short --untracked-files=all`。
- 不要提交 `.agpair/`、`~/.agpair`、raw executor logs、本地 receipts、session transcripts、个人 Codex/Claude 配置或生成的 hook debug 输出。
- 从要提交的 docs 中清掉本机路径和私有 artifact 引用。
- 手动检查 GitHub About / description / topics；这些仓库元数据不在 source diff 里，可能仍是旧措辞。

---

## 16. 最推荐的命令顺序

对真实任务，建议顺序是：

1. `agpair doctor --fresh --repo-path <repo>`
2. `agpair daemon status`
3. `agpair task start ... --task-kind quick_review ...` 或 `--task-kind implementation --isolated-worktree ...`
4. `agpair task status <TASK_ID>` 或 `agpair task list`
5. 代码任务先运行 `agpair task diff <TASK_ID>` 和 `agpair task apply <TASK_ID> --check`
6. `agpair task logs <TASK_ID>`（需要 raw 证据时）
7. 只选一个：
   - `retry`
   - `abandon`（仅本地清理）
8. 验证后 `agpair task accept <TASK_ID> --adoptable-result yes --controller-rework none`
