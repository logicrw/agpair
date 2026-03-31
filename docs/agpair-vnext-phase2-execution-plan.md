# agpair vNext 第二阶段执行计划

> 文档状态：执行计划
> 日期：2026-03-31
> 目标：把当前已经存在的多执行器内核 groundwork，推进到 “Codex 作为正式第二执行器可用” 的状态。

---

## 1. 当前完成状态

第一阶段已经完成的内容：

- `task watch` 已落地，控制器不再只能靠外部 60 秒轮询。
- `ExecutorAdapter` 已从 Antigravity 路径中抽出。
- `CodexExecutor` 已存在，具备内部 `dispatch / poll / cancel` groundwork。
- `doctor / task status` 已能暴露 backend 可见性。
- continuation capability matrix 已编码：
  - `antigravity = same_session`
  - `codex_cli = fresh_resume_first`

当前还没完成的关键缺口：

1. `task start` 仍然固定派给 Antigravity，不能正式选择 `codex`
2. Codex adapter 还没真正接入主生命周期
3. Codex backend 的 terminal receipt 还没进入正式任务收口链
4. Codex backend 的 continuation 还只是策略声明，不是完整生产路径

一句话：**零件已经有了，但主执行链还没切通。**

---

## 2. 第二阶段目标

把 agpair 从：

- “Antigravity 专用 delegation 工具，外加一个内部 Codex adapter 草稿”

推进到：

- “可以正式用 `Codex` 作为第二执行器启动任务，并且沿用 agpair 现有 lifecycle、receipt、doctor、watch、inspect 能力”

这一阶段不追求：

- 多执行器并发调度
- Gemini backend
- Claude Code plugin
- Codex same-session continuation

---

## 3. 设计原则

1. 先打通主路径，再加花样
   - 优先完成 `task start --executor codex`
   - 不要在主路径没通前继续扩展更多 backend

2. 保持 canonical receipt 不变
   - 不论底层是 Antigravity 还是 Codex，最终都必须回到 agpair v1 receipt

3. continuation 先保守
   - Antigravity 继续 `same_session`
   - Codex 明确走 `fresh_resume_first`
   - 不伪装 Codex 支持旧 session continuation

4. 先做 additive change
   - 默认执行器仍然是 Antigravity
   - 新能力通过显式选择启用

---

## 4. 分阶段工作包

### WP-CODEX-01：正式引入 executor 选择

目标：

- 给 `task start` 增加用户可见的 `--executor antigravity|codex`
- 把所选 backend 持久化到任务记录中
- 保持默认值仍然是 `antigravity`

建议范围：

- `agpair/cli/task.py`
- `agpair/models.py`
- `agpair/storage/schema.sql`
- `agpair/storage/db.py`
- `agpair/storage/tasks.py`
- 对应 integration tests

交付门槛：

- 不传 `--executor` 时行为不变
- 传 `--executor codex` 时，任务记录里可见 backend
- `doctor / status / inspect / watch` 不会丢失 backend 信息

### WP-CODEX-02：把 Codex backend 接入正式生命周期

目标：

- `task start --executor codex` 真的走 `CodexExecutor`
- dispatch 后能产出稳定 task ref
- daemon / CLI 能沿现有主路径消费 Codex terminal state

建议范围：

- `agpair/executors/codex.py`
- `agpair/daemon/loop.py`
- `agpair/terminal_receipts.py`
- 与状态消费相关的 tests

交付门槛：

- Codex backend 完成后能落成 canonical terminal receipt
- `task status / wait / watch` 不需要知道底层是谁
- `blocked / committed` 正常收口

### WP-CODEX-03：Codex backend 的 live 验证与 hardening

目标：

- 跑通至少一条真实 `task start --executor codex`
- 验证 committed / blocked 两条收口路径
- 检查 cancel、timeout、stuck 语义

重点：

- 这一步不是“写代码”，而是“验证生产语义”
- 需要真实 evidence，不接受仅看源码或 unit test

### WP-CODEX-04：Codex continuation 的 fresh-resume-first 落地

目标：

- review feedback 能在 `codex` backend 下转成 context-carry fresh resume
- 不依赖旧 session continuation

范围：

- `task continue`
- `task approve/reject`
- continuation payload 组装
- 相关 integration tests

交付门槛：

- 对 `codex` backend，继续执行不再依赖旧 session 存活
- review context 不丢失

---

## 5. 推荐顺序

按 ROI 与风险排序，建议顺序是：

1. `WP-CODEX-01`
2. `WP-CODEX-02`
3. `WP-CODEX-03`
4. `WP-CODEX-04`

原因：

- 没有 `--executor codex`，就还谈不上正式第二执行器
- 没有主生命周期收口，Codex adapter 只是内部零件
- live 验证必须在主路径接通后做
- continuation 是 correctness 增强，但不必抢在主启动链前面

---

## 6. 不该做的事

- 不要把 Gemini adapter 和 Codex adapter 同时推进
- 不要把 plugin 集成排到主路径之前
- 不要为了让 Codex 更“像 Antigravity”，伪装 same-session continuation
- 不要为了多执行器支持而退化现有 Antigravity 行为

---

## 7. 当前建议

下一张应该发给 Antigravity 的卡是：

**`WP-CODEX-01：正式引入 executor 选择`**

这张卡的目标不是“打通所有东西”，而是：

- 让 `task start --executor codex` 在产品层成为合法入口
- 把 executor backend 变成任务记录中的一等字段
- 保持默认行为不变

如果这张卡做稳了，后续 `WP-CODEX-02` 就可以在不碰 CLI 入口设计的前提下，专注于真正打通 Codex lifecycle。
