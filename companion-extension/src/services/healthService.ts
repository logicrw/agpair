/**
 * Health Service — reports real SDK and monitor state.
 *
 * /health must truthfully reflect:
 *   - sdk_initialized: whether AntigravitySDK.initialize() succeeded
 *   - monitor_running: whether EventMonitor is actively polling
 *   - ls_bridge_ready: whether LSBridge discovered LS port/CSRF
 *   - active_tasks: count of bound sessions
 *   - delegation_auto_return: status of the delegation receipt watcher
 *
 * Spec: codex_antigravity_companion_extension_ts_spec.md §12
 */

import type { AntigravitySDK } from "antigravity-sdk";
import type { MonitorController } from "../sdk/monitorController";
import { TaskSessionStore } from "../state/taskSessionStore";
import type { BridgeAuthMode } from "../bridge/authResolver";

export interface DelegationAutoReturnStatus {
  enabled: boolean;
  receipt_watcher_running: boolean;
  heartbeat_running: boolean;
  heartbeat_interval_ms: number;
  receipt_dir: string;
  tracker_summary: {
    total: number;
    pending: number;
    completed: number;
    tasks: Array<{
      taskId: string;
      status: string;
      sessionId: string;
      ackedAt: string;
      lastActivityAt: string;
      lastHeartbeatAt: string | null;
      terminalSentAt: string | null;
    }>;
  };
}

export interface HealthResponse {
  ok: boolean;
  extension_loaded: boolean;
  extension_host: boolean;
  extension_id: string | null;
  extension_path: string | null;
  sdk_initialized: boolean;
  ls_bridge_ready: boolean;
  monitor_running: boolean;
  active_tasks: number;
  bridge_port: number;
  /** Auth mode: "configured", "generated", or "insecure". Never includes the token. */
  bridge_auth_mode: BridgeAuthMode;
  /** True when mutating endpoints require Authorization header. */
  bridge_mutating_auth_required: boolean;
  workspace_paths: string[];
  agent_bus_watch_running: boolean;
  agent_bus_watch_mode: string;
  agent_bus_watch_pid: number | null;
  agent_bus_delegation_enabled: boolean;
  delegation_stale_timeout_ms: number;
  delegation_stale_guard_degraded: boolean;
  delegation_auto_return: DelegationAutoReturnStatus;
  version: string;
  timestamp: string;
}

export class HealthService {
  private _bridgePort = 0;
  private _bridgeAuthMode: BridgeAuthMode = "generated";

  constructor(
    private readonly sdk: AntigravitySDK | null,
    private readonly monitor: MonitorController | null,
    private readonly sessionStore: TaskSessionStore,
    private readonly version: string,
    private readonly extensionMetadata: {
      id: string | null;
      path: string | null;
    } = {
      id: null,
      path: null,
    },
    private readonly workspacePathsProvider: () => string[] = () => [],
    private readonly agentBusWatchStatusProvider: () => {
      running: boolean;
      mode: string;
      pid: number | null;
    } = () => ({ running: false, mode: "disabled", pid: null }),
    private readonly agentBusDelegationStatusProvider: () => DelegationAutoReturnStatus = () => ({
      enabled: false,
      receipt_watcher_running: false,
      heartbeat_running: false,
      heartbeat_interval_ms: 0,
      receipt_dir: "",
      tracker_summary: { total: 0, pending: 0, completed: 0, tasks: [] },
    }),
    private readonly delegationTimeoutMsProvider: () => number = () => 0,
  ) {}

  /** Set the actual bridge port after listen succeeds. */
  setBridgePort(port: number): void {
    this._bridgePort = port;
  }

  /** Set the effective bridge auth mode (called from extension.ts). */
  setBridgeAuthMode(mode: BridgeAuthMode): void {
    this._bridgeAuthMode = mode;
  }

  getHealth(): HealthResponse {
    const watchStatus = this.agentBusWatchStatusProvider();
    const delegationStatus = this.agentBusDelegationStatusProvider();
    const rawStaleTimeout = this.delegationTimeoutMsProvider();
    const isStaleGuardDegraded =
      !Number.isFinite(rawStaleTimeout) || rawStaleTimeout <= 0;
    const actualStaleTimeoutMs = isStaleGuardDegraded ? 0 : rawStaleTimeout;

    return {
      ok: true,
      extension_loaded: true,
      extension_host: true,
      extension_id: this.extensionMetadata.id,
      extension_path: this.extensionMetadata.path,
      sdk_initialized: this.sdk?.isInitialized ?? false,
      ls_bridge_ready: this.sdk?.ls?.isReady ?? false,
      monitor_running: this.monitor?.isRunning ?? false,
      active_tasks: this.sessionStore.count(),
      bridge_port: this._bridgePort,
      bridge_auth_mode: this._bridgeAuthMode,
      bridge_mutating_auth_required: this._bridgeAuthMode !== "insecure",
      workspace_paths: this.workspacePathsProvider(),
      agent_bus_watch_running: watchStatus.running,
      agent_bus_watch_mode: watchStatus.mode,
      agent_bus_watch_pid: watchStatus.pid,
      agent_bus_delegation_enabled: delegationStatus.enabled,
      delegation_stale_timeout_ms: actualStaleTimeoutMs,
      delegation_stale_guard_degraded: isStaleGuardDegraded,
      delegation_auto_return: delegationStatus,
      version: this.version,
      timestamp: new Date().toISOString(),
    };
  }
}
