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
mkdir -p ~/.codex/skills/agpair
cp "$PWD/skills/Codex/SKILL.md" ~/.codex/skills/agpair/SKILL.md
agpair codex config --install --scope project --repo-path /path/to/repo
```

Claude Code：

```bash
mkdir -p ~/.claude/skills/agpair
cp "$PWD/skills/Claude/SKILL.md" ~/.claude/skills/agpair/SKILL.md
claude mcp add --transport stdio agpair -- agpair-mcp
agpair claude config --install --scope project --repo-path /path/to/repo
```

AGPair 不可用时 hook 会 fail open。它们只是路由提示和结束护栏，不能替代主控验收。

## 4. 派发任务

`task start` 默认会等待终态：

```bash
agpair task start \
  --repo-path /path/to/repo \
  --executor antigravity-cli \
  --authorization-profile local_mutating \
  --body "Goal: 修复失败的 smoke test。Required evidence: 运行聚焦测试。"
```

异步或并行任务：

```bash
agpair task start \
  --repo-path /path/to/repo \
  --executor antigravity-cli \
  --authorization-profile local_mutating \
  --body "Goal: ..." \
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

除非 brief 或授权 profile 明确要求提交，`commit_ref` 是可选字段。

## 6. 处理授权阻塞

如果任务返回 `blocked(approval_required)`，不要继续轮询。用结构化 block 上下文开新 attempt：

```bash
agpair task retry TASK-123 \
  --from-block \
  --authorization-profile local_mutating
```

retry 会带上原 brief、blocked 原因、terminal receipt、journal tail、当前 git status、diff/commits 和新的授权 profile。

## 7. Executor 选择

默认顺序：

1. `antigravity-cli`：默认外部实现 executor。
2. `grok-cli`：低成本 challenger / backup。
3. `claude-code`：质量升级或 Claude 相关外部执行。
4. `codex`：外部 Codex worker fallback。

新任务不要使用 Gemini。历史 `gemini_cli` 记录只用于检查或清理。

## 8. 本地文件

不要提交本地运行状态或个人配置：

- `~/.agpair`
- `~/.codex/*`
- `~/.claude/settings.json`
- `.claude/settings.local.json`
- AGPair raw logs
- session transcripts
- 生成的 hook debug output

项目级 `.claude/settings.json` 或 Codex hook config 只有在清理过、且明确要共享时才应提交。

## Legacy 说明

Antigravity 桌面端 companion extension 和 bridge 诊断仍保留给旧安装使用。新任务应使用 `antigravity-cli`。
