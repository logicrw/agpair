# agpair

![Python](https://img.shields.io/badge/python-≥3.12-blue)
![Platform](https://img.shields.io/badge/platform-macOS-lightgrey)
![License](https://img.shields.io/badge/license-MIT-green)

[English](README.md) | 中文

**agpair** 是一个面向 AI 编程工作流的持久化编排层：把工作拆成任务，派给受支持的 executor，追踪结构化结果，从失败中恢复，并让长时间项目推进时不必把所有状态都塞进聊天上下文。当前支持 [Antigravity](https://antigravity.google/)、本地 Codex CLI 和本地 Gemini CLI。

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
- **retry 的控制层**
- **watchdog / doctor / watch 的运行时控制面**
- **长任务流里节省 token 的状态外置层**

### 为什么这件事真的重要

如果没有 `agpair`，主控 AI 往往要把越来越多的信息硬塞在上下文里：

- 现在做到第几个任务
- 上一个任务的结果是什么
- 哪个任务已经完成
- 哪个任务 `blocked`
- 哪个任务需要 `retry`
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

`agent-bus` 是 agpair 在 Antigravity 执行路径中使用的本地消息总线。如果你要把 Antigravity 作为 executor，就必须保证它在 `PATH` 中可用。

> **说明：** `agent-bus` 是 Antigravity 工具链的一部分。如果你只使用 `codex` / `gemini` executor，agpair 的生命周期仍然成立，只是不会走 Antigravity 专属的 transport 路径。如果你希望 Antigravity 可用作 executor，请确保 `agent-bus` 在 `PATH` 中可用。

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
  --body "Goal: 修复 xxx。Scope: 只有文件 Y。Required changes: 更新行 Z。Exit criteria: 成功返回 EVIDENCE_PACK。"
```

默认情况下，`task start` **会等待**任务进入终态。加 `--no-wait` 可以即发即走。

如果你经常在同一个 repo 上操作，可以先保存一个本地 target alias，之后直接复用 `--target`：

```bash
agpair target add --name my-project --repo-path /你的项目路径
agpair doctor --target my-project
agpair inspect --target my-project --json
agpair task start --target my-project \
  --body "Goal: 修复 xxx。Scope: 只有文件 Y。Required changes: 更新行 Z。Exit criteria: 成功返回 EVIDENCE_PACK。"
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

**数据流向：** 主控 AI → `agpair task start` → agpair 分发到所选 executor → executor 返回结构化进度 / 终态 → agpair 持久化状态 → 主控 AI 通过 `status/watch/inspect` 继续推进。

## 实际使用方式

正常情况下，**你不需要手动敲所有 agpair 命令**。

推荐的流程是：

1. 你对 AI 工具说自然语言任务
2. AI 工具在后台调用 `agpair` 命令
3. Antigravity 执行任务
4. `agpair` 保持机械链路稳定

CLI 在手动检查、调试、retry 和 AI 工具不可用时仍然很有价值。

## 可选的 Agent Skill

这个仓库在 `skills/` 下带了两份可复用的 skill：

- [skills/Claude/SKILL.md](skills/Claude/SKILL.md) —— 面向 Claude 的工作流
- [skills/Codex/SKILL.md](skills/Codex/SKILL.md) —— 面向 Codex 的工作流

这是对外可分发、可复用的主方案，用来教 AI 工具正确使用 `agpair`：

- 在语义动作前先做 preflight
- 进入 blocking wait 后持续轮询到终态
- 同一个 task 存在 active waiter 时，不要过早干预

对外分发时，这里刻意采用 **skill-first** 方案，不要求别人把 repo 级 `AGENTS.md`、`CLAUDE.md` 或 `GEMINI.md` 复制到他们自己的项目里。

安装方式：

```bash
# Codex
mkdir -p ~/.codex/skills/agpair
cp "$PWD/skills/Codex/SKILL.md" ~/.codex/skills/agpair/SKILL.md

# Claude Code
mkdir -p ~/.claude/skills/agpair
cp "$PWD/skills/Claude/SKILL.md" ~/.claude/skills/agpair/SKILL.md
```

然后重启 AI 工具或新开一个窗口即可。

这会提升 Antigravity 派活场景下的自动触发概率。如果你想要更确定地触发，prompt 里可以直接写 `use agpair`。

## 默认 Executor 配置

`task start` 的 executor 解析顺序是：

1. 显式 `--executor`
2. target 级 `default_executor`
3. `AGPAIR_DEFAULT_EXECUTOR`
4. 产品回退（`antigravity`）

这是**产品层**的统一解析顺序，对所有 controller 都一样。

它和各个 skill 里的**推荐策略**不是一回事：

- 面向 Claude 的工作流通常推荐：
  - 单工作区：`antigravity`
  - 并行 / 隔离 worktree：`codex`，再 `gemini`
- 面向 Codex 的工作流通常推荐：
  - 单工作区：`antigravity`
  - 并行 / 隔离 worktree：`gemini`
  - 只有明确要求时才用 `codex` 作为 executor

也就是说：

- 产品层决定的是：**没有显式 `--executor` 时最终会落到谁**
- skill 决定的是：**controller 平时应该优先选谁**

示例：

```bash
export AGPAIR_DEFAULT_EXECUTOR=codex

agpair target add \
  --name my-project \
  --repo-path /你的项目路径 \
  --default-executor gemini
```

如果是 Codex 做主控，更常见的设置是：

```bash
export AGPAIR_DEFAULT_EXECUTOR=antigravity
```

需要并行或隔离任务时，再显式写 `--executor gemini`。

## 当前状态

agpair v1.0 最初是 AI 编程工具 → Antigravity 的桥接层，现在已经在向多 executor 控制面演进。

已经可用的能力：

- 基于 `agent-bus` 的任务发送，并带自动等待
- 本地 SQLite 持久化 task / receipt / journal
- 重新执行流程：`retry`、`abandon`（含严格的 ACK/NACK 确认机制）
- 独立 `task wait`，支持超时和轮询间隔配置
- 流式 `task watch`，用于在终端持续观察任务进展并支持 NDJSON 机器可读模式
- daemon 负责接收回执、标记 stuck
- `inspect` 命令提供统一仓库与任务状态概览，整合 `doctor` 预检查与任务上下文
- 本地 `target` alias 能力，高频命令可以用 `--target <alias>` 代替完整 repo 路径
- `doctor` 预检查（本地健康、desktop 冲突、bridge 健康、并发策略与挂起任务可见性）
- 结构化的 v1 terminal receipts 与带 A2A 状态提示的 JSON CLI 输出
- 任务启动幂等性键 (idempotency keys) 以及结构化的成功/失败上下文
- 面向 Claude Code 的辅助命令：可直接接 `statusLine`、`SessionStart`、`PreCompact` 的 `agpair claude ...`
- 增加了基础的任务依赖、并发模型、Setup/Teardown Hook、环境隔离元数据以及本地化焦点测试提示，允许控制平面记录任务依赖 (`depends_on`)、并行隔离意图 (`isolated_worktree`)、声明环境隔离边界 (`worktree_boundary`)、执行前后置守卫命令 (`setup_commands` / `teardown_commands`)、每任务环境变量配置 (`env_vars`) 以及优先运行局部测试而非全量测试的意图 (`spotlight_testing`)。
- 内部 `ExecutorAdapter` 抽象层已扩展暴露稳定的 `backend_id`（如 `antigravity` / `codex_cli` / `gemini_cli`），并可在信息只读接口（如 `task status --json` 和 `doctor`）中查看其详情，提高底层透明度。
- `task start --executor codex` 和 `task start --executor gemini` 都已成为正式入口，CLI executor 会走统一的 dispatch / poll / canonical terminal receipt 主路径。
- 增加了正式的 Executor Safety Metadata 以强制各后端适配器声明明确且保守的执行安全属性（如 `is_mutating`、`is_concurrency_safe`、`requires_human_interaction`）。
- 对于“repo 里其实已经有 commit，但 task 还停在 `evidence_ready`”的情况，已增加基于 repo 强证据的自动收口。
- 后台 daemon 的 stdout/stderr 现在会落盘到 `~/.agpair/daemon.stdout.log` 和 `~/.agpair/daemon.stderr.log`。
- Gemini CLI executor 已接入正式生命周期。

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
| 中文 | [Claude Code 集成研究](docs/claude-code-integration.zh-CN.md) | 近半年功能更新梳理与 AGPair 融合建议 |

## Claude Code 集成

如果你把 Claude Code 当主控，当前推荐同时启用两层集成：

1. **Skill 层**：安装仓库自带的 `skills/agpair`，让 Claude Code 学会什么时候该走 `agpair task start/watch/wait`。
2. **MCP 层**：运行 `agpair-mcp`，让 Claude Code 直接通过结构化工具调用 `agpair`，而不是只能拼 shell 命令。

从 2025-12 到 2026-04 的 Claude Code 更新看，MCP、任务系统、worktree、memory / recap、Monitor 工具、status line、agent teams 都在持续增强。`agpair` 更应该顺着这条路线把自己做成 **Claude Code 的持久化外部控制面**，而不是只停留在一份 skill 上。

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

### 并行与并发控制

不支持在同一代码库的同一工作区中进行并发编辑。你必须保持**每个工作区只能存在一个活跃的受托任务**。

**并发建议：** 永远在跨 worktree 间做并发，不要在同一个 worktree 内并发。

你可以使用任务元数据来辅助编排并行执行：`depends_on`、`isolated_worktree`、`worktree_boundary`、`setup_commands`、`teardown_commands`、`env_vars` 以及 `spotlight_testing`。
*注意：这些当前仅为供主控器识别的元数据。它们会持久化并在 `status`/`inspect` 中显示，但 agpair 不会在运行时强制执行这些逻辑。*

### Desktop 回执独占

agpair 消费 `code -> desktop` 回执。如果还有别的 desktop watcher 在抢同一批回执，`agpair doctor` 会报 `desktop_reader_conflict=true`，daemon 会拒绝启动。先停掉那个 watcher。

### 一个任务只让一个窗口主控

你可以开多个 AI 工具窗口，但不要让两个窗口同时对**同一个** `TASK_ID` 发 `retry`。原则：一个 active task → 一个主控窗口。

### daemon 不是第二个大脑

daemon 只做机械工作（收回执、维护连续性、标记 stuck）。它不审核代码，也不做语义判断。

### `doctor` 是预检，不是每步都要跑

在开始新任务、切 repo、重启 daemon、排查卡住任务时跑。不需要每次 `status` 或 `logs` 前都跑一遍。

### Bridge 安全性

companion 扩展的 HTTP bridge 仅监听 `127.0.0.1`。**默认情况下，bridge 使用自动生成的 bearer token 进行保护**，token 存储在 VS Code 的 SecretStorage 中。修改性端点（`/run_task`、`/write_receipt` 等）需要有效的 `Authorization: Bearer <token>` 头；只读端点（`/health`、`/task_status`）无需认证即可访问，以保证 `agpair doctor` 开箱即用。

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

这轮执行没有成功。跑 `agpair task logs <TASK_ID>` 查看原因，然后通过 `retry` 换新的 session 重试。默认情况下 `logs` 会过滤掉高频的过渡性日志，如需查看完整记录可添加 `--all` 参数。

## License

MIT
