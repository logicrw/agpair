# Postmortem: Delegation Pipeline 无法创建 Antigravity 对话

**日期**: 2026-03-30
**影响**: 所有通过 agpair 派发的 delegation 任务无法在 Antigravity 中打开 agent 对话窗口
**修复 commit**: `9ca5925`
**引入问题的 commit**: `f87b255` (fix: harden antigravity delegation lifecycle)

---

## 一、故障现象

通过 `agpair task start` 派发任务后：

- CLI 返回 TASK_ID，daemon 正常 ACK
- companion extension 收到消息，heartbeat 正常
- 但 **Antigravity 从未打开 agent 对话窗口**
- `last_workspace_activity_at` 始终为 None — agent 从未工作
- 任务永远停在 `acked` 阶段

## 二、根因分析（三层问题叠加）

### 第一层：Antigravity SDK 返回幽灵 Session ID（底层根因）

`SessionController.createBackgroundSession()` 有 4 条 session 创建路径：

| 路径 | 方法 | 是否能创建真实对话 |
|------|------|-------------------|
| Path 1 | `sdk.ls.createCascade()` | **否** — 返回幽灵 ID |
| Path 2 | Path 1 + CSRF 重试 | **否** — 同上 |
| Path 3 | `sdk.cascade.createSession()` | **否** — 返回幽灵 ID |
| Path 4 | `vscode.commands: startNewConversation + sendPromptToAgentPanel` | **是** — 唯一能工作的路径 |

**什么是幽灵 Session ID：**
- `createCascade()` 和 `cascade.createSession()` 返回一个 UUID 格式的 session ID
- 这个 ID 会出现在 `sdk.cascade.getSessions()` 的结果中
- `focusSession()` 也会返回成功
- 但 **Antigravity UI 从未打开对应的对话窗口**，agent 从未启动
- 本质上是 SDK 层面注册了 session 记录，但 UI 层面没有渲染

这是 Antigravity 平台侧的问题，不是 companion extension 的 bug。在当前版本的 Antigravity 中，**只有直接调用 vscode commands（Path 4）才能真正打开对话并注入 prompt。**

### 第二层：`f87b255` 阻断了唯一能工作的路径

`f87b255` 做了一个看似合理的改动：为 `createBackgroundSession` 添加了 `allowInteractiveFallback` 选项，delegation 调用方传 `false` 以避免 UI 闪烁。

```typescript
// f87b255 添加的门控
if (!allowInteractiveFallback) {
  return { ok: false, session_id: "", error: msg };
}
// Path 3, 4 在这里，被门控阻断
```

**问题在于：**

1. Path 1 返回幽灵 ID 后直接 `return { ok: true }` — 函数提前返回，永远不会走到 Path 3/4
2. 即使 Path 1 失败，`allowInteractiveFallback: false` 也会在 Path 3/4 之前返回错误
3. **Path 4（唯一能工作的路径）在 delegation 场景下被彻底阻断**

**为什么旧代码能工作：**

旧代码（`f87b255` 之前）没有 `allowInteractiveFallback` 门控。所有 4 条路径始终可用。当 Path 1 的幽灵 ID 碰巧没有通过 `pickFreshSessionId` 检测（session diff 没发现"新" session），代码会 fallthrough 到 Path 4，后者通过 vscode commands 打开真正的对话。

### 第三层：Path 4 的 session 检测过于严格

即使 Path 4 的 vscode commands 成功打开了对话，原始代码仍然依赖 `getSessions()` diff 来检测新 session ID：

```typescript
// 如果 getSessions() 检测不到新 session，就抛错
if (!sessionId) {
  throw new Error("Direct commands created no fresh session");
}
```

由于 `getSessions()` 在当前 Antigravity 环境中不可靠（无法反映通过 vscode commands 创建的对话），Path 4 即使成功创建了对话，也会因为检测失败而报错。**对话确实打开了，agent 也在工作，但 delegation 系统认为创建失败了。**

## 三、时间线

| 时间 (UTC) | 事件 |
|------------|------|
| 03:17 | `f87b255` 提交（添加 `allowInteractiveFallback` 门控） |
| 04:34 | AG-P0-1-TS attempt 6 派发 — **旧编译版本仍在运行，正常工作** |
| 06:04 | 上一个任务最后的 workspace activity |
| **06:23** | **extension 重新 bundle（`npm run build`）— 新代码生效** |
| 06:25 | TASK-DA2E 派发 — **第一个失败的任务** |
| 06:34 | TASK-0482 — 失败 |
| 06:47 | TASK-A482 — 失败 |

**关键节点：** extension 在 06:23 UTC 被重新 bundle，新代码生效。在此之前运行的是旧编译版本（没有 `allowInteractiveFallback` 门控），所以任务能正常工作。

## 四、修复方案

### 修改 1: 跳过 Path 1-3（幽灵 ID 路径）

```typescript
// sessionController.ts
// 直接跳过 LS bridge 和 SDK cascade 路径
console.log("[session] Paths 1/2 (LS createCascade) skipped — known phantom-ID issue");
console.log("[session] Path 3 (SDK cascade) skipped — same phantom-ID issue");
// 直接走 Path 4
```

### 修改 2: Path 4 不再强依赖 `getSessions()` 检测

```typescript
// 如果 getSessions() 检测不到新 session，生成一个 tracking ID
// 而不是报错
if (!sessionId) {
  sessionId = `ag-cmd-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  console.log(`[session] getSessions() could not detect new session; using tracking ID`);
}
// 不再 throw，直接返回成功
return { ok: true, session_id: sessionId };
```

### 修改 3: 所有调用方设置 `allowInteractiveFallback: true`

```typescript
// agentBusDelegationService.ts, taskExecutionService.ts, delegationReceiptWatcher.ts
const result = await this.sessionCtrl.createBackgroundSession(prompt, {
  allowInteractiveFallback: true,  // 确保 Path 4 可达
  contextLabel: `delegated task ${taskId}`,
});
```

## 五、给 Codex 的教训和规则

### 教训 1: 不要假设 SDK API 的返回值是可信的

`createCascade()` 返回了 session ID，`getSessions()` 也能查到这个 ID，`focusSession()` 也返回成功 — 但对话从未真正创建。**SDK 返回"成功"不等于操作真的成功了。** 在添加新的代码路径限制之前，必须验证每条路径在真实环境中是否实际工作。

**规则：** 修改 fallback 链时，先手动测试每条路径是否能独立工作，再决定哪些可以禁用。

### 教训 2: 不要在不理解 fallback 机制的情况下添加门控

`allowInteractiveFallback: false` 的意图是好的（避免 delegation 任务的 UI 闪烁），但它阻断了唯一能工作的路径。问题在于：

- **没有验证 headless 路径（Path 1-3）是否真的能独立工作**
- 假设 Path 1 成功就意味着对话创建成功
- 没有端到端测试（从派发到对话窗口打开再到 agent 开始工作）

**规则：** 添加门控或禁用 fallback 路径之前，必须有端到端验证证明被保留的路径确实能完成完整工作流。不要只看 `ok: true` 就认为成功了。

### 教训 3: 构建命令很重要

companion extension 的构建流程：

| 命令 | 作用 | 产出 |
|------|------|------|
| `npm run compile` | TypeScript 编译（`tsc`） | 独立 `.js` 文件到 `dist/`（用于测试） |
| `npm run build` | esbuild bundle | 单个 `dist/extension.js`（**extension 实际加载的**） |

**`npm run compile` 不会更新 extension 的 bundle。** 只有 `npm run build` 才会。如果只跑 `tsc` 然后复制 `dist/extension.js`，安装的仍然是旧的 bundle。

**规则：** 修改 companion extension 代码后，必须用 `npm run build`（不是 `npm run compile`）构建，然后复制到 `~/.antigravity/extensions/` 并 reload Antigravity 窗口。验证方法：`grep "你改的关键字符串" dist/extension.js` 确认代码在 bundle 中。

### 教训 4: 调试 session 创建问题的正确方法

本次调试过程中走了很多弯路。正确的调试路径应该是：

1. **先看 developer console 日志** — extension 中所有 `console.log("[session] ...")` 都会出现在 Antigravity 的开发者工具控制台中。过滤 `[session]` 可以看到走了哪条 Path、每条 Path 的结果
2. **确认构建产物包含改动** — `grep "关键字符串" dist/extension.js` 立即确认
3. **区分"SDK 返回成功"和"对话真的打开了"** — 用眼睛看 Antigravity 的 Agent 面板是否出现新对话，不要只看 API 返回值
4. **检查时间线** — 对比 extension bundle 的修改时间和任务失败的时间，快速定位是哪次编译引入的问题

### 教训 5: 改动 companion extension 后的验证清单

每次修改 companion extension 代码后，必须执行以下步骤：

```bash
# 1. 构建（不是 compile！）
cd companion-extension && npm run build

# 2. 确认改动在 bundle 中
grep "你改的关键字符串" dist/extension.js

# 3. 安装
cp dist/extension.js ~/.antigravity/extensions/logicrw.antigravity-companion-extension-1.0.0/dist/extension.js

# 4. Reload Antigravity 窗口

# 5. 端到端测试
agpair task start --repo-path <path> --no-wait --body "简单测试任务"
# 然后用眼睛确认 Antigravity 是否打开了对话窗口
```

## 六、后续工作

1. **长期修复：** 调查 Antigravity SDK 为什么 `createCascade()` / `createSession()` 返回幽灵 ID。如果这是 Antigravity 的 bug，提交 issue。一旦修复，可以重新启用 Path 1-3 获得更好的 headless 体验（无 UI 闪烁）。
2. **监控：** Path 4 使用生成的 tracking ID 替代真实 session ID，这意味着 `sessionExists()` 等基于 ID 的查询不会工作。如果后续功能需要真实 session ID，需要找到可靠的检测方法。
3. **测试：** 为 delegation 流程添加端到端冒烟测试，在 CI 中验证对话是否真的能打开。
