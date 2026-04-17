# agpair 命令参考

这份文档是命令参考。

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

`agpair` 默认查找 `agent-bus`：

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
- `agent-bus` 是否可用
- daemon 状态
- 最新 receipt id
- `desktop_reader_conflict`

### 针对具体 repo 的预检

```bash
agpair doctor --repo-path /absolute/path/to/repo
```

会额外输出：

- bridge marker 路径
- bridge 端口
- `/health` 是否可达
- `sdk_initialized`
- `ls_bridge_ready`
- `monitor_running`
- `workspace_paths` 是否命中目标 repo
- `agent_bus_watch_running`
- `agent_bus_delegation_enabled`
- `receipt_watcher_running`
- `repo_bridge_session_ready`
- `repo_bridge_warning`

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

- `--force` 只会绕过预检告警
- **不会**绕过真正的共享锁

---

## 4. `task start`

```bash
agpair task start \
  --repo-path /absolute/path/to/repo \
  --body "Goal: ..."
```

如果要显式使用 Codex backend：

```bash
agpair task start \
  --executor codex \
  --repo-path /absolute/path/to/repo \
  --body "Goal: ..."
```

如果要显式使用 Gemini backend：

```bash
agpair task start \
  --executor gemini \
  --repo-path /absolute/path/to/repo \
  --body "Goal: ..."
```

当前后端策略摘要：

- `antigravity`：交互式 IDE executor
- `codex`：CLI executor
- `gemini`：CLI executor

本地 CLI 的 approval 模式可以通过环境变量调整：

- `AGPAIR_CODEX_APPROVAL_MODE=default|full_auto|bypass_all`
  默认：`bypass_all`
- `AGPAIR_GEMINI_APPROVAL_MODE=default|auto_edit|yolo`
  默认：`yolo`

默认情况下，`task start` **会阻塞**直到任务进入终态。
要立即返回：

```bash
agpair task start \
  --repo-path /absolute/path/to/repo \
  --body "Goal: ..." \
  --no-wait
```

自定义 task id：

```bash
agpair task start \
  --task-id TASK-001 \
  --repo-path /absolute/path/to/repo \
  --body "Goal: ..."
```

### 任务元数据（编排提示）

你可以为任务附加编排元数据，帮助主控器计划并行与隔离执行。
**注意：** 这些字段当前**仅作为元数据**存在。它们会落盘保存并在 `status` 与 `inspect` 输出中可见，但 `agpair` daemon 目前**不会**在运行时强制执行它们（例如自动运行 setup 脚本）。

- `depends_on`: 在此任务开始前必须完成的前置任务 ID 列表。
- `isolated_worktree`: 布尔值，表示意图在一个独立的 git worktree 中执行此任务。
- `worktree_boundary`: 预期任务运行的工作区根目录边界。
- `setup_commands`: 执行前置脚本（例如创建 worktree 或启动依赖）。
- `teardown_commands`: 执行后置脚本（例如清理 worktree）。
- `env_vars`: 单任务环境变量隔离（例如 `PORT`, `AGPAIR_PORT_OFFSET`）。
- `spotlight_testing`: 布尔值，表示优先运行局部焦点测试而非全量测试的意图。

**并发建议：** 永远在跨 worktree 间做并发，不能在同一个 worktree 内并发任务。

---

## 5. `task status`

```bash
agpair task status TASK-001
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
- `--json`：输出机器可读 JSON，适合给 MCP client、status line 或 controller 端筛选逻辑直接消费

## 6.1 Claude Code 辅助命令

`agpair` 现在还带了一组面向 Claude Code 的轻量集成命令：

```bash
agpair claude config
agpair claude statusline
agpair claude hook session-start
agpair claude hook precompact
```

`agpair claude config` 会直接输出一段可粘贴到 Claude Code `settings.json` 的配置，默认接好：

- `statusLine.command` → `agpair claude statusline`
- `SessionStart` hook → `agpair claude hook session-start`
- `PreCompact` hook → `agpair claude hook precompact`

配置管理参数：

- 默认行为：只打印这段受 AGPair 管理的 JSON 片段
- `--install` / `--merge`：把 AGPair 管理片段写入 Claude Code settings
- `--scope project|user`：选择当前 repo 下的 `.claude/settings.json` 或 `~/.claude/settings.json`；默认 `project`
- `--dry-run`：只打印 unified diff，不写盘
- `--uninstall`：只移除 AGPair 自己管理的条目
- `--force`：在 `statusLine`、`hooks.SessionStart`、`hooks.PreCompact` 冲突时显式覆盖

安全约束：

- 遇到非 AGPair 管理的 `statusLine`，默认拒绝覆盖，除非显式 `--force`
- 遇到 `SessionStart` / `PreCompact` 中的未知 hook，默认拒绝做“智能 merge”；要么报错，要么在 `--force` 下整段替换
- `--uninstall` 只移除 AGPair 自己的条目，不碰无关配置

设计取舍：

- `statusline` 会读取 Claude Code 通过 stdin 传来的 JSON，解析当前 repo / worktree，并输出简短 AGPair 状态。
- `session-start` 会给当前 repo 注入一段很短的 AGPair 提示上下文，提醒主控优先用 AGPair 做长流程任务编排。
- `precompact` 只会在 AGPair 任务处于 `acked` 或 `evidence_ready` 时阻止 compact；其他可见状态可能仍显示在 status line，但不会因此拦截 compact。
- 默认**不**提供 `InstructionsLoaded` 提示 hook，因为 Claude Code 官方把这个事件定义为 observability-only，不能可靠地做上下文提醒。
- 默认**不**提供 `WorktreeCreate` hook，因为这个 hook 会完全替换 Claude Code 内建的 git worktree 行为，默认启用太重。

---

## 7. `task logs`

```bash
agpair task logs TASK-001
```

日志会显示最近的：

- 创建
- 发单
- ACK
- EVIDENCE_PACK
- BLOCKED
- COMMITTED
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

---

## 9. `task abandon`

如果你只是想在本地停止跟踪一个悬挂任务，可以直接：

```bash
agpair task abandon TASK-001 --reason "manual cleanup"
```

这个命令只改本地状态，不会给 Antigravity 发送新消息。

---

## 10. `task wait`

如果你发单时用了 `--no-wait`，可以之后再挂起等待：

```bash
agpair task wait TASK-001
agpair task wait TASK-001 --timeout-seconds 600 --interval-seconds 10
```

退出码 `0` 表示成功（`evidence_ready` / `committed`），`1` 表示失败（`blocked` / `stuck` / `abandoned` / 超时 / **watchdog**）。

现在对于“repo 里其实已经有 commit，但最终 terminal receipt 没回来”的部分 `evidence_ready` 任务，系统可以基于强 repo 证据自动收口。遇到这类情况时，优先查看 `task status --json` / `inspect --json`，而不是默认手动 `abandon`。

当 daemon watchdog 触发（任务仍为 `acked` 但 `retry_recommended=true`）时，
`task wait` 和默认自动等待会提前退出并提示你执行 `agpair task retry <TASK_ID>`。

---

## 11. 自动等待选项

所有发单命令（`start`、`retry`）支持：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--wait / --no-wait` | `--wait` | 发单后等待终态 |
| `--interval-seconds` | `5` | 轮询间隔（秒） |
| `--timeout-seconds` | `3600` | 最大等待时长，故意 > daemon stuck 超时（1800s） |

`status`、`logs`、`wait` 命令**不**带 `--wait/--no-wait`。

---

## 12. 失败姿态

`agpair` 故意偏保守：

- daemon 不会自动发 semantic message
- daemon 不会自动帮你 fresh retry
- `acked` 太久没动静时，会先把 `retry_recommended=true`
- `task wait` 和自动等待在 watchdog 标记后会提前退出（code 1），而不是盲等到硬超时
- 只有到了硬超时，才会标成 `stuck`

---

## 13. 最推荐的命令顺序

对真实任务，建议顺序是：

1. `agpair doctor --repo-path <repo>`
2. `agpair daemon status`
3. `agpair task start ...`（默认会等到终态）
4. `agpair task status <TASK_ID>` 或 `agpair task list`
5. `agpair task logs <TASK_ID>`
6. 只选一个：
   - `retry`
   - `abandon`（仅本地清理）
7. 再看一次 `status` 和 `logs`
