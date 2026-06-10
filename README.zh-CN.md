# agpair

![Python](https://img.shields.io/badge/python-≥3.12-blue)
![Platform](https://img.shields.io/badge/platform-macOS-lightgrey)
![License](https://img.shields.io/badge/license-MIT-green)

[English](README.md) | [新手教程](docs/getting-started-zh.md) | [命令参考](docs/usage.zh-CN.md)

AGPair 是给 Codex 和 Claude Code 使用的 external-agent-first 控制面。

主控 agent 负责规划和验收。AGPair 负责把任务派给外部 CLI executor、持久化任务状态、低噪等待、校验结构化 receipt，并在 executor 阻塞时支持带上下文的 retry。

## 当前模型

- 默认外部 executor：`antigravity-cli`
- 低成本 challenger / backup：`grok-cli`
- 质量升级：`claude-code`
- 外部 Codex CLI worker fallback：`codex`
- Codex / Claude Code 原生 subagent：只作为 fallback 或 review 资源

路由是 controller-aware 的：Codex 主控默认不选择 AGPair 管理的外部 `codex`，Claude Code 主控默认不选择 AGPair 管理的外部 `claude-code`，除非明确使用 `--allow-self-executor`。

实际分工是：`codex` 是给 Claude Code 主控使用的外部 Codex CLI worker；Codex 主控自己的 fallback / review lane 应使用 Codex 原生 subagent。`claude-code` 是给 Codex 主控使用的外部 Claude Code worker；Claude Code 主控自己的 fallback / review lane 应使用 Claude Code 原生 subagent。

新任务不再使用 Gemini。历史 `gemini_cli` 记录仍可检查或清理。

所有 active 外部 CLI executor 默认都使用 `managed-natural`。AGPair 负责任务边界、
receipt、日志、status、retry 和验收证据；外部 CLI 继续使用它正常启动时的
skills、MCP、memory、plugins 和 provider 配置。自调用规避由 controller
suppression 处理，不通过 executor launch 配置特殊化。

AGPair 自己启动的 executor、probe、smoke 和 retry 进程会被标记为 internal，
因此已安装的 Codex / Claude hooks 会对这些进程 no-op。正常主控会话仍会收到
external-first 提示。

## 快速开始

```bash
git clone https://github.com/logicrw/agpair.git
cd agpair
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e '.[dev]'
```

让主控 agent 能直接调用 CLI：

```bash
ln -sf "$PWD/.venv/bin/agpair" ~/.local/bin/agpair
agpair doctor
```

派发任务。`task start` 默认会等待终态：

```bash
agpair task start \
  --executor antigravity-cli \
  --authorization-profile local_mutating \
  --repo-path /path/to/repo \
  --body "Goal: 修复失败的 smoke test。Required evidence: 运行聚焦测试。"
```

异步或并行任务使用低噪 watch：

```bash
agpair task start \
  --executor antigravity-cli \
  --authorization-profile local_mutating \
  --repo-path /path/to/repo \
  --body "Goal: ..." \
  --no-wait

agpair task watch TASK-123 --json
```

`watch --json` 只输出状态变化和 raw log / receipt 路径，不会把完整 executor 日志塞进主控上下文。

实现 / 重构 / 修测试这类切片，用 isolated worktree 加 evidence completion：

```bash
agpair task start \
  --executor antigravity-cli \
  --authorization-profile local_mutating \
  --completion-policy evidence \
  --isolated-worktree \
  --repo-path /path/to/repo \
  --body "Goal: 做一个有边界的修改。Scope: 写清允许文件。Required changes: 写清要改什么。Exit criteria: 跑聚焦验证。"
```

isolated 的 mutating evidence/commit 任务默认会把 tracked 的 staged/unstaged 改动同步到 executor worktree；不会复制 ignored 或 untracked 文件。需要强制干净基线时传 `--dirty-snapshot off`。

如果 executor 返回 `blocked(approval_required)`，用 structured blocked context 开新 attempt：

```bash
agpair task retry TASK-123 \
  --from-block \
  --authorization-profile local_mutating
```

如果 retry 不是合适的恢复路径，就换另一个外部 executor，或者让主控回到自己的原生 subagent fallback / review lane。

## 主控配置

Codex：

```bash
agpair codex config
agpair codex config --install --scope project --repo-path /path/to/repo --sync-skill
```

Claude Code：

```bash
agpair claude config
agpair claude config --install --scope project --repo-path /path/to/repo --sync-skill
```

要让 Codex 调用外部 `claude-code` worker，AGPair 默认使用 Claude auth
mode `auto`：先尝试有效的本机 Claude Code OAuth / 订阅登录；如果没有登录或
live probe 失败，就复用 CC Switch 当前选中的 Anthropic-compatible Claude
provider。AGPair 不需要再单独配置一套 Claude API key。

```bash
claude auth status
agpair doctor --fresh --repo-path /path/to/repo
```

`doctor --fresh` 会跑一个极小 live auth probe，并把选中的 `auth_mode` 显示为
`oauth` 或 `ccswitch`。如果 OAuth 失败，用 `claude auth login` 刷新本机
Claude Code 登录；如果 CC Switch 失败，就在 CC Switch 里更新当前 Claude
provider。`last_failure_type` 里的 `executor_probe_timeout` 和
`executor_hook_interference` 是 runtime / probe 边界问题，不是 credential
问题；`executor_auth_required` 才表示 credential 需要处理。

只有明确想绕过 OAuth 和 CC Switch、给 worker 使用单独 API credential 时，才启用
API-key worker mode：

```bash
mkdir -p ~/.agpair
agpair claude worker-settings > ~/.agpair/claude-worker-settings.json
export AGPAIR_CLAUDE_CODE_AUTH_MODE=api
export AGPAIR_CLAUDE_CODE_SETTINGS="$HOME/.agpair/claude-worker-settings.json"
export ANTHROPIC_API_KEY="..."
```

AGPair 管理的 hook 是提示和护栏，AGPair 不可用时 fail open。安装器会保留无关本地设置，卸载时只移除 AGPair 自己管理的条目。

## 授权 Profile

派发时选择能完成任务的最小授权预算：

- `local_readonly`：只读检查。
- `local_mutating`：普通仓库内修改和聚焦测试。
- `local_test_heavy`：更重的本地测试 / 构建。
- `external_network`：任务明确需要外部网络访问。

V1 不做“运行中暂停等待授权”。越界时 executor 应返回 `blocked(approval_required)`，主控再发起新 retry attempt。

## 验收门

`ready_for_review`、`evidence_ready`、`committed` 都不是自动成功。主控必须检查 AGPair 状态、git diff/commit 证据、receipt、必要时的 raw log 路径，并运行相应验证后才能报告完成。真实 executor smoke 也必须看到 `all_success=true`，且每个尝试过的 executor 都是无 blocker 的 `adoptable_result=yes` 或 `partial`；只 dispatch 成功或只进入成功 phase 不算通过。

判断 AGPair 是否真的有价值，主要看 completion rate、adoptable-result rate、time-to-first-useful-signal、fallback rate、controller rework rate，以及 abandoned/no-progress rate。用 `task status --json`、`task list --json` 和 `scripts/smoke_real_executors.py` 看这些字段。

主控验收 evidence 后，用下面的命令标记任务已接受，避免 Stop hook 对同一个 receipt 反复阻塞：

```bash
agpair task accept TASK-123 --adoptable-result yes --controller-rework none
```

如果 AGPair 协议解析失败但 report/stdout 可用，用显式采纳记录 salvage：

```bash
agpair task adopt TASK-123 --from-report --adoptable-result partial --controller-rework minor
```

除非 brief 或授权 profile 明确要求 commit，`commit_ref` 是可选字段。

## 本地状态

AGPair 默认把本地运行状态放在 `~/.agpair`。测试时可覆盖：

```bash
export AGPAIR_HOME=/path/to/agpair-state
```

不要提交本地运行状态、raw logs、session transcript、生成的 hook debug 输出，或个人 Codex/Claude 配置。

仓库源文件：

- `skills/Codex/SKILL.md`
- `skills/Claude/SKILL.md`
- `agpair/cli/codex.py`
- `agpair/cli/claude.py`

本机安装副本：

- `~/.codex/skills/agpair-codex/SKILL.md`
- `~/.claude/skills/agpair/SKILL.md`
- Codex hook config
- `~/.claude/settings.json`

`.claude/settings.json` 或 Codex 项目 hook 只有在已经清理并明确要共享时才应提交。

## 架构

```
Controller (Codex / Claude Code)
        |
        | agpair task start / watch / retry
        v
AGPair CLI + SQLite state + journal + receipts
        |
        | external CLI executor
        v
antigravity-cli / grok-cli / claude-code / codex
```

AGPair 不是语义控制器。规划、范围决策、review 和最终验证仍由主控 AI 负责。

## 文档

| 文档 | 说明 |
| --- | --- |
| [新手教程](docs/getting-started-zh.md) | 最小安装和第一个任务 |
| [命令参考](docs/usage.zh-CN.md) | 中文 CLI 参考 |
| [Executor Lifecycle](docs/executor-lifecycle.md) | 新增、禁用、弃用或移除外部 executor |
| [工作流 V2](docs/workflows.zh-CN.md) | 声明式多任务工作流编排 |
| [Claude Code 集成](docs/claude-code-integration.zh-CN.md) | Claude Code 配置和路由规则 |
| [Getting Started](docs/getting-started.en.md) | English quick guide |
| [Command Reference](docs/usage.md) | English CLI reference |

## 兼容性

仓库仍保留 Antigravity 桌面端 companion extension 和旧 bridge 诊断，用于已有安装。新任务推荐路径是 `antigravity-cli` executor，不是 IDE bridge。

## License

MIT
