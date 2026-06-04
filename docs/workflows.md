# AGPair Workflows V2

AGPair workflows are for high-value, multi-part local engineering orchestration. Use normal `agpair task start` for ordinary single-task delegation.

Workflow manifests are declarative DAGs. AGPair validates the manifest, stores `workflows` and `workflow_nodes`, then creates normal V1.1 child tasks. Child tasks still use AGPair task attempts, durable artifacts, structured receipts, completion policies, and controller-aware executor routing.

Manifests are not a script runner. Any nested `workflow_script`, `python`, `javascript`, `shell`, `command`, `commands`, `setup_commands`, `teardown_commands`, `postinstall`, or `preinstall` field is rejected.

Common commands:

```bash
agpair workflow validate --file templates/workflows/fanout-synthesize.json
agpair workflow start --file templates/workflows/fanout-synthesize.json --controller codex --repo-path /path/to/repo --json
agpair workflow status WF-ABC123DEF456 --json
agpair workflow watch WF-ABC123DEF456 --json --cursor '<cursor>'
agpair workflow retry-node WF-ABC123DEF456 scan-routing --authorization-profile local_mutating
agpair workflow cancel WF-ABC123DEF456 --reason 'operator requested'
```

Workflow `ready_for_review` means AGPair has an evidence pack for controller verification, not final user-facing success. `workflow watch --json` emits state changes and durable artifact paths, not full raw logs. AGPair does not auto-merge, auto-push, or modify OMX source.
