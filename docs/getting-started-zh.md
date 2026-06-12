# AGPair 新手教程

这份教程帮助你让 Codex 或 Claude Code 通过 AGPair 派发外部 CLI agent。

## 1. 安装

```bash
git clone https://github.com/logicrw/agpair.git
cd agpair
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e '.[dev]'
```

可选：让 CLI 全局可用。

```bash
mkdir -p ~/.local/bin
ln -sf "$PWD/.venv/bin/agpair" ~/.local/bin/agpair
which agpair
```

## 2. 健康检查

```bash
agpair doctor
agpair doctor --repo-path /path/to/repo
agpair doctor --fresh --repo-path /path/to/repo
```

重点看：

- `supported_executor_backends`: `antigravity-cli`, `grok-cli`, `claude-code`, `codex`。
- `default_executor_backend`: `antigravity-cli`。
- `executor_cli_health`: 每个 CLI binary 是否可用。
- `authorization_profiles`: 派发时可选的授权预算。
- `client_hook_install_status`: 传入 repo path 时会显示 Codex/Claude hook 安装状态。

非默认 executor 缺 binary 只是 warning，不会阻止 AGPair 使用其他可用 executor。

## 3. 配置主控

Codex：

```bash
agpair codex config --install --scope project --repo-path /path/to/repo --sync-skill
```

Claude Code：

```bash
agpair claude config --install --scope project --repo-path /path/to/repo --sync-skill
```

AGPair 不可用时 hook 会 fail open。它们只是路由提示和结束护栏，不能替代主控验收。

## 4. 派发任务

`task start` 默认会等待终态：

```bash
agpair task start \
  --repo-path /path/to/repo \
  --executor antigravity-cli \
  --task-kind quick_review \
  --wait-policy lease \
  --authorization-profile local_readonly \
  --completion-policy report \
  --body "Goal: 审查指定区域。Scope: 仅限已点名文件。Required changes: None. This is report-only. Do not edit files. Exit criteria: 返回带证据的中文结论。"
```

实现、重构、修测试这类有边界的写代码任务，用 isolated worktree 和 evidence
completion：

```bash
agpair task start \
  --repo-path /path/to/repo \
  --executor antigravity-cli \
  --task-kind implementation \
  --wait-policy lease \
  --authorization-profile local_mutating \
  --completion-policy evidence \
  --isolated-worktree \
  --body "Goal: 做一个有边界的修改。Scope: 写清允许文件。Required changes: 写清要改什么。Exit criteria: 跑聚焦验证并返回证据。"
```

`--wait-policy lease` 会让主控在有界窗口里低噪等待。如果 executor 仍在运行，
AGPair 会返回结构化的 background-running 结果，而不是让主控浪费模型轮次轮询，
或过早杀掉任务。

异步或并行任务：

```bash
agpair task start \
  --repo-path /path/to/repo \
  --executor antigravity-cli \
  --task-kind quick_review \
  --wait-policy lease \
  --authorization-profile local_readonly \
  --completion-policy report \
  --body "Goal: 审查指定区域。Scope: 仅限已点名文件。Required changes: None. This is report-only. Do not edit files. Exit criteria: 返回带证据的结论。" \
  --no-wait

agpair task watch TASK-123 --json
```

`watch --json` 只输出状态变化和 raw log / receipt 路径，不会流式输出完整日志。

## 5. 验收结果

```bash
agpair task status TASK-123 --json
agpair task logs TASK-123
git -C /path/to/repo status --short
git -C /path/to/repo diff
```

`ready_for_review`、`evidence_ready`、`committed` 都只是验收门。外部 executor 声称完成后，Codex 或 Claude Code 仍要检查 diff、receipt、raw evidence path 和测试证据，再报告成功。

在 `status --json` 和 `wait --json` 里，先看 `agent_result.controller_action`：报告任务通常是 `use_result`，隔离实现 diff 通常是 `review_then_apply`，blocked attempt 会提示主控检查、重试或切换 executor。

除非 brief 或授权 profile 明确要求提交，`commit_ref` 是可选字段。

isolated 代码任务需要显式查看并应用 worker diff：

```bash
agpair task diff TASK-123
agpair task apply TASK-123 --check
agpair task apply TASK-123
```

主控验证通过后，标记这个 receipt 已处理：

```bash
agpair task accept TASK-123 --adoptable-result yes --controller-rework none
```

## 6. 处理授权阻塞

如果任务返回 `blocked(approval_required)`，不要继续轮询。用结构化 block 上下文开新 attempt：

```bash
agpair task retry TASK-123 \
  --from-block \
  --authorization-profile local_mutating
```

retry 会带上原 brief、blocked 原因、terminal receipt、journal tail、当前 git status、diff/commits 和新的授权 profile。

## 7. 多段任务使用工作流

普通工作继续使用 `agpair task start`。高价值、多段、并行、对抗审查或长时间任务再使用 `agpair workflow start`：

```bash
agpair workflow validate --file templates/workflows/fanout-synthesize.json
agpair workflow start --file templates/workflows/fanout-synthesize.json --controller codex --repo-path /path/to/repo --json
agpair workflow watch WF-ABC123DEF456 --json
```

工作流清单是声明式的，不是脚本运行器。Workflow `ready_for_review` 表示 AGPair 已生成 evidence pack 等待主控验收，不是最终用户侧完成。

## 8. Executor 选择

默认先选：

1. `antigravity-cli`：默认外部实现 executor。
2. `grok-cli`：低成本 challenger / backup。

Codex 主控之后用 `claude-code`；外部 `codex` 默认被抑制，因为它是 AGPair 管理的 Codex CLI worker。Claude Code 主控之后用 `codex`；外部 `claude-code` 默认被抑制，因为 Claude Code 已有原生 subagent。只有明确需要时才用 `--allow-self-executor` 覆盖。

历史 executor 记录仍可为兼容性读取。新任务只使用 active registered executor id。

如果 `antigravity-cli` 健康，但当前 Antigravity 默认模型在 `--print` 任务中超时，
设置 `AGPAIR_ANTIGRAVITY_MODEL` 为本机 CLI 已验证可用的模型，例如
`Gemini 3.1 Pro (Low)`。

新增、禁用、弃用或移除 executor 时，走共享 registry profile contract。详见
[Executor Lifecycle](executor-lifecycle.md)。

## 9. 本地文件

不要提交本地运行状态或个人配置：

- `~/.agpair`
- `~/.codex/*`
- `~/.claude/settings.json`
- `.claude/settings.local.json`
- AGPair raw logs
- session transcripts
- 生成的 hook debug output

项目级 `.claude/settings.json` 或 Codex hook config 只有在清理过、且明确要共享时才应提交。

## 兼容性说明

旧 companion 和 bridge 诊断仍保留给已有安装读取。当前任务派发使用上面列出的注册 CLI executor。
