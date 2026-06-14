# AGPair 3.0 Workflows

AGPair workflows are for high-value, multi-part local engineering orchestration. Use normal `agpair task start` for ordinary single-task delegation.

Workflow manifests are declarative DAGs. AGPair validates the manifest, stores `workflows` and `workflow_nodes`, then creates normal AGPair child tasks. Child tasks still use AGPair task attempts, durable artifacts, structured receipts, completion policies, and controller-aware executor routing.

Manifests are not a script runner. Any nested `workflow_script`, `python`, `javascript`, `shell`, `command`, `commands`, `setup_commands`, `teardown_commands`, `postinstall`, or `preinstall` field is rejected.

Common commands:

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

Use `workflow fanout` as the default controller-friendly entrypoint for Fusion-style panels. Use hand-written manifests only when the preset modes are too small for the job.

Supported fanout modes are `review`, `research`, `implementation`, and `test-fix`. Review and research lanes are report-only. Implementation and test-fix lanes are mutating and the preset isolates those lanes by default.

Fanout evidence exposes `lane_cards`, `synthesis_result`, and `panel_result`. Lane cards preserve useful partial evidence, including stdout-only salvage, but mark it as `needs_review` instead of success. Synthesis is evidence, not authority: the controller still decides whether to use, apply, retry, switch executor, or fall back to native helpers.

Workflow `ready_for_review` means AGPair has an evidence pack for controller verification, not final user-facing success. `workflow watch --json` emits state changes and durable artifact paths, not full raw logs. AGPair does not auto-merge, auto-push, or modify OMX source.
