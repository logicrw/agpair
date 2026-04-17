# Codex v0.121.0 升级与 AGPair 主控适配研究

> 研究日期：2026-04-17
> 触发事件：OpenAI 于 2026-04-15 发布 Codex v0.121.0（rust-v0.121.0），2026-04-16 推进到 0.122.0-alpha.5
> 核心问题：Codex 是否已具备"当 AGPair 主控"的能力，AGPair 是否应该做相应适配

## 结论先行

**Codex v0.121.0 不宜改为 AGPair 的主控。** 当前 Claude Code 主控 + Codex executor 的格局仍然是正确选择。本次升级确实增强了 Codex 作为 executor 接入 AGPair 的能力（主要是 MCP client 这条线），但没有解决"Codex 对话结束就结束、无法长时间等待委派任务完成"这个根本限制。

建议动作：**花半天验证 + 加一节文档，不启动主控适配工程**。

## Codex v0.121.0 真实升级内容

来源：`github.com/openai/codex` releases 与 PR 列表（2026-04-15 发布窗口）。

### 已确认的新增能力

**1. MCP client 支持大幅增强**

- MCP Apps tool calls（PR #17364）
- 命名空间化 MCP 工具注册（PR #17404）
- `supports_parallel_tool_calls` 标志（PR #17667）
- sandbox state 元数据通过 MCP tool metadata 传递（PR #17763）
- 修复 deferred MCP tool call 展开、elicitation timeout 等边缘情况

**2. Marketplace / Plugin / Skill 系统**

- `codex marketplace add` 命令，支持 GitHub、git URL、本地目录、marketplace.json URL 安装插件市场（PR #17087、#17717、#17756）
- Skill 加载变为 filesystem-aware（PR #17720）
- Plugin 插件参与 external agent config 迁移（PR #17855）

**3. `spawn_agent` 子 agent 派发机制（feature flag 后面）**

- `spawn_agent` API 支持 `fork_context`（完整历史 fork）和 `fork_turns`（部分历史 fork）
- Forked agent 继承父线程的 model、MCP 连接管理器、prompt cache key（PR #16055、#17247）
- `features.use_agent_identity`：feature-gated agent 身份注册机制（PR #17385–#17388 栈）
- PR 作者自述 "full local app-server E2E path is still being debugged"

**4. App-server / Session persistence 演进**

- turn item injection、memory reset/delete 端点、external-agent config 迁移 API（PR #17703、#17913、#17871、#17855）
- Thread store 抽象（`codex-thread-store` crate）：**本地 thread 列表持久化，不是跨会话工作流恢复**（PR #17659、#17824）

**5. Memory 系统 UI + TUI 改进**

- Memory mode 控制、memory reset/delete UI
- `Ctrl+R` 反向搜索 prompt 历史、Slash command 本地历史召回

### 未升级的能力（特意确认）

以下能力在 release notes 和相关 PR 中**找不到任何证据**：

| 能力 | 状态 |
|------|------|
| 长时间 background task / daemon 模式 | 查无此事 |
| 流式后台观察（类 Claude Code Monitor） | 查无此事 |
| 持续轮询外部任务状态（heartbeat / receipt / retry） | 查无此事 |
| 跨对话恢复 long-running 工作流 | 查无此事 |
| Hook / 事件系统 | 查无此事 |
| 官方文档声明"Codex 适合做 multi-agent coordinator / 主控" | 查无此事 |

## 对原关切的判断

用户此前观察到的核心问题："Codex 让它等委派任务做完再审核推进，对话结束就结束，没有长流程维持能力。"

**这个根本限制 v0.121.0 没动。**

- `spawn_agent` 是 Codex **进程内部**派生子 agent，不是让 Codex **外部等**一个 AGPair 任务跑完。
- Thread store 是本地历史持久化，不是"恢复一个跑到一半的长流程"。
- 没有引入任何 daemon / long-poll / background-process 原语。

## 一个被忽视的事实：AGPair 的 `task wait` 已经把"等"剥离到自己身上

最近两个 commit：

- `feat: add inline executor polling to wait loop for daemon-free task close`（6d0dd94）
- `feat: unify task monitoring with Monitor tool and inline poll in watch`（a945394）

这两个改动意味着 `agpair task wait <TASK_ID> --timeout 1h` 本身就是阻塞长等的。**任何控制器——Claude Code、Codex、普通 shell——只要能跑一次阻塞子进程就能拿到终态**。Codex CLI 本来就能 shell out 并等子进程返回。

所以"Codex 当主控"的主要痛点，AGPair 已经替它补上了大半。缺的不是 Codex 自己变强，而是 AGPair 现有能力有没有对着 Codex 的调用范式暴露好。

## 要不要做适配：要做一点，不要大动

### 应该做（低成本，高价值）

**1. 验证 `agpair-mcp` 在 Codex v0.121 MCP client 下能跑**

Codex 这次 MCP 支持改动很多，要真跑一次确认：

- `agpair_start_task` 参数 schema 被 Codex 正确识别
- 大结果（比如 `agpair_list_tasks` 返回一堆任务）不被 Codex 截断
- `agpair_task_wait` 这种长耗时工具调用 Codex 不会超时 reset

**2. 在 `docs/usage.md` 加一小节"Codex 作为控制器"**

写明白 Codex 回合内可以 shell out `agpair task wait --timeout 1h` 做阻塞等待。一页文档就解决问题，不需要新代码。

**3. Codex-flavored skill / marketplace entry（可选）**

Codex v0.121 的 Skill 系统是 marketplace-based 而不是 filesystem symlink。如果想让 Codex 自然发现 AGPair 能力，可以做一个小的 marketplace-compatible skill 包。**前置条件是用户真的把 Codex 当主控用**，不是主动先铺。

### 不应该做

1. **不要为 Codex 单独做 daemon / long-poll / controller adapter**。v0.121 根本没有这方面的原语，再怎么适配也做不出来。
2. **不要复制 Claude Code Monitor 绑定那套玩法给 Codex**。Monitor 是 Claude Code 独有的流式工具，Codex 没有对等物，勉强模拟就是 AI slop。
3. **不要赌 `spawn_agent` / `use_agent_identity` 马上成熟**。feature flag 后面 + PR 作者自述 "still being debugged"，半年内不能作为稳定接入面。

### 值得观察但不立即做

- 如果 OpenAI 下个月把 `spawn_agent` 推出 feature flag 并给出稳定 API，那时候 Codex 才算有了"subagent orchestrator"能力，那时再评估 AGPair 是否要支持"Codex 派生子 agent → 外部任务委派给 AGPair"的流程。
- `codex marketplace` 如果形成生态，AGPair 可能值得上架。生态还不存在，现在上架没意义。

## 重新评估的分工

| 角色 | 谁更合适 | 为什么 |
|------|---------|--------|
| 主控 / controller | Claude Code | Monitor tool + ScheduleWakeup + /loop + background tasks，能长时间盯盘 |
| executor（被委派执行） | Codex / Gemini / Antigravity | 单回合内完成机械性编码任务，拿结果就退 |
| durable state layer | AGPair | SQLite 持久化 + receipt + heartbeat + retry，跨控制器、跨会话都能查 |

v0.121 没有改变这个分工。它让 Codex 作为 **executor** 在调用 AGPair MCP 时更顺手，仅此而已。

## 下次重新评估的触发条件

只有以下任一条件发生，才值得重开这个研究：

1. OpenAI 官方文档 / blog 明确声称 Codex 支持 long-running background task 或 daemon 模式
2. `spawn_agent` / `use_agent_identity` 从 feature flag 毕业成稳定 API
3. Codex 新增类似 Claude Code Monitor tool 的流式观察能力
4. 用户实际在 Codex 主控场景里撞到具体痛点（目前是假设性需求）

## 官方来源

- Codex Releases: <https://github.com/openai/codex/releases>
- Codex v0.121.0 tag: `rust-v0.121.0`（2026-04-15）
- 相关 PR 栈参见正文各节引用
