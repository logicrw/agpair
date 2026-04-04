# agpair

![Python](https://img.shields.io/badge/python-≥3.12-blue)
![Platform](https://img.shields.io/badge/platform-macOS-lightgrey)
![License](https://img.shields.io/badge/license-MIT-green)

[English](README.md) | 中文

**agpair** 是一个面向 AI 编程工作流的持久化任务生命周期控制层，适合那种会持续运行几分钟到几小时、需要反复决策推进的工程任务。它让主控 AI（比如 Claude Code、Codex，或其他编码代理）先拆任务，再把任务派给受支持的 executor——目前包括 [Antigravity](https://antigravity.google/)、本地 Codex CLI 和本地 Gemini CLI——并在结果回来后继续做结构化决策。

支持 [Codex](https://openai.com/codex)（CLI 和 Desktop）、[Claude Code](https://docs.anthropic.com/en/docs/claude-code)，以及任何能跑终端命令的工具。

## 为什么需要 agpair？

很多工具很擅长做**一次性委托**：

- 把一个 prompt 发出去
- 等一个结果回来
- 最多再看一下状态或取消它

这很适合快速 rescue、快速 review、一次性小改动。  
但它不适合下面这种更接近真实工程节奏的工作流：

1. 先写一份方案或项目文档
2. 拆成多个任务
3. 把任务一个个派出去，或者在不同 worktree 中并行派发
4. 持续观察任务进度
5. 基于结构化结果决定下一步
6. 遇到卡住、阻塞、需要 fresh resume 时继续推进，而不是丢状态

`agpair` 补的就是这块。

你可以把它理解成：

- **持久化的任务状态层**
- **结构化 receipt 层**
- **continue / approve / reject / retry 的控制层**
- **watchdog / doctor / watch 的运行时控制面**
- **长任务流里节省 token 的状态外置层**

### 为什么这件事真的重要

如果没有 `agpair`，主控 AI 往往要把越来越多的信息硬塞在上下文里：

- 现在做到第几个任务
- 上一个任务的结果是什么
- 哪个任务已经完成
- 哪个任务 `blocked`
- 哪个任务需要 `continue / retry / approve`
- 当前返回到底是成功、失败，还是只是部分证据

这种做法既贵，又脆弱。

`agpair` 把这些状态外置到：

- SQLite task records
- journal
- structured receipts
- `doctor` / `inspect` / `watch`

这样 controller 可以随时查询“当前真实状态”，而不是把整段项目历史一直背在 prompt 里走。

所以更准确地说：

- 如果你只是想**快速把一个任务甩给 Codex**
  - 插件往往更直接
- 如果你想跑的是**长时间、多任务、可恢复、可编排**的工程工作流
  - `agpair` 更合适

**agpair 不是替代你的 AI。**  
它是给你的 AI 一个可持续运行的控制面。

### 当前最佳实践：谁来当主控

`agpair` 本身不绑定某个 controller，但按当前实际使用体验来看：

- **Claude Code** 更适合当长流程主控
  - 拆大任务
  - 持续派发 / 观察 / 决策
  - 在不同 worktree 上管理并行任务
- **Codex** 很适合当 executor，或者做短链路 reviewer / implementation worker，但不如 Claude Code 那样自然地长期盯整条流程

这只是使用建议，不是产品限制：`agpair` 本身仍然保持 controller-agnostic。

### agpair *不是*什么

- 不是语义控制器——语义规划和决策仍由你的 AI 工具负责。
- 不是“一个 slash 命令就完事”的超轻插件——它更接近基础设施/控制面。
- 不是零依赖 runtime——它仍然依赖 `agent-bus`、受支持的 executor，以及适用时的 companion 扩展。

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

如果你经常在同一个 repo 上操作，可以先保存一个本地 target alias，之后直接复用 `--target`：

```bash
agpair target add --name my-project --repo-path /你的项目路径
agpair doctor --target my-project
agpair inspect --target my-project --json
agpair task start --target my-project \
  --body "Goal: 修复 xxx，并返回 EVIDENCE_PACK。"
```

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

agpair v1.0 最初是 AI 编程工具 → Antigravity 的桥接层，现在已经在向多 executor 控制面演进。

已经可用的能力：

- 基于 `agent-bus` 的任务发送，并带自动等待
- 本地 SQLite 持久化 task / receipt / journal
- 续跑流程：`continue`、`approve`、`reject`、`retry`、`abandon`（含严格的 ACK/NACK 确认机制）
- 独立 `task wait`，支持超时和轮询间隔配置
- 流式 `task watch`，用于在终端持续观察任务进展并支持 NDJSON 机器可读模式
- daemon 负责接收回执、维护 session 连续性、标记 stuck
- `inspect` 命令提供统一仓库与任务状态概览，整合 `doctor` 预检查与任务上下文
- 本地 `target` alias 能力，高频命令可以用 `--target <alias>` 代替完整 repo 路径
- `doctor` 预检查（本地健康、desktop 冲突、bridge 健康、并发策略与挂起任务可见性）
- 结构化的 v1 terminal receipts 与带 A2A 状态提示的 JSON CLI 输出
- 任务启动幂等性键 (idempotency keys) 以及结构化的成功/失败上下文
- 内部 `ExecutorAdapter` 抽象层已扩展暴露稳定的 `backend_id`（如 `antigravity` / `codex_cli` / `gemini_cli`），并可在信息只读接口（如 `task status --json` 和 `doctor`）中查看其详情，提高底层透明度。
- `task start --executor codex` 和 `task start --executor gemini` 都已成为正式入口，CLI executor 会走统一的 dispatch / poll / canonical terminal receipt 主路径。
- 增加了正式的延续能力矩阵 (Continuation Capability Matrix)，为不同的后端记录明确的续航策略（例如 Antigravity 支持 `same_session`，Codex CLI 采用 `fresh_resume_first`，Gemini 目前保持保守/有限的 continuation 支持），可通过 `task status --json` 查看。
- 实现了适用于 `fresh_resume_first` 的审查与同意流程，允许以 Codex 为后端的任务通过重新派发干净地继承前置上下文及反馈意见。
- 对于“repo 里其实已经有 commit，但 task 还停在 `evidence_ready`”的情况，已增加基于 repo 强证据的自动收口。
- 后台 daemon 的 stdout/stderr 现在会落盘到 `~/.agpair/daemon.stdout.log` 和 `~/.agpair/daemon.stderr.log`。
- Gemini CLI executor 已接入正式生命周期；目前 continuation 仍保持保守，不默认启用同 session 续跑。

### 为什么它对团队/重度用户有吸引力

`agpair` 的价值不只是“把任务派出去”。

它真正提供的是：

- **长期可运行的控制层**，而不只是一次性委托按钮
- **机器可读的结果**，而不是一大段自然语言完成报告
- **可恢复的执行链路**，任务卡住或 session 死亡时仍有后续动作
- **多 executor 统一治理**，不用为每种工具重新发明一套编排方法
- **更低的长期 token 成本**，因为状态已经被外置，而不是反复塞进上下文

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

### A2A 状态提示 (A2A State Hints)

CLI 的 JSON 输出（`task status`、`task wait` 和 `task watch`）包含一个 `a2a_state_hint` 字段，将内部 phase 近似映射到 A2A 的 `TaskState`（例如将 blocked auth 任务映射为 `auth-required`）。这仅仅是一个供 AI 消费端参考的语义提示对齐——**agpair 并没有实现完整的 A2A 服务端或协议**。它的主要目标依然是做健壮的本地执行桥接层。

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
