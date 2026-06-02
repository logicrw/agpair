# Claude Code 集成

AGPair 面向 Claude Code 的定位很简单：Claude Code 是主控和验收者，AGPair 是外部 CLI agent 的持久化控制面。

## 推荐安装

### Skill

```bash
mkdir -p ~/.claude/skills/agpair
cp /absolute/path/to/agpair/skills/Claude/SKILL.md ~/.claude/skills/agpair/SKILL.md
```

### MCP

```bash
claude mcp add --transport stdio agpair -- agpair-mcp
```

项目级共享配置：

```bash
claude mcp add --transport stdio --scope project agpair -- agpair-mcp
```

### Hooks 和 Status Line

```bash
agpair claude config
agpair claude config --install --scope project --repo-path "$REPO"
```

`agpair claude config` 管理这些 Claude Code 配置：

- `statusLine`: 显示当前 repo 的 AGPair 任务状态。
- `SessionStart`: 注入简短 AGPair 可用提示。
- `PreCompact`: 有活跃 AGPair 任务时阻止过早 compact。
- `UserPromptSubmit`: 注入 external-first 路由提示。
- `Stop`: 只在 `ready_for_review`、`approval_required` 等需要主控决策的状态阻止结束。
- `SubagentStart`: 提醒 Claude 原生 subagent 只作为 fallback/review lane。
- `SubagentStop`、`TaskCreated`、`TaskCompleted`: V1 仅保留为 observability hook。

安装器只按 AGPair 命令身份追加或移除配置。它不会覆盖非 AGPair `statusLine`，除非显式传 `--force`；也不会删除其他 Claude Code hook。

## 工作方式

Claude Code 默认先走 AGPair：

```bash
agpair task start \
  --repo-path "$REPO" \
  --executor antigravity-cli \
  --authorization-profile local_mutating \
  --body "$BRIEF"
```

并行或后台任务使用低噪等待：

```bash
agpair task start ... --no-wait
agpair task watch TASK-123 --json
```

`watch --json` 只输出状态变化和证据路径，避免把完整日志塞进 Claude 上下文。Claude Code 的 Monitor/background task 可以观察这条 watch 输出，但 AGPair 的真实状态仍以 SQLite、journal、terminal receipt、git diff/commit 为准。

如果 executor 返回 `blocked(approval_required)`，不要继续轮询。用新授权预算开新 attempt：

```bash
agpair task retry TASK-123 \
  --from-block \
  --authorization-profile local_mutating
```

`--from-block` 会自动带上原 brief、blocked 原因、receipt、journal tail、当前 git status、diff/commits 和新的授权 profile。

## 分工边界

- AGPair：任务生命周期、executor 路由、等待、receipt、blocked retry、raw evidence path。
- Claude Code：需求判断、派单 brief、最终 diff/receipt/测试验收。
- Claude 原生 subagent：只在 AGPair 不可用、不适合、或外部结果不达标时作为 fallback/review。

新任务不要再派给 Gemini。历史 `gemini_cli` 任务可以检查或清理，但不能作为新的 `task start` / `retry` 目标。

## 官方参考

- [Claude Code MCP](https://code.claude.com/docs/en/mcp)
- [Claude Code hooks](https://code.claude.com/docs/en/hooks)
- [Claude Code status line](https://code.claude.com/docs/en/statusline)
- [Claude Code skills](https://code.claude.com/docs/en/skills)
- [Claude Code subagents](https://code.claude.com/docs/en/sub-agents)
