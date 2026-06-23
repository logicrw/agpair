# AGPair 1.0 工作流

AGPair 工作流用于高价值、多节点的本地工程编排。普通单任务委派仍应优先使用 `agpair task start`。

工作流清单是声明式 DAG。AGPair 会校验清单、创建 `workflows` 与 `workflow_nodes` 持久化记录，然后通过 AGPair 任务核心创建子任务。子任务仍使用普通 AGPair 任务模型，包括 attempt、artifact、结构化 receipt、completion policy 与 controller-aware executor policy。

清单禁止任意脚本字段。任意层级出现 `workflow_script`、`python`、`javascript`、`shell`、`command`、`commands`、`setup_commands`、`teardown_commands`、`postinstall`、`preinstall` 都会被拒绝。

常用命令：

```bash
agpair workflow fanout \
  --controller codex \
  --mode review \
  --topic "Review external-agent routing risks" \
  --lane grok-cli:primary \
  --lane grok-cli:adversarial \
  --lane antigravity-cli:second-opinion \
  --repo-path /path/to/repo \
  --dry-run --json
agpair workflow fanout \
  --controller codex \
  --mode review \
  --topic "Review external-agent routing risks" \
  --lane grok-cli:primary \
  --lane grok-cli:adversarial \
  --repo-path /path/to/repo \
  --wait --json
agpair workflow validate --file templates/workflows/fanout-synthesize.json
agpair workflow start --file templates/workflows/fanout-synthesize.json --controller codex --repo-path /path/to/repo --json
agpair workflow status WF-ABC123DEF456 --json
agpair workflow watch WF-ABC123DEF456 --json --cursor '<cursor>'
agpair workflow retry-node WF-ABC123DEF456 scan-routing --authorization-profile local_mutating
agpair workflow cancel WF-ABC123DEF456 --reason 'operator requested'
```

`workflow fanout` 是 Fusion-style panel 的默认友好入口。只有 preset 模式不够表达任务时，才手写 manifest。

支持的 fanout mode 是 `review`、`research`、`implementation`、`test-fix`。Review/research 是 report-only；implementation/test-fix 会写代码，preset 会默认隔离这些 mutating lane。

Fanout 还会生成协调元数据。`role` 仍是人类可读的 lane label，比如 `primary`、`adversarial`、`candidate-a`；语义提示写在 `coordination_role`，取值为 `thinker`、`worker`、`verifier`、`synthesizer`、`gate` 或 `general`。`coordination_policy.expected_roles` 和 evidence 里的 `role_coverage` 只给主控看拓扑覆盖情况，不是采纳证据，也不是硬成功门槛。

Fanout evidence 会暴露 `lane_cards`、`synthesis_result`、`panel_result`。Lane card 会保留有用的部分证据，包括 stdout-only salvage，但这种证据会标成 `needs_review`，不会伪装成成功。Synthesis 是证据，不是最终裁判；主控仍负责决定 use、apply、retry、switch executor 或 fallback 到原生 helper。

Workflow `ready_for_review` 表示 AGPair 已生成 evidence pack 等待主控验收，不是最终用户侧完成。

`workflow watch --json` 只输出状态变化和 durable artifact 路径，不输出完整原始日志。`workflow evidence.json` 聚合子任务的结构化 receipt、artifact 路径、校验结果、阻塞节点和残余风险。AGPair 不会自动 merge，不会自动 push，不会修改 OMX 源码。
