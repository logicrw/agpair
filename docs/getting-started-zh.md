# agpair 新手教程

这份教程带你从零开始到成功发出第一个任务。

> **核心要点**：正常使用时，你对 AI 编程工具（Codex、Claude Code 等）说自然语言，它在后台调用 `agpair`。只有在你想手动检查状态、调试或接管时，才需要直接用 CLI。

## 前置条件

| 要求 | 说明 |
|------|------|
| **macOS** | 主要测试平台。Linux 未测试，但可能可用 |
| **Python 3.12+** | 运行 `agpair` CLI |
| **Node.js 18+** | 构建 companion 扩展 |
| **`agent-bus`** | 共享消息总线 CLI — 必须在 `PATH` 中可用 |
| **[Antigravity](https://antigravity.google/) IDE** | companion 扩展运行在其中 |

### 什么是 `agent-bus`？

`agent-bus` 是 agpair 在 Antigravity 执行路径中使用的本地消息总线。如果你使用 Antigravity 作为 executor，它必须在 `PATH` 中可用。它是 Antigravity 工具链的一部分。如果你使用的是 Antigravity 管理的环境，它应该已经可用。否则，请安装 Antigravity 发行版提供的 `agent-bus` 二进制文件并确保它在 `PATH` 中。目前没有独立的公开包发布。

如果你使用的是 `--executor codex` 或 `--executor gemini`，agpair 的生命周期控制仍然一样，只是底层 executor 变成了本地 CLI 进程，而不是 Antigravity session。本教程后续仍以 Antigravity 示例为主，因为它的运行时表面最完整。

当前可选的 executor 包括：

- `antigravity`
- `codex`
- `gemini`

### 什么是 Antigravity IDE？

[Antigravity](https://antigravity.google/) IDE 是一个兼容 VS Code 的 IDE，为 agpair 任务提供执行环境。本仓库中的 companion 扩展（`companion-extension/`）运行在其中，提供 `agpair` CLI 和 Antigravity 执行能力之间的 HTTP bridge。下面用到的 `antigravity --install-extension` 命令是 Antigravity IDE 的 CLI，用于侧载 `.vsix` 扩展，类似 VS Code 中的 `code --install-extension`。

## 第 1 步：安装 agpair

```bash
git clone https://github.com/logicrw/agpair.git agpair
cd agpair
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e '.[dev]'
```

让 `agpair` 全局可用（任何 AI 编程工具都能直接调用）：

```bash
ln -sf "$PWD/.venv/bin/agpair" ~/.local/bin/agpair
which agpair   # 应该输出 ~/.local/bin/agpair
```

### 第 1.5 步：安装 companion 扩展

companion 扩展提供 `agpair` CLI 和 [Antigravity](https://antigravity.google/) IDE 之间的 HTTP bridge。它已经包含在这个仓库中：

```bash
cd companion-extension
npm install
npm run build
npm run package
antigravity --install-extension antigravity-companion-extension-*.vsix
cd ..
```

安装后重新加载 Antigravity 窗口。扩展在启动时自动激活。

> **安全提示**：Bridge 仅监听 `127.0.0.1`。默认情况下，bridge 使用自动生成的 bearer token 进行保护，token 存储在 VS Code 的 SecretStorage 中——无需手动配置。修改性端点（`/run_task`、`/write_receipt` 等）需要有效的 `Authorization: Bearer <token>` 头；只读端点（`/health`、`/task_status`）无需认证即可访问，以保证 `agpair doctor` 开箱即用。仅在本地调试时，可以设置 `antigravityCompanion.bridgeInsecure = true` 来禁用认证——不建议日常使用。请求体大小限制为 1 MiB。

## 第 2 步：确认 agent-bus 可用

```bash
agent-bus --help
```

如果不通过，先安装或配置 `agent-bus`。参见上方的[前置条件](#什么是-agent-bus)了解如何获取。agpair 依赖它来发任务。

## 第 3 步：检查目标项目健康状态

发任务前，先确认环境就绪：

```bash
agpair doctor --repo-path /你的项目路径
```

最重要的三个字段：

| 字段 | 期望值 |
|------|--------|
| `agent_bus_available` | `true` |
| `desktop_reader_conflict` | `false` |
| `repo_bridge_session_ready` | `true` |

**`doctor` 输出示例（健康状态）：**

```
agpair doctor — target: /Users/you/projects/my-app

  agent_bus_available ............ true
  desktop_reader_conflict ........ false
  repo_bridge_session_ready ...... true
  bridge_url ..................... http://127.0.0.1:8765

All checks passed.
```

**如果 `desktop_reader_conflict=true`**：还有别的 desktop watcher 在抢回执，先停掉它再继续。

**如果 `repo_bridge_session_ready=false`**：目标 repo 的 Antigravity 窗口不健康。确认打开的是正确的项目，Reload/重启 Antigravity 窗口，再跑一次 `doctor`。

## 第 4 步：启动 daemon

```bash
agpair daemon start
agpair daemon status
```

daemon 是一个轻量后台进程，负责：

- 接收回执（`ACK`、`EVIDENCE_PACK`、`BLOCKED`、`COMMITTED`）
- 检测卡住任务（soft watchdog → hard timeout）

它**不是**语义审核者——不解读代码，也不做决策。

## 第 5 步：发送你的第一个任务

```bash
agpair task start \
  --repo-path /你的项目路径 \
  --body "Goal: 修复 xxx，并返回 EVIDENCE_PACK。"
```

命令会返回一个 `TASK_ID`，并默认**等待**任务进入终态。

**输出示例：**

```
Task created: TASK-MY-APP-FIX-BUG-20260324-01
Waiting for terminal phase ...
Phase changed: new → acked
Phase changed: acked → evidence_ready
Task reached terminal phase: evidence_ready
```

如果想即发即走：

```bash
agpair task start \
  --repo-path /你的项目路径 \
  --body "Goal: ..." \
  --no-wait
```

## 第 6 步：查看任务状态

```bash
agpair task status <TASK_ID>
agpair task logs <TASK_ID>
```

**`task status` 输出示例：**

```
task_id:    TASK-MY-APP-FIX-BUG-20260324-01
phase:      evidence_ready
attempt_no: 1
session_id: sess-abc123
created_at: 2026-03-24T10:00:00Z
```

### 任务阶段

| 阶段 | 含义 |
|------|------|
| `new` | 任务已创建，尚未收到 ACK |
| `acked` | Antigravity 已接单，建立了执行 session |
| `evidence_ready` | Antigravity 返回了 `EVIDENCE_PACK`——去看 logs |
| `blocked` | 执行失败，有阻塞原因 |
| `committed` | 任务已完成提交 |
| `stuck` | 长时间无进展，daemon 标记为卡住 |

## 第 7 步：选择下一步动作

看完 `task logs` 后，只选一个：

```bash
# 换 fresh session 重试
agpair task retry <TASK_ID> --body "换 fresh session 重试"

# 停止本地跟踪（不通知 Antigravity）
agpair task abandon <TASK_ID> --reason "不再需要了"
```

## 第 8 步：配合 AI 编程工具使用（正常流程）

日常使用中，推荐的流程是：

1. 你对 AI 工具（Codex、Claude Code 等）说自然语言任务
2. 工具调用 `agpair doctor`、`task start`、`task status` 等
3. Antigravity 执行工作
4. 你审核结果，给出下一步指令

CLI 是手动辅助工具，适用于：

- 直接检查卡住的任务
- 列出本地跟踪的所有任务
- 手动 retry 或 abandon
- 确认 bridge 是否健康
- AI 工具不可用时自己接管

## 常见问题

### `desktop_reader_conflict=true`

还有别的 desktop watcher 在抢回执。先停掉它，再启动 `agpair daemon`。

### `repo_bridge_session_ready=false`

目标 repo 的 Antigravity 窗口不健康。确认打开的是正确的 repo，Reload/重启窗口，再跑 `agpair doctor`。

### `BLOCKED`

这轮执行没有成功。跑 `agpair task logs <TASK_ID>` 看原因，然后通过 `retry` 换新的 session 重试。

## 可选：让 daemon 开机自启

```bash
# 安装 launchd agent
python3 -m agpair.tools.install_agpair_daemon_launchd install \
  --agpair-home ~/.agpair

# 查看状态
python3 -m agpair.tools.install_agpair_daemon_launchd status

# 卸载
python3 -m agpair.tools.install_agpair_daemon_launchd uninstall
```

这完全是可选的——先用 `agpair daemon start` 手动启动，熟悉流程后再决定要不要常驻。

## 最实用的建议

- **一个时间段只让 `agpair` 接管一套项目**
- **先检查 `doctor`，再发任务**
- **大多数时间只看 `status` 和 `logs`**
- **复杂判断留在 AI 工具的聊天窗口里做**

不要把它想成"全自动平台"，而是：**一个让 AI 编程工具更稳定地驱动 Antigravity 的轻量控制台。**

## 任务元数据与并发

`agpair` 支持并发任务执行，但你必须遵守**并发建议：永远在跨 worktree 间做并发，不要在同一个 worktree 内并发**。

为了更好地编排并行工作，AI 主控可以在任务中记录执行元数据：包括 `depends_on`、`isolated_worktree`、`worktree_boundary`、`setup_commands`、`teardown_commands`、`env_vars` 以及 `spotlight_testing`。

*注意：这些字段当前仅为供主控器阅读的元数据（metadata-only）。它们持久化存储，帮助 AI 安全地计划并发任务路线，但 agpair daemon 目前不会在底层运行时自动强制执行这些字段（比如自动运行 setup 脚本）。*
