# agpair

![Python](https://img.shields.io/badge/python-≥3.12-blue)
![Platform](https://img.shields.io/badge/platform-macOS-lightgrey)
![License](https://img.shields.io/badge/license-MIT-green)

[English](README.md) | 中文

**agpair** 是一个轻量级工具，连接任意 AI 编程工具和 [Antigravity](https://antigravity.google/) 执行器——让你在对话中就能发任务、跟踪进度、审核结果。

支持 [Codex](https://openai.com/codex)（CLI 和 Desktop）、[Claude Code](https://docs.anthropic.com/en/docs/claude-code)，以及任何能跑终端命令的工具。

## 为什么需要 agpair？

当你同时使用 AI 编程工具 + Antigravity 时，"我告诉 AI 工具该做什么"到"Antigravity 执行完毕"之间有一段机械链路需要处理：

- 通过 `agent-bus` 发任务
- 跟踪 task 和 executor session 的映射
- 接收回执（`ACK`、`EVIDENCE_PACK`、`BLOCKED`、`COMMITTED`）
- 检测卡住的任务
- 提供 continue / approve / reject / retry 流程

**agpair 填补了这段空白。** 它是 AI 工具的工具箱——也是你需要直接控制时的手动操作台。

### agpair *不是*什么

- 不是语义控制器——语义决策由你的 AI 工具负责。
- 不是完全自动的 reviewer——你（或你的 AI 工具）来选择下一步。
- 不是零依赖 runtime——它仍然依赖 `agent-bus`、Antigravity 本体，以及仓库内自带的 companion 扩展。

## 前置条件

| 要求 | 说明 |
|------|------|
| **macOS** | 主要测试平台。Linux 未测试，但可能可用 |
| **Python 3.12+** | 运行 `agpair` CLI |
| **Node.js 18+** | 构建 companion 扩展 |
| **`agent-bus`** | 共享消息总线 CLI — 详见下方说明 |
| **[Antigravity](https://antigravity.google/) IDE** | companion 扩展运行在其中 |

### `agent-bus`

`agent-bus` 是 agpair 在 AI 工具（desktop 端）和 Antigravity（executor）之间发送任务和接收回执所使用的本地共享消息总线。它必须在你的 `PATH` 中可用。

> **说明：** `agent-bus` 是 Antigravity 工具链的一部分。如果你使用的是 Antigravity 管理的环境，它应该已经可用。否则，请安装 Antigravity 发行版提供的 `agent-bus` 二进制文件并确保它在 `PATH` 中。目前没有独立的公开包发布——它应该在 Antigravity 安装的环境中就已存在。

### Antigravity IDE

companion 扩展（`companion-extension/`）是一个兼容 VS Code 的扩展，运行在 [Antigravity](https://antigravity.google/) IDE 中。下面用到的 `antigravity --install-extension` 命令是 Antigravity IDE 的 CLI，用于侧载 `.vsix` 扩展，类似 VS Code 中的 `code --install-extension`。

## 快速开始

### 1. 安装 agpair 和 companion 扩展

```bash
git clone https://github.com/logicrw/agpair.git && cd agpair
python3 -m venv .venv && source .venv/bin/activate
python3 -m pip install -e '.[dev]'

# 构建并安装 companion 扩展
cd companion-extension && npm install && npm run package
antigravity --install-extension antigravity-companion-extension-*.vsix
cd ..
```

### 2. 检查环境

```bash
agpair doctor --repo-path /你的项目路径
```

你希望看到 `agent_bus_available=true`、`desktop_reader_conflict=false`、`repo_bridge_session_ready=true`。详见[新手教程](docs/getting-started-zh.md)。

### 3. 开始工作

```bash
agpair daemon start
agpair task start --repo-path /你的项目路径 \
  --body "Goal: 修复 xxx，并返回 EVIDENCE_PACK。"
```

默认情况下，`task start` **会等待**任务进入终态。加 `--no-wait` 可以即发即走。

完整的操作步骤请参考下面的文档链接。

## 架构

```
┌───────────────┐     agpair CLI      ┌─────────────┐     agent-bus      ┌──────────────────┐
│               │  ─────────────────▶  │             │  ───────────────▶  │   Antigravity    │
│   AI Agent    │   task start/wait    │   agpair    │   dispatch/recv    │   (executor)     │
│  (chat UI)    │  ◀─────────────────  │   daemon    │  ◀───────────────  │                  │
│               │   status/receipts    │             │   receipts/ack     │   companion ext  │
└───────────────┘                      └──────┬──────┘                    └──────────────────┘
                                              │
                                         SQLite DB
                                     (tasks, receipts,
                                       journals)
```

**数据流向：** AI 工具 → `agpair task start` → daemon 通过 `agent-bus` 分发 → Antigravity 执行 → companion extension 写回执 → daemon 接收回执 → AI 工具读取状态。

## 实际使用方式

正常情况下，**你不需要手动敲所有 agpair 命令**。

推荐的流程是：

1. 你对 AI 工具说自然语言任务
2. AI 工具在后台调用 `agpair` 命令
3. Antigravity 执行任务
4. `agpair` 保持机械链路稳定

CLI 在手动检查、调试、retry 和 AI 工具不可用时仍然很有价值。

## 可选的 Agent Skill

这个仓库还带了一份可复用的 skill，位于 [skills/agpair/SKILL.md](skills/agpair/SKILL.md)，可用于 Codex、Claude Code 等 AI 工具。

这是对外可分发、可复用的主方案，用来教 AI 工具正确使用 `agpair`：

- 在语义动作前先做 preflight
- 进入 blocking wait 后持续轮询到终态
- 同一个 task 存在 active waiter 时，不要过早干预

对外分发时，这里刻意采用 **skill-first** 方案，不要求别人把 repo 级 `AGENTS.md`、`CLAUDE.md` 或 `GEMINI.md` 复制到他们自己的项目里。

安装方式：

```bash
# Codex
mkdir -p ~/.codex/skills
ln -sfn "$PWD/skills/agpair" ~/.codex/skills/agpair

# Claude Code
mkdir -p ~/.claude/skills
ln -sfn "$PWD/skills/agpair" ~/.claude/skills/agpair
```

然后重启 AI 工具或新开一个窗口即可。

这会提升 Antigravity 派活场景下的自动触发概率。如果你想要更确定地触发，prompt 里可以直接写 `use agpair`。

## 当前状态

agpair v1.0 是一个专注于 AI 编程工具 → Antigravity 任务发送的工具。

已经可用的能力：

- 基于 `agent-bus` 的任务发送，并带自动等待
- 本地 SQLite 持久化 task / receipt / journal
- 续跑流程：`continue`、`approve`、`reject`、`retry`、`abandon`
- 独立 `task wait`，支持超时和轮询间隔配置
- daemon 负责接收回执、维护 session 连续性、标记 stuck
- `doctor` 预检查（本地健康、desktop 冲突、bridge 健康）

明确不在范围内的：

- 替代 AI 工具做语义控制
- 隐藏所有操作边界

## 文档导航

| 语言 | 文档 | 说明 |
|------|------|------|
| English | [Getting Started](docs/getting-started.en.md) | Step-by-step beginner guide |
| English | [Command Reference](docs/usage.md) | Full CLI reference |
| 中文 | [新手教程](docs/getting-started-zh.md) | 详细入门指南 |
| 中文 | [命令参考](docs/usage.zh-CN.md) | 中文命令参考 |

## 仓库结构

```
agpair/
├── agpair/                 # Python CLI 包
├── companion-extension/    # 自带的 Antigravity companion 扩展 (TypeScript)
│   ├── src/                # 扩展源码
│   ├── package.json
│   └── esbuild.js
├── skills/
│   └── agpair/             # 可选的 Agent skill 包
├── tests/                  # Python 集成测试
├── docs/                   # 文档
└── pyproject.toml
```

这是一个**单一自包含仓库**。不需要额外检出其他项目。

## 重要使用须知

### 并行控制规则（单工作区单任务）

不支持在同一代码库的同一工作区中进行并发编辑。你必须保持**每个工作区只能存在一个活跃的受托任务**。若需并行处理任务，请使用单独的 `git worktree` 或克隆一份独立的仓库。现在，`agpair doctor` 会显式公开此并发策略，并展示当前挂起任务的数量与 ID，以便自动化工具正确隔离任务。

### Desktop 回执独占

agpair 消费 `code -> desktop` 回执。如果还有别的 desktop watcher 在抢同一批回执，`agpair doctor` 会报 `desktop_reader_conflict=true`，daemon 会拒绝启动。先停掉那个 watcher。

### 一个任务只让一个窗口主控

你可以开多个 AI 工具窗口，但不要让两个窗口同时对**同一个** `TASK_ID` 发 `continue / approve / reject / retry`。原则：一个 active task → 一个主控窗口。

### daemon 不是第二个大脑

daemon 只做机械工作（收回执、维护连续性、标记 stuck）。它不审核代码，也不做语义判断。

### `doctor` 是预检，不是每步都要跑

在开始新任务、切 repo、重启 daemon、排查卡住任务时跑。不需要每次 `status` 或 `logs` 前都跑一遍。

### Bridge 安全性

companion 扩展的 HTTP bridge 仅监听 `127.0.0.1`。**默认情况下，bridge 使用自动生成的 bearer token 进行保护**，token 存储在 VS Code 的 SecretStorage 中。修改性端点（`/run_task`、`/continue_task`、`/write_receipt` 等）需要有效的 `Authorization: Bearer <token>` 头；只读端点（`/health`、`/task_status`）无需认证即可访问，以保证 `agpair doctor` 开箱即用。

token 在首次激活时自动生成并安全持久化——正常使用不需要手动配置。你可以通过 `antigravityCompanion.bridgeToken` IDE 设置覆盖 token。仅在本地调试时，可以设置 `antigravityCompanion.bridgeInsecure = true` 来禁用认证——**不建议日常使用**，因为这会允许任何本地进程调用 bridge 的修改性端点。请求体大小限制为 1 MiB。

## macOS 开机自启（可选）

```bash
# 安装
python3 -m agpair.tools.install_agpair_daemon_launchd install \
  --agpair-home ~/.agpair

# 查看状态
python3 -m agpair.tools.install_agpair_daemon_launchd status

# 卸载
python3 -m agpair.tools.install_agpair_daemon_launchd uninstall
```

## 常见问题

### `desktop_reader_conflict=true`

还有别的 desktop watcher 在抢回执。先停掉它，再启动 `agpair daemon`。

### `repo_bridge_session_ready=false`

目标 repo 的 Antigravity 窗口不健康。确认打开的是正确的 repo，Reload/重启 Antigravity 窗口，再跑 `agpair doctor --repo-path ...`。

### `BLOCKED`

这轮执行没有成功。跑 `agpair task logs <TASK_ID>` 查看原因，然后决定是 `continue` 同一 session 还是 `retry` 换新的。

## License

MIT
