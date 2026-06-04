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

新任务不再使用 Gemini。历史 `gemini_cli` 记录仍可检查或清理。

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

如果 executor 返回 `blocked(approval_required)`，用 structured blocked context 开新 attempt：

```bash
agpair task retry TASK-123 \
  --from-block \
  --authorization-profile local_mutating
```

## 主控配置

Codex：

```bash
mkdir -p ~/.codex/skills/agpair
cp "$PWD/skills/Codex/SKILL.md" ~/.codex/skills/agpair/SKILL.md
agpair codex config
agpair codex config --install --scope project --repo-path /path/to/repo
```

Claude Code：

```bash
mkdir -p ~/.claude/skills/agpair
cp "$PWD/skills/Claude/SKILL.md" ~/.claude/skills/agpair/SKILL.md
agpair claude config
agpair claude config --install --scope project --repo-path /path/to/repo
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

`ready_for_review`、`evidence_ready`、`committed` 都不是自动成功。主控必须检查 AGPair 状态、git diff/commit 证据、receipt、必要时的 raw log 路径，并运行相应验证后才能报告完成。

主控验收 evidence 后，用下面的命令标记任务已接受，避免 Stop hook 对同一个 receipt 反复阻塞：

```bash
agpair task accept TASK-123
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

- `~/.codex/skills/agpair/SKILL.md`
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
| [工作流 V2](docs/workflows.zh-CN.md) | 声明式多任务工作流编排 |
| [Claude Code 集成](docs/claude-code-integration.zh-CN.md) | Claude Code 配置和路由规则 |
| [Getting Started](docs/getting-started.en.md) | English quick guide |
| [Command Reference](docs/usage.md) | English CLI reference |

## Legacy Surface

仓库仍保留 Antigravity 桌面端 companion extension 和旧 bridge 诊断，用于已有安装。新任务推荐路径是 `antigravity-cli` executor，不是 IDE bridge。

## License

MIT
