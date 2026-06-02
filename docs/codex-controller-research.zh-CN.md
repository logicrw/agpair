# Codex 主控适配结论

旧的 v0.121 结论已经过时。当前定位如下：

- Codex 可以作为 AGPair controller：负责拆任务、派发、等待、复核 diff/receipt/evidence，并最终向用户汇报。
- AGPair 提供低 token 的等待层：`task wait` / `task watch --json`，不要用反复提示 Codex 的方式轮询外部任务。
- Codex hooks 用于提示 external-first 和阻止过早结束：`UserPromptSubmit` 注入外部优先上下文，`Stop` 在 ready_for_review 或 approval_required 时提醒 Codex 处理终态。
- Codex 仍没有 Claude Code Monitor 的完全等价机制；AGPair 的 watch/wait/receipt ledger 是跨 controller 的一致控制层。
- OMX 不需要在 V1 里改源码。通过 AGPair 拥有的 Codex hooks、skills、config 让 Codex 优先走外部 executor；AGPair 不可用或结果不达标时，Codex native subagents 才作为 fallback/review 使用。

安装或输出 Codex 配置：

```bash
agpair codex config
agpair codex config --install --scope project --repo-path "$REPO"
```
