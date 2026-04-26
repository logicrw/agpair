# AGPair Remediation Plan: Antigravity 状态同步与 Isolated Worktree 真执行

> 状态：待实现
> 目的：把两个当前会误导主控器的执行问题写清楚，供后续 AI/工程师直接修复
> 范围：
> 1. `antigravity` executor 的状态同步问题
> 2. `isolated_worktree` 当前仅为元数据、未被真正执行的问题

---

## 1. 背景

最近在真实项目中使用 `agpair` 编排多执行器任务时，暴露了两个会直接影响可靠性的行为缺口：

1. **Antigravity 实际已经开始修改代码，但 `agpair task status` 仍停留在 `new/submitted`。**
2. **`--isolated-worktree` 当前只被持久化为任务元数据，并不会自动创建/切换到独立 worktree。**

这两个问题组合在一起，会让 controller 看到一个“看起来安全、实际上不安全”的系统：

- 主控器以为任务还没开始，其实 provider 已经开始改代码。
- 主控器以为任务在独立 worktree 中执行，其实本地 CLI 仍然可能在基础仓库路径运行。

这不是文档问题，而是运行时语义与状态链路的问题。

---

## 2. 问题 A：Antigravity 明明在做，但任务状态仍然是 `new`

### 2.1 现象

观察到如下行为：

- `agpair task start --executor antigravity ... --no-wait` 返回了 `TASK_ID`
- `agpair task status <TASK_ID>` 长时间仍显示：
  - `phase = new`
  - `a2a_state_hint = submitted`
  - `session_id = null`
  - `last_heartbeat_at = null`
- `agpair inspect --repo-path ... --json` 中：
  - `bridge.reachable = true`
  - `bridge.session_ready = true`
  - `pending_task_count` 一度从 `1` 变成 `0`
- 同时，目标仓库已经出现了真实未提交改动，说明 provider 实际上已经开始工作

也就是说：

1. 任务已成功投递到 provider 队列
2. provider 也已经消费并开始执行
3. 但 `agpair` 侧没有收到 ack / heartbeat / receipt

### 2.2 当前实现中的结构性原因

当前 `AntigravityExecutor` 是一个**投递型 executor**，不是可主动轮询的 executor。

相关实现：

- `agpair/executors/antigravity.py`
  - `dispatch()` 只做 `AgentBusClient.send_task(...)`
  - `poll()` 返回 `None`

这意味着：

- `agpair` 无法像 `local_cli` executor 一样主动轮询本地进程状态
- 它只能被动等待 Antigravity companion 回写：
  - ack
  - heartbeat
  - terminal receipt

如果 companion / bridge / receipt watcher 任一段没有把状态写回 `agpair`，任务就会一直卡在：

- `new`
- `submitted`

即使 provider 实际上已经在改代码。

### 2.3 需要修的不是“展示”，而是状态桥

问题不在前端显示，而在于：

- `task status` 的数据源本身没有被更新
- journal 只有 `created` / `dispatched`
- tasks 表没有 phase 前进，也没有 `session_id` / `last_heartbeat_at`

此外，这次真实排障里还观察到一个非常重要的“中间态信号”：

- `inspect` 中的 `pending_task_count` 从 `1` 变成 `0`
- 但 `task status` 仍停留在 `new`
- 同时目标仓库出现了真实文件改动

这说明系统必须区分两种状态：

1. **消息仍在 provider 队列中**（尚未被消费）
2. **provider 已经取走任务，但状态桥没有回写**

当前这两种情况都会表现成“submitted”，这对 controller 来说是不可接受的。

### 2.4 目标行为

对于任何成功被 Antigravity consumer 接手的任务，`agpair` 必须能在短时间内看到：

1. `new -> acked` 或 `new -> running`
2. `session_id` 被填入，且该 ID 是可验证的 provider session / conversation 标识
3. `last_heartbeat_at` 定期更新
4. 最终有 terminal receipt，或有明确的 blocking / lost-session 诊断

### 2.5 修复要求

必须满足以下之一，不能继续保持现在这种“已执行但无状态”：

#### 方案 A：补全 Antigravity ack / heartbeat / receipt 回写链路

需要检查：

- provider 接收到 `TASK` 后，是否有稳定的 ack 回写事件
- 当前 bridge / companion 是否只消费了任务但没有把运行态同步回 `agpair`
- `receipt_watcher` 是否只处理 terminal receipt，而不处理中间心跳
- `taskSessionStore` / `agentBusDelegationService` / `httpServer` 是否遗漏了针对 `repo_path=/Users/.../agpair` 这类仓库的绑定更新

最低要求：

- provider 一旦接受任务，必须回写一个可验证 ack
- ack 必须推进 task phase
- provider 运行期间必须定期回 heartbeat，或由 bridge 代发 heartbeat

#### 方案 B：在 `agpair` 侧增加“workspace activity fallback”

如果短期内无法稳定补全 provider 回写链路，则需要加一个兜底：

- 当任务为 `antigravity`
- 且 `pending_task_count` 已清零
- 且目标仓库在 `last_activity_at` 之后出现了明确 workspace activity / git dirtiness

则任务不能继续停在 `new`

至少应推进到一个明确的中间态，例如：

- `working_untracked`
- 或 `running_without_receipt`

这样主控器知道“provider 正在做，但状态桥失联”。

这个兜底只能作为 fallback，不应替代真正的 ack/heartbeat。

### 2.5.1 额外要求：显式区分“queue drained but no ack”

除了上面的两个大方案，还需要明确一个可观测状态：

- provider 队列已清空（例如 `pending_task_count == 0`）
- 但对应 task 没有 ack / session_id / heartbeat

这不应再被显示为普通 `new`。

建议：

- 增加一个中间 phase 或者明确的 blocker / warning 字段，例如：
  - `dispatched_unacked`
  - `provider_consumed_no_ack`
- `inspect` 中直接暴露该状态

这样主控器能知道：

- 不是“没人接单”
- 而是“有人接单了，但状态桥断了”

### 2.6 测试要求

至少补以下测试：

1. **integration test：**
   模拟 `task start --executor antigravity` 后 provider 消费了任务但尚未结束，断言 task 不会永久停在 `new`

2. **integration test：**
   模拟 provider 成功 ack，断言：
   - `phase` 推进
   - `session_id` 被记录
   - `inspect` 能看到最新状态

3. **integration test：**
   模拟 provider 已开始工作但 receipt watcher 失联，断言 fallback 能把任务标成一个非 `new` 的中间态

4. **integration test：**
   模拟 provider 队列已被消费（`pending_task_count` 清零），但任务仍无 ack，断言：
   - 不会继续显示为普通 `new`
   - `inspect` / `status` 能暴露“provider 已消费但未确认”的状态

### 2.7 验收标准

满足以下条件才算修复完成：

- 对 `antigravity` executor，任务被 provider 接手后，`task status` 在合理时间内不再停留在 `new`
- `inspect` 能看到可解释的运行态，而不是只有 `dispatched`
- provider 真在改代码时，主控器不需要靠“去看仓库 diff”才能知道任务在运行
- provider 真正卡死或丢失时，状态会明确反映为 `stuck` / `blocked` / `lost`，而不是长时间假装“只是 submitted”

---

## 3. 问题 B：`isolated_worktree` 只是元数据，不是运行时行为

### 3.1 现象

用户或 controller 发出：

```bash
agpair task start \
  --repo-path /path/to/repo \
  --executor gemini \
  --isolated-worktree
```

任务状态里会记录：

- `isolated_worktree = true`

但实际执行目录仍可能是基础仓库，而不是独立 worktree。

在真实项目中，这已经导致：

- 任务本应隔离执行
- 实际却直接修改主工作区

### 3.2 当前实现原因

`isolated_worktree` / `worktree_boundary` 当前只被：

- 存入 tasks 表
- 暴露在 `status` / `inspect`

但不会自动驱动运行时行为。

相关代码路径：

- `agpair/cli/task.py`
  - `tasks.create_task(... isolated_worktree=..., worktree_boundary=...)`
  - 但真正 dispatch 给 executor 的还是原始 `repo_path`

- `agpair/executors/local_cli.py`
  - 最终用 `cwd=repo_path` 启动 wrapper

结果就是：

- 元数据说“我想隔离”
- 实际运行说“我还是在 base repo 里执行”

### 3.3 需要明确的新语义

对于 `local_cli` 类 executor（至少 `codex_cli` / `gemini_cli`）：

#### 若 `isolated_worktree = false`

- 行为保持不变
- 使用传入的 `repo_path`

#### 若 `isolated_worktree = true`

则必须在 dispatch 之前完成以下步骤：

1. 确定真实 worktree 路径
2. 如果 worktree 不存在，则创建
3. 验证该路径确实是 git worktree
4. 将**实际执行路径**重写为该 worktree 路径
5. executor 只能在这个实际 worktree 路径中启动

不能再允许“只记录意图，不改执行路径”。

### 3.4 worktree 解析规则

建议统一成下面的优先级：

1. **如果提供了 `worktree_boundary`**
   - 若是绝对路径：直接使用
   - 若是相对路径：相对 `repo_path` 解析

2. **如果没提供 `worktree_boundary`**
   - 生成一个可预测、稳定、不会冲突的默认路径
   - 例如：

```text
<repo_path>/.agpair/worktrees/<task_id>
```

或

```text
<repo_path>/.agpair/worktrees/<sanitized-task-id>
```

要求：

- 路径必须可重复计算
- retry 时行为可解释
- 不允许悄悄复用错误目录

### 3.5 安全要求

如果用户请求了 isolated worktree，必须满足下面这些 fail-fast 条件：

1. worktree 创建失败
   - 直接 `BLOCKED`
   - 不允许回退到 base repo 继续跑

2. 指定路径存在，但不是合法 git worktree
   - 直接 `BLOCKED`

3. 本地 CLI executor 收到的 `repo_path` 仍等于原始 base repo 路径
   - 直接视为 bug
   - 不允许继续 dispatch

### 3.6 状态可见性要求

`status` / `inspect` 至少应能看到：

- 原始 `repo_path`
- 实际执行路径（例如 `effective_repo_path` 或 `execution_repo_path`）
- `worktree_boundary`
- `isolated_worktree` 是否已被真正满足

否则主控器仍无法判断任务是否真的在隔离环境运行。

### 3.6.1 关键约束：不要直接覆写 `tasks.repo_path`

这是一个容易修错的点，必须明确写进方案：

- `tasks.repo_path` 当前承担的是**逻辑项目路径**语义
- 它还参与：
  - target 解析
  - doctor / inspect 按 repo 查询
  - `(repo_path, client_idempotency_key)` 唯一索引

如果直接把 `tasks.repo_path` 改成实际 worktree 路径，会引入新问题：

- 同一个项目下不同 worktree 会被当成不同 repo
- 现有 idempotency 语义会被打散
- `task list --repo-path <base repo>` 可能再也找不到它的 isolated 子任务

因此推荐的实现方式是：

- **保留** `tasks.repo_path` 作为逻辑项目路径
- **新增** 一个明确字段，例如：
  - `execution_repo_path`
  - 或 `effective_repo_path`

由这个字段保存实际执行工作区路径。

状态输出也应同时展示：

- `repo_path`：逻辑项目路径
- `execution_repo_path`：实际执行路径

这条要求非常关键，不应交给实现者自由发挥。

### 3.7 对不同 executor 的要求

#### `codex_cli`

必须保证：

- `codex exec -C ...` 使用的是**实际 worktree 路径**
- 不是 base repo 路径

#### `gemini_cli`

必须保证：

- wrapper 的 `cwd` 是**实际 worktree 路径**
- 不是 base repo 路径

#### `antigravity`

这次修复的重点不是让 Antigravity 自动创建 worktree。

但需要明确：

- 当前是否继续保持 metadata-only
- 或者至少在 `task start` 层统一把 repo_path 改写为实际 worktree 路径后再交给 provider

无论选哪种，都必须在文档和状态输出里写清楚，不能让 controller 猜。

### 3.8 测试要求

至少补以下测试：

1. **unit test：`local_cli.dispatch()`**
   - 当 `isolated_worktree=true` 且给定 `worktree_boundary`
   - 断言最终 wrapper 启动时使用的是 worktree 路径

2. **unit test：`codex executor`**
   - 断言 `codex exec -C ...` 指向 worktree 路径

3. **unit test：`gemini executor`**
   - 断言 wrapper `cwd` 指向 worktree 路径

4. **integration test：`task start --isolated-worktree`**
   - 创建一个临时 git repo
   - 触发 isolated task
   - 断言：
     - 真实 worktree 被创建
     - 任务逻辑 `repo_path` 仍指向 base repo
     - 任务 `execution_repo_path` / `effective_repo_path` 指向真实 worktree
     - base repo 未被改脏

5. **negative test**
   - 指定无效 `worktree_boundary`
   - 断言任务会 fail fast，而不是退回 base repo 执行

6. **retry behavior test**
   - 对 isolated task 执行 retry
   - 断言 retry 的 worktree 语义是明确且可预测的
   - 必须明确测试以下两种策略中采用的是哪一种：
     - 复用同一个 worktree
     - 每个 attempt 使用独立 worktree

### 3.8.1 必须先做出的产品决策：retry 与 cleanup

实现前必须明确以下策略，不要边写边猜：

#### Retry 是否复用原 worktree？

两个可接受方案：

1. **复用 worktree**
   - 优点：上下文延续好，适合长任务
   - 风险：半残 worktree 可能把 retry 污染掉

2. **attempt 级新建 worktree**
   - 例如：
     - `<repo>/.agpair/worktrees/<task_id>/attempt-1`
     - `<repo>/.agpair/worktrees/<task_id>/attempt-2`
   - 优点：隔离最强
   - 风险：需要更明确 cleanup 策略

这条必须在实现和测试里明确，不要默默决定。

#### Worktree 何时清理？

必须明确谁负责：

- task 完成后立即 teardown？
- 仅在显式 cleanup 时移除？
- stuck / abandoned 任务是否保留 worktree 供取证？

推荐：

- `committed`：默认保留，供审计与取证
- `blocked` / `stuck`：保留，直到 controller 或 cleanup 明确处理
- 提供显式 cleanup 路径，而不是任务结束自动删除

### 3.9 验收标准

满足以下条件才算修复完成：

- `--isolated-worktree` 对 local CLI executor 不再是 metadata-only
- `codex_cli` 和 `gemini_cli` 都能保证实际执行目录为真实 worktree
- worktree 无法满足时，任务会 fail fast，而不是静默落到 base repo
- `status` / `inspect` 能明确看出“声明路径”和“实际执行路径”
- 有自动化测试证明 base repo 不会再被 isolated task 误写

---

## 4. 推荐实施顺序

建议分两步修，不要一口气混在一个大 patch 里：

### Step 1：修 isolated worktree 真执行

原因：

- 这是直接破坏仓库安全边界的问题
- 一旦修完，`codex_cli` / `gemini_cli` 的风险会先大幅下降

目标：

- local CLI executor 真正使用 worktree
- fail-fast 护栏到位

### Step 2：修 Antigravity 状态同步

原因：

- 这是执行可观测性问题
- 不影响是否会误写主仓库，但会误导 controller

目标：

- provider 一旦接单，就要能在 `status` / `inspect` 里体现
- 至少不允许“已经在干活，但 phase 还是 new”

---

## 5. 另一个 AI 执行时应避免的误区

1. **不要只改文档或只改 DB 字段**
   - 这两个问题都不是文档层问题
   - 必须改运行时行为

2. **不要只改 metadata 持久化**
   - `isolated_worktree=true` 已经会入库了
   - 真正缺的是 dispatch 前的实际 worktree 处理

3. **不要用“controller 手工 chdir 到 worktree”作为最终方案**
   - 这只能作为临时 workaround
   - 不能作为 `agpair` 的长期产品语义

4. **不要把 Antigravity 问题误判成 executor 空跑**
   - 真实情况可能是 provider 已开始工作，但状态桥没回写
   - 修复目标是“状态同步”，不是“证明 provider 会跑”

---

## 6. 最终验收清单

后续 AI/工程师只有在以下全部满足时，才应宣布“彻底修复”：

### A. Isolated worktree

- `task start --isolated-worktree --executor codex` 实际运行在 worktree
- `task start --isolated-worktree --executor gemini` 实际运行在 worktree
- base repo 在上述两种场景下不会被误写
- 无法创建/验证 worktree 时会 fail fast

### B. Antigravity 状态同步

- `task start --executor antigravity` 被 provider 接手后，状态不会永久停在 `new`
- 至少有可见 ack / running / heartbeat / lost / blocked 之一
- provider 真在改代码时，controller 不需要靠手工看 git diff 才知道它在运行

### C. 回归

- 现有非 isolated task 行为不回归
- 现有 Antigravity 基本派发能力不回归
- 相关 unit / integration tests 全部通过

### D. 运行中断与运维路径

- `task abandon` / `task retry` / `task inspect` 在上述两类新状态下都能正常工作
- 不会出现 controller 想放弃任务时，CLI 自己因为状态分支 bug 崩溃

---

## 7. 建议落点

实现层建议重点检查这些文件：

- `agpair/cli/task.py`
- `agpair/executors/local_cli.py`
- `agpair/executors/codex.py`
- `agpair/executors/gemini.py`
- `agpair/executors/antigravity.py`
- `companion-extension/src/...` 中处理 provider 回写 / session / heartbeat 的链路
- `tests/unit/test_local_cli_executor.py`
- `tests/unit/test_gemini_executor.py`
- `tests/integration/test_task_start_and_status.py`

如果需要新增测试文件，优先使用明确命名，例如：

- `tests/unit/test_local_cli_executor_isolated.py`
- `tests/integration/test_task_start_isolated_runtime.py`
- `tests/integration/test_antigravity_status_sync.py`

---

## 8. 这次排障中发现的相关附带问题（建议顺手修）

这些不是主问题本身，但会妨碍主控器监督任务，建议在同一批次或紧随其后修复。

### 8.1 `task abandon` 的局部变量错误

这次实操里，`agpair task abandon <task_id>` 曾触发：

```text
UnboundLocalError: cannot access local variable 'is_local_cli_backend'
```

这说明 `abandon_task()` 某条分支里对 `is_local_cli_backend` 的引用作用域有问题。

影响：

- controller 想清理/放弃卡死任务时，CLI 本身可能再次报错
- 会降低对 stuck / orphaned task 的恢复能力

要求：

- 至少补一个 regression test，确保 `task abandon` 在：
  - antigravity task
  - local_cli task
  - 无 session_id task
  三种情况下都不会因局部变量作用域报错

### 8.2 `status` / `inspect` / `task list` 的字段对齐

如果新增了 `execution_repo_path` / `effective_repo_path` 或新的 phase：

- `task status --json`
- `task list --json`
- `inspect --json`

都要一起补，不允许只有底层有字段、上层看不到。

否则 controller 仍然无法稳定使用。
