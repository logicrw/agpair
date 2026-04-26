import * as childProcess from "child_process";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import type { Readable } from "stream";

export interface AgentBusWatchStatus {
  enabled: boolean;
  running: boolean;
  mode: "disabled" | "running" | "stopped" | "failed" | "skipped_existing_lock";
  command: string;
  pid: number | null;
  lock_path: string;
  inbox_path: string;
  last_error: string;
}

export interface AgentBusWatchServiceOptions {
  enabled: boolean;
  command: string;
  intervalMs: number;
  workspacePathsProvider: () => string[];
  outputChannel: { appendLine(message: string): void };
  notify?: (message: string) => void;
  onMessages?: (messages: AgentBusMessage[]) => void | Promise<void>;
  spawnFn?: typeof childProcess.spawn;
  isPidAlive?: (pid: number) => boolean;
  lockPath?: string;
  inboxPath?: string;
}

interface WatchLockRecord {
  pid: number;
  command: string;
  workspace_paths: string[];
  started_at: string;
}

type WatchBatchMessage = {
  id?: number;
  task_id?: string | null;
  status?: string | null;
  body?: string | null;
  timestamp?: string | null;
  claim_id?: string | null;
  lease_expires_at?: string | null;
};

export type AgentBusMessage = WatchBatchMessage;

type WatchBatchEvent = {
  ok?: boolean;
  mode?: string;
  claimed?: number;
  reserved?: number;
  messages?: WatchBatchMessage[];
  emitted_at?: string;
};

export class AgentBusWatchService {
  private readonly outputChannel: { appendLine(message: string): void };
  private readonly notify: (message: string) => void;
  private readonly onMessages?: (
    messages: AgentBusMessage[],
  ) => void | Promise<void>;
  private readonly spawnFn: typeof childProcess.spawn;
  private readonly isPidAlive: (pid: number) => boolean;
  private readonly workspacePathsProvider: () => string[];
  private readonly enabled: boolean;
  private readonly intervalMs: number;
  private readonly requestedCommand: string;
  private readonly lockPath: string;
  private readonly inboxPath: string;
  private static readonly RETRY_INTERVAL_MS = 5_000;
  private static readonly MAX_RETRIES = 30; // give up after ~2.5 min
  private child: childProcess.ChildProcessByStdio<
    null,
    Readable,
    Readable
  > | null = null;
  private ownPid: number | null = null;
  private retryTimer: ReturnType<typeof setInterval> | null = null;
  private retryCount = 0;
  private stdoutBuffer = "";
  private stderrBuffer = "";
  private fallbackNoRepoPath = false;
  private status: AgentBusWatchStatus;

  constructor(options: AgentBusWatchServiceOptions) {
    this.outputChannel = options.outputChannel;
    this.notify = options.notify ?? (() => undefined);
    this.onMessages = options.onMessages;
    this.spawnFn = options.spawnFn ?? childProcess.spawn;
    this.isPidAlive = options.isPidAlive ?? defaultIsPidAlive;
    this.workspacePathsProvider = options.workspacePathsProvider;
    this.enabled = options.enabled;
    this.intervalMs = Math.max(options.intervalMs, 100);
    this.requestedCommand = options.command.trim() || "agent-bus";
    this.lockPath =
      options.lockPath ??
      path.join(os.homedir(), ".agpair", "agent_bus_watch_code.lock.json");
    this.inboxPath =
      options.inboxPath ??
      path.join(os.homedir(), ".agpair", "agent_bus_inbox_code.jsonl");
    this.status = {
      enabled: this.enabled,
      running: false,
      mode: this.enabled ? "stopped" : "disabled",
      command: this.resolveCommand(),
      pid: null,
      lock_path: this.lockPath,
      inbox_path: this.inboxPath,
      last_error: "",
    };
  }

  start(): boolean {
    if (!this.enabled) {
      this.status = {
        ...this.status,
        enabled: false,
        running: false,
        mode: "disabled",
      };
      this.outputChannel.appendLine(
        "[companion] agent-bus watch disabled by configuration.",
      );
      return false;
    }

    const existing = this.readLock();
    if (existing && this.isPidAlive(existing.pid)) {
      this.status = {
        ...this.status,
        running: false,
        mode: "skipped_existing_lock",
        pid: existing.pid,
        command: existing.command || this.status.command,
        last_error: "",
      };
      this.outputChannel.appendLine(
        `[companion] agent-bus watch already owned by pid=${existing.pid}; skipping duplicate watcher.`,
      );
      this.scheduleRetry();
      return false;
    }

    const command = this.resolveCommand();
    this.status = { ...this.status, command };
    this.ensureParentDir(this.lockPath);
    this.ensureParentDir(this.inboxPath);

    try {
      const args = [
        "watch",
        "--sender",
        "desktop",
        "--reader",
        "code",
        "--full",
        "--interval-ms",
        String(this.intervalMs),
        "--lease-ms",
        String(Math.max(this.intervalMs * 5, 30_000)),
      ];

      const repoPath = this.workspacePathsProvider()[0];
      if (repoPath && !this.fallbackNoRepoPath) {
        args.push("--repo-path", repoPath);
      } else if (repoPath && this.fallbackNoRepoPath) {
        this.outputChannel.appendLine("[companion] Using fallback mode: skipping --repo-path isolation due to outdated agent-bus.");
      }

      const child = this.spawnFn(command, args, {
        cwd: this.workspacePathsProvider()[0] || os.homedir(),
        env: process.env,
        stdio: ["ignore", "pipe", "pipe"],
      });

      this.child = child;
      this.ownPid = child.pid ?? null;
      this.stdoutBuffer = "";
      this.stderrBuffer = "";

      if (this.ownPid) {
        this.writeLock({
          pid: this.ownPid,
          command,
          workspace_paths: this.workspacePathsProvider(),
          started_at: new Date().toISOString(),
        });
      }

      child.stdout.setEncoding("utf8");
      child.stderr.setEncoding("utf8");

      child.stdout.on("data", (chunk: string) => this.handleStdout(chunk));
      child.stderr.on("data", (chunk: string) => this.handleStderr(chunk));
      child.on("error", (err: Error) => {
        this.status = {
          ...this.status,
          running: false,
          mode: "failed",
          last_error: err.message,
        };
        this.cleanupOwnedLock();
        this.outputChannel.appendLine(
          `[companion] agent-bus watch failed to start: ${err.message}`,
        );
        this.scheduleRetry();
      });
      child.on("exit", (code, signal) => {
        const detail = signal ? `signal=${signal}` : `code=${code ?? 0}`;
        this.status = {
          ...this.status,
          running: false,
          mode: this.status.mode === "failed" ? "failed" : "stopped",
          pid: null,
        };
        this.cleanupOwnedLock();
        this.child = null;
        this.ownPid = null;
        this.outputChannel.appendLine(
          `[companion] agent-bus watch exited (${detail}).`,
        );
        this.scheduleRetry();
      });

      this.status = {
        ...this.status,
        running: true,
        mode: "running",
        pid: this.ownPid,
        last_error: "",
      };
      this.outputChannel.appendLine(
        `[companion] agent-bus watch started (pid=${this.ownPid ?? "unknown"}, inbox=${this.inboxPath}).`,
      );
      return true;
    } catch (err: any) {
      this.status = {
        ...this.status,
        running: false,
        mode: "failed",
        last_error: err?.message ?? String(err),
      };
      this.cleanupOwnedLock();
      this.outputChannel.appendLine(
        `[companion] agent-bus watch failed to start: ${this.status.last_error}`,
      );
      return false;
    }
  }

  private scheduleRetry(): void {
    if (this.retryTimer || !this.enabled) return;
    this.retryTimer = setInterval(() => {
      if (this.status.running) {
        this.retryCount = 0;
        this.clearRetryTimer();
        return;
      }
      this.retryCount++;
      if (this.retryCount > AgentBusWatchService.MAX_RETRIES) {
        this.outputChannel.appendLine(
          `[companion] agent-bus watch retry: giving up after ${AgentBusWatchService.MAX_RETRIES} attempts.`,
        );
        this.clearRetryTimer();
        return;
      }
      this.outputChannel.appendLine(
        `[companion] agent-bus watch retry ${this.retryCount}/${AgentBusWatchService.MAX_RETRIES}: attempting start...`,
      );
      if (this.start()) {
        this.retryCount = 0;
        this.clearRetryTimer();
      }
    }, AgentBusWatchService.RETRY_INTERVAL_MS);
  }

  private clearRetryTimer(): void {
    if (this.retryTimer) {
      clearInterval(this.retryTimer);
      this.retryTimer = null;
    }
  }

  dispose(): void {
    this.clearRetryTimer();
    if (this.child) {
      this.child.kill();
    } else {
      this.cleanupOwnedLock();
    }
    this.child = null;
    this.ownPid = null;
    this.status = {
      ...this.status,
      running: false,
      mode: this.enabled ? "stopped" : "disabled",
      pid: null,
    };
  }

  getStatus(): AgentBusWatchStatus {
    return { ...this.status };
  }

  private resolveCommand(): string {
    if (
      path.isAbsolute(this.requestedCommand) &&
      fs.existsSync(this.requestedCommand)
    ) {
      return this.requestedCommand;
    }
    if (this.requestedCommand === "agent-bus") {
      const homeCommand = path.join(os.homedir(), ".local", "bin", "agent-bus");
      if (fs.existsSync(homeCommand)) {
        return homeCommand;
      }
    }
    return this.requestedCommand;
  }

  private ensureParentDir(targetPath: string): void {
    fs.mkdirSync(path.dirname(targetPath), { recursive: true });
  }

  private handleStdout(chunk: string): void {
    this.stdoutBuffer += chunk;
    const lines = this.stdoutBuffer.split(/\r?\n/);
    this.stdoutBuffer = lines.pop() ?? "";
    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      this.handleStdoutLine(trimmed);
    }
  }

  private handleStderr(chunk: string): void {
    this.stderrBuffer += chunk;
    const lines = this.stderrBuffer.split(/\r?\n/);
    this.stderrBuffer = lines.pop() ?? "";
    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      this.outputChannel.appendLine(`[companion] agent-bus stderr: ${trimmed}`);
      if (trimmed.includes("unrecognized argument") && trimmed.includes("--repo-path")) {
        this.fallbackNoRepoPath = true;
      }
    }
  }

  private handleStdoutLine(line: string): void {
    try {
      const event = JSON.parse(line) as WatchBatchEvent;
      this.appendInboxLine(line);
      if (
        !event.ok ||
        event.mode !== "watch" ||
        !Array.isArray(event.messages) ||
        event.messages.length === 0
      ) {
        this.outputChannel.appendLine(`[companion] agent-bus: ${line}`);
        return;
      }

      const taskIds = Array.from(
        new Set(
          event.messages
            .map((message) => message.task_id)
            .filter(
              (taskId): taskId is string =>
                typeof taskId === "string" && taskId.length > 0,
            ),
        ),
      );
      const reservedCount =
        typeof event.reserved === "number"
          ? event.reserved
          : (event.claimed ?? event.messages.length);
      const summary = `[companion] agent-bus reserved ${reservedCount} message(s) for task(s): ${taskIds.join(", ")}`;
      this.outputChannel.appendLine(summary);
      this.notify(
        `Antigravity inbox received ${event.messages.length} message(s): ${taskIds.join(", ")}`,
      );
      if (this.onMessages) {
        void this.handleReservedMessages(event.messages).catch((err) => {
          this.outputChannel.appendLine(
            `[companion] agent-bus handoff failed: ${err instanceof Error ? err.message : String(err)}`,
          );
        });
      }
    } catch {
      this.outputChannel.appendLine(`[companion] agent-bus stdout: ${line}`);
    }
  }

  private appendInboxLine(line: string): void {
    try {
      this.ensureParentDir(this.inboxPath);
      fs.appendFileSync(this.inboxPath, `${line}\n`, "utf8");
    } catch (err: any) {
      this.outputChannel.appendLine(
        `[companion] Failed to append agent-bus inbox log: ${err?.message ?? String(err)}`,
      );
    }
  }

  private async handleReservedMessages(messages: AgentBusMessage[]): Promise<void> {
    if (!this.onMessages) return;
    for (const message of messages) {
      await Promise.resolve(this.onMessages([message]));
      const claimId =
        typeof message.claim_id === "string" && message.claim_id.length > 0
          ? message.claim_id
          : null;
      if (!claimId) {
        continue;
      }
      await this.settleClaim(claimId);
    }
  }

  private settleClaim(claimId: string): Promise<void> {
    return new Promise((resolve, reject) => {
      const child = this.spawnFn(
        this.status.command,
        [
          "settle",
          "--reader",
          "code",
          "--claims",
          claimId,
        ],
        {
          cwd: this.workspacePathsProvider()[0] || os.homedir(),
          env: process.env,
          stdio: ["ignore", "pipe", "pipe"],
        },
      );

      let stdout = "";
      let stderr = "";
      child.stdout.setEncoding("utf8");
      child.stderr.setEncoding("utf8");
      child.stdout.on("data", (chunk: string) => {
        stdout += chunk;
      });
      child.stderr.on("data", (chunk: string) => {
        stderr += chunk;
      });
      child.on("error", reject);
      child.on("exit", (code) => {
        if (code === 0) {
          resolve();
          return;
        }
        reject(
          new Error(
            stderr.trim() || stdout.trim() || `agent-bus settle exited ${code ?? 1}`,
          ),
        );
      });
    });
  }

  private readLock(): WatchLockRecord | null {
    try {
      if (!fs.existsSync(this.lockPath)) {
        return null;
      }
      const raw = fs.readFileSync(this.lockPath, "utf8");
      const parsed = JSON.parse(raw) as Partial<WatchLockRecord>;
      if (typeof parsed.pid !== "number" || !Number.isFinite(parsed.pid)) {
        return null;
      }
      return {
        pid: parsed.pid,
        command:
          typeof parsed.command === "string"
            ? parsed.command
            : this.resolveCommand(),
        workspace_paths: Array.isArray(parsed.workspace_paths)
          ? parsed.workspace_paths.filter(
              (value): value is string => typeof value === "string",
            )
          : [],
        started_at:
          typeof parsed.started_at === "string" ? parsed.started_at : "",
      };
    } catch {
      return null;
    }
  }

  private writeLock(record: WatchLockRecord): void {
    fs.writeFileSync(this.lockPath, JSON.stringify(record, null, 2), "utf8");
  }

  private cleanupOwnedLock(): void {
    try {
      if (!fs.existsSync(this.lockPath)) {
        return;
      }
      const existing = this.readLock();
      if (!existing) {
        fs.unlinkSync(this.lockPath);
        return;
      }
      if (this.ownPid && existing.pid === this.ownPid) {
        fs.unlinkSync(this.lockPath);
      }
    } catch {
      // best-effort
    }
  }
}

function defaultIsPidAlive(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}
