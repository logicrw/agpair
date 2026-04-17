# Claude Code 近半年更新与 AGPair 融合研究

> 研究日期：2026-04-16  
> 范围：官方公开 release / changelog / docs  
> 核心问题：Claude Code 最近半年的哪些新能力，值得被 AGPair 吸收，以增强 AGPair 本身，并让 Claude Code 通过 AGPair 发挥更强效果？

## 结论

AGPair 的核心不是“一次性委托”，而是 **长流程 AI 编程任务的持久化编排**：

- controller / executor 机械解耦
- task / receipt / retry / stuck 状态外置
- 多任务、多 worktree、多 executor 的恢复控制面

Claude Code 最近半年的演进方向，则是在持续强化 controller 能力：

- 任务系统与依赖
- worktree / subagent / agent teams
- MCP / hook / plugin / skill
- memory / recap / compaction
- Monitor / status line / Remote Control

因此最佳组合不是让 AGPair 去复制 Claude Code，而是：

**让 AGPair 成为 Claude Code 的 durable execution layer 与 external state layer。**

## 研究方法

我核对了 `anthropics/claude-code` 官方公开发布面：

- GitHub Releases / CHANGELOG：在最近半年窗口内，公开可见的版本从 **2025-12-19** 到 **2026-04-15**
- 这段窗口内共有 **87 个 release**
- 我逐条过了一遍 release body / changelog，再按能力域而不是版本号去做归类

这里的“系统性”不是把 87 个版本逐条抄下来，而是按能力域归并，再筛掉纯 UI 修补和纯 bugfix，只保留会改变 AGPair 产品路径的部分。

## 最小可用接入方式

如果你现在就想把 Claude Code 和 AGPair 接起来，最小可用组合是：

### 1. 安装 skill

```bash
mkdir -p ~/.claude/skills
ln -sfn "/绝对路径/agpair/skills/agpair" ~/.claude/skills/agpair
```

### 2. 添加 AGPair MCP server

按 Claude Code 官方 MCP 文档，最稳妥的本地 stdio 接入方式是：

```bash
claude mcp add --transport stdio agpair -- agpair-mcp
```

如果你希望把这份 MCP 配置随项目共享，可以改成 project scope：

```bash
claude mcp add --transport stdio --scope project agpair -- agpair-mcp
```

### 3. 主控侧推荐工作流

- 让 Claude Code 用 skill 判断何时应走 AGPair
- 一旦进入 AGPair 任务流，就优先通过 MCP 调 `agpair_start_task` / `agpair_list_tasks` / `agpair_inspect_repo`
- dispatch 之后，用 Claude Code 的 Monitor tool 跟 `agpair task watch <TASK_ID> --json`

## AGPair 现在的真实定位

结合仓库本身的 README、usage、research 文档与代码实现，AGPair 当前定位很清楚：

- 主控 AI 通常是 Claude Code 这类 agent
- AGPair 负责 `task start/status/watch/wait/retry/abandon`
- 状态持久化在 SQLite
- 结果和终态通过 journal / receipt / terminal receipt 外置
- daemon 负责 ingest / watchdog / stuck 标记 / repo evidence closeout
- executor 目前支持 Antigravity、Codex CLI、Gemini CLI
- 并发边界明确：**永远跨 worktree 并发，不要在同一 worktree 内并发编辑**

这意味着两者的最佳耦合点不是更花哨的命令封装，而是明确分层：

- Claude Code 负责语义控制与长流程决策
- AGPair 负责任务持久化、结果治理与执行边界

## 近半年最重要的 Claude Code 能力变化

下面只列和 AGPair 路径真正相关的高价值变化。

### 1. 任务系统与并行执行面显著增强

高价值版本锚点：

- `v2.1.16`（2026-01-22）：新增 task management system，并明确提到 dependency tracking
- `v2.1.20`（2026-01-27）：`TaskUpdate` 可以删除任务
- `v2.1.49`（2026-02-18）：`--worktree`、subagent `isolation: "worktree"`、background agents
- `v2.1.98`（2026-04-09）：新增 Monitor tool，可流式消费后台脚本事件
- `v2.1.105`（2026-04-13）：`EnterWorktree` 新增 `path` 参数，可切换到当前仓库已有 worktree

对 AGPair 的意义：

- Claude Code 现在已经不只是“单线程对话 agent”
- 它已经具备自己的任务概念、后台任务、worktree 隔离和并行执行语义
- AGPair 不该和它竞争任务概念，而应该接住这些任务，把 durable state 放在外面

最直接的融合点是：

- 让 Claude Code 通过 MCP 直接创建 / 查询 / 检查 AGPair 任务
- 让 AGPair 的 `depends_on` / `isolated_worktree` / `worktree_boundary` 更自然地接到 Claude Code task/subagent/worktree 流程

### 2. MCP / Skill / Hook / Plugin 面已经成为第一公民

高价值版本锚点：

- `v2.0.74`（2025-12-19）：新增 LSP tool
- `v2.1.3`（2026-01-09）：slash commands 与 skills 合并
- `v2.1.7`（2026-01-14）：MCP tool search auto mode 默认开启
- `v2.1.9`（2026-01-16）：skills 可拿到 `${CLAUDE_SESSION_ID}`，hooks 可返回 `additionalContext`
- `v2.1.49` 附近：新增 `TaskCreated` / `WorktreeCreate` hooks
- `v2.1.91`（2026-04-02）：MCP tool result persistence override，可把大结果上限提升到 500K 字符
- `v2.1.105`（2026-04-13）：PreCompact hook 可阻止 compaction；插件支持 background monitors
- `v2.1.108`（2026-04-14）：内建 slash commands 可通过 Skill tool 被模型发现并调用

对 AGPair 的意义：

- AGPair 不能只停留在 `skills/agpair` 这一层
- Claude Code 官方正在把 **MCP + Skill + Hook + Plugin** 作为统一能力面
- 所以 AGPair 至少应该同时拥有：
  - Skill：告诉 Claude Code 什么时候该用 AGPair
  - MCP：让 Claude Code 结构化调用 AGPair
  - Hook / plugin-ready surface：为后续自动化留接口

### 3. Memory / recap / partial summarization 开始和长流程强相关

高价值版本锚点：

- `v2.1.84`（2026-03-26）：Claude 自动记录与召回 memories；支持 “Summarize from here”
- `v2.1.101`（2026-04-10）：`/team-onboarding` 可根据本地使用历史生成队友上手指南
- `v2.1.108`（2026-04-14）：新增 recap feature，支持回到 session 时自动补上下文
- `v2.1.110`（2026-04-15）：telemetry 关闭场景下也支持 session recap

对 AGPair 的意义：

- Claude Code 越来越适合“长期担任 controller”
- 但 Claude 的 memory / recap 更擅长保存**语义性上下文**
- AGPair 仍然更适合保存**机械性任务状态**

更合理的边界是分层：

- Claude memory / CLAUDE.md / rules：保存偏好、语义约束、项目惯例
- AGPair：保存 task lifecycle、executor state、receipt、retry/stuck 证据

### 4. Status line / Monitor / Remote Control 让“持续盯流程”更可用

高价值版本锚点：

- `v2.1.97` / `v2.1.98`：`workspace.git_worktree` 进入 status line JSON；Monitor tool 上线
- `v2.1.101`：远程 session 自动创建默认 cloud environment
- `v2.1.110`：`/tui`、`/focus`、push notification、Remote Control 端更多命令可用

对 AGPair 的意义：

- Claude Code 现在更适合做“长时间盯盘的主控”
- AGPair 需要把自己的状态喂给 Claude Code 的 status line / monitor / Remote Control，而不是要求用户手工反复敲 CLI

### 5. agent teams 说明 Claude Code 正在从单 agent 向 orchestration 演进

高价值版本锚点：

- `v2.1.84`（2026-03-26）：research preview agent teams
- 官方 docs 明确说明 agent teams 是多 session、多上下文窗口、可相互发消息的协作模型

对 AGPair 的意义：

- Claude Code 正在长出自己的“队伍编排层”
- AGPair 不适合去和 agent teams 做同构竞争
- AGPair 更适合做这些 team / subagent / background worker 的 durable backend

## 从研究到产品决策：哪些要立刻融合，哪些不要

### 立即融合

#### A. 把 AGPair 的 MCP 面做强

原因：

- Claude Code 近半年最稳定、最官方、最可组合的扩展面就是 MCP
- 技能层只能给策略，MCP 才能给结构化调用
- Claude Code 自己也在持续增强 MCP 的发现、可靠性和大结果处理

当前仓库已落地：

- `agpair_start_task` 现在支持结构化参数，而不是要求调用方手工拼 JSON 字符串
  - `executor`
  - `depends_on`
  - `isolated_worktree`
  - `setup_commands`
  - `teardown_commands`
  - `env_vars`
  - `worktree_boundary`
  - `spotlight_testing`
- 新增 `agpair_list_tasks` / `agpair_inspect_repo` / `agpair_doctor`
- 新增 `agpair claude config` / `agpair claude statusline` / `agpair claude hook session-start` / `agpair claude hook precompact`

#### B. 把 `task list` 变成机器可读、可按 repo 收敛

原因：

- Claude Code 现在有 status line、Monitor、TaskList、自身 task system
- AGPair 若不能稳定吐出“某个 repo 下有哪些活任务”的 JSON，就没法被这些能力自然消费

当前仓库已落地：

- `agpair task list --json`
- `agpair task list --repo-path ...`
- `agpair task list --target ...`

### 值得排进下一阶段

#### C. 官方 Claude Code status line 集成

已完成第一版：

- `agpair claude statusline`
- `agpair claude config` 会吐出可直接接到 Claude Code `statusLine` 的 settings 片段

建议做法：

- 用 Claude Code 的 `statusLine` 命令脚本读取 stdin JSON
- 基于 `workspace.current_dir` / `workspace.git_worktree`
- 调 `agpair task list --repo-path ... --json --limit 1`
- 在底栏显示：
  - 当前 repo 是否有 active AGPair task
  - phase / task_id
  - 是否处于 linked worktree

这样主控在 Claude Code 里就能直接看到 AGPair 外部状态，而不必主动查询。

#### D. 用 Monitor tool 绑定 `agpair task watch --json`

当前状态：

- `skills/agpair/SKILL.md` 已把 `Monitor("agpair task watch <TASK_ID> --json")` 作为标准动作
- `agpair claude hook session-start` 会把这条建议以简短上下文注入 Claude Code

建议做法：

- Claude Code dispatch 完 AGPair 任务后
- 自动开 Monitor 跟 `agpair task watch <TASK_ID> --json`
- 把状态变化流式反馈回主控

这能把 AGPair durable state 与 Claude Code 后台监控能力真正接起来。

#### E. 用 hooks 做轻量自动化，而不是重自动编排

当前实现取舍：

- 已落地 `SessionStart`：实际承担“提醒当前 repo 已启用 AGPair 协议”的职责
- 已落地 `PreCompact`：有活任务时阻止 compact
- 暂不默认落地 `InstructionsLoaded`：官方定义上只能做 observability，不能稳定注入提醒
- 暂不默认落地 `WorktreeCreate`：该 hook 会完全替换 Claude Code 内建 git worktree 行为，默认太重
- 暂不默认落地 `TaskCreated` → AGPair 映射：当前官方 hook 输入不足以安全建立原生 task 与 AGPair task 的稳定映射

### 暂时不要急着做

#### F. 不要让 AGPair 去复制 Claude Code 的 agent teams

原因：

- Claude Code 官方已经在做这条线
- AGPair 更适合做 durable orchestration backend，而不是 UI / session team manager

#### G. 不要急着做全自动 hook 编排闭环

原因：

- hook 自动化一旦过深，错误边界会很难排查
- AGPair 当前更需要稳定的结构化接入面，而不是“自动魔法”

## 下一阶段路线图

### P1

- 增加官方 Claude Code MCP 配置示例
- 让 `agpair claude config` 支持直接落盘到项目 `.claude/settings.json` 或输出 merge patch
- 在 skill 中继续强化 `Monitor(agpair task watch --json ...)` 的默认使用路径

### P2

- 增加 repo/worktree 维度的轻量状态行工具
- 增加 task ↔ worktree 绑定可视化
- 设计 hook-based sync，但先只做只读提示，不做自动 mutation

### P3

- 研究把 Claude Code 原生 task graph 和 AGPair task graph 做更强映射
- 评估是否要做专用 Claude Code plugin，而不只是 MCP + skill

## 官方来源

- Claude Code Releases: <https://github.com/anthropics/claude-code/releases>
- Claude Code CHANGELOG: <https://github.com/anthropics/claude-code/blob/main/CHANGELOG.md>
- Claude Code Docs, MCP: <https://code.claude.com/docs/en/mcp>
- Claude Code Docs, Subagents: <https://code.claude.com/docs/en/sub-agents>
- Claude Code Docs, Agent teams: <https://code.claude.com/docs/en/agent-teams>
- Claude Code Docs, Memory: <https://code.claude.com/docs/en/memory>
- Claude Code Docs, Status line: <https://code.claude.com/docs/en/statusline>
- Claude Code Docs, Tools reference: <https://code.claude.com/docs/en/tools-reference>
