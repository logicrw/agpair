/**
 * agpair Companion Extension — VS Code Extension Entry Point.
 *
 * Runs inside the Antigravity IDE extension host.
 * Receives a real ExtensionContext from the host lifecycle.
 *
 * Lifecycle:
 *   activate(context) →
 *     1. Initialize AntigravitySDK with real context
 *     2. Initialize LS bridge for headless session creation
 *     3. Create services (SessionController, MonitorController, etc.)
 *     4. Start bridge HTTP server on localhost
 *     5. Start monitor polling
 *
 *   deactivate() →
 *     1. Stop monitor
 *     2. Close bridge server
 *     3. Dispose SDK
 *
 * Bundled with the agpair repository for standalone demo operation.
 */

import * as vscode from "vscode";
import * as crypto from "crypto";
import * as http from "http";
import * as path from "path";
import * as os from "os";
import { AntigravitySDK } from "antigravity-sdk";

import { TaskSessionStore } from "./state/taskSessionStore";
import { PendingEventStore } from "./state/pendingEventStore";
import { DelegationTaskTracker } from "./state/delegationTaskTracker";
import { SessionController } from "./sdk/sessionController";
import { MonitorController } from "./sdk/monitorController";
import { TaskExecutionService } from "./services/taskExecutionService";
import { HealthService } from "./services/healthService";
import { AgentBusWatchService } from "./services/agentBusWatchService";
import { AgentBusDelegationService } from "./services/agentBusDelegationService";
import { startBridgeServer, BridgeConfig } from "./bridge/httpServer";
import { removeWrittenMarkers, writeMarkerToDir } from "./bridge/discoveryMarkers";
import { resolveAuth } from "./bridge/authResolver";

let bridgeServer: http.Server | null = null;
let sdk: AntigravitySDK | null = null;
let monitorCtrl: MonitorController | null = null;
let agentBusWatchService: AgentBusWatchService | null = null;
let agentBusDelegationService: AgentBusDelegationService | null = null;
let activeBridgePort = 0;
let delegationTracker: DelegationTaskTracker | null = null;
const BRIDGE_PORT_MARKER = "bridge_port";
const BRIDGE_AUTH_TOKEN_MARKER = "bridge_auth_token";
const BRIDGE_AUTH_TOKEN_MODE = 0o600;

// ── Module-level stores (shared across services) ────────────────
const sessionStore = new TaskSessionStore();
const eventStore = new PendingEventStore();

/**
 * Extension activation — called by the Antigravity/VS Code host.
 */
export async function activate(context: vscode.ExtensionContext): Promise<void> {
  const config = vscode.workspace.getConfiguration("antigravityCompanion");
  const port = config.get<number>("bridgePort", 8765);
  const configuredToken = config.get<string>("bridgeToken", "");
  const insecureMode = config.get<boolean>("bridgeInsecure", false);
  const monitorInterval = config.get<number>("monitorIntervalMs", 3000);
  const trajectoryInterval = config.get<number>("trajectoryIntervalMs", 5000);
  const agentBusWatchEnabled = config.get<boolean>("agentBusWatchEnabled", true);
  const agentBusWatchInterval = config.get<number>("agentBusWatchIntervalMs", 1000);
  const agentBusWatchCommand = config.get<string>("agentBusWatchCommand", "agent-bus");
  const delegationTaskTimeoutMs = config.get<number>("delegationTaskTimeoutMs", 1800000);

  // ── Resolve effective bridge auth ──────────────────────────
  const authResolution = await resolveAuth(
    configuredToken,
    insecureMode,
    context.secrets,
  );
  const effectiveToken = authResolution.effectiveToken;

  const outputChannel = vscode.window.createOutputChannel("Antigravity Companion");
  context.subscriptions.push(outputChannel);
  outputChannel.appendLine("[companion] Activating Antigravity Companion Extension...");
  outputChannel.appendLine(`[companion] Bridge auth mode: ${authResolution.mode}`);

  const earlyWorkspacePaths = vscode.workspace.workspaceFolders?.map((folder) => folder.uri.fsPath) ?? [];
  const wsSlug = crypto.createHash("sha256").update(earlyWorkspacePaths.sort().join("\n")).digest("hex").slice(0, 8);
  const agpairDir = path.join(os.homedir(), ".agpair");
  delegationTracker = new DelegationTaskTracker(
    path.join(agpairDir, `delegation_tasks_code_${wsSlug}.json`),
  );

  // ── Step 1: Initialize SDK ──────────────────────────────────
  sdk = new AntigravitySDK(context, { debug: false });
  let sdkReady = false;

  try {
    await sdk.initialize();
    sdkReady = true;
    outputChannel.appendLine("[companion] AntigravitySDK initialized successfully.");
  } catch (err: any) {
    outputChannel.appendLine(
      `[companion] SDK initialization failed: ${err.message}. ` +
      `Bridge will run in degraded mode (sdk_initialized=false).`
    );
    // Don't throw — the bridge can still run in skeleton/degraded mode.
  }

  // ── Step 2: Initialize LS bridge ────────────────────────────
  if (sdkReady) {
    try {
      const lsReady = await sdk.ls.initialize();
      if (lsReady) {
        outputChannel.appendLine(
          `[companion] LS bridge ready (port=${sdk.ls.port}, csrf=${sdk.ls.hasCsrfToken ? "yes" : "no"}).`
        );
      } else {
        outputChannel.appendLine("[companion] LS bridge discovery failed. Using command-based fallback.");
      }
    } catch (err: any) {
      outputChannel.appendLine(`[companion] LS bridge init error: ${err.message}`);
    }
  }

  // ── Step 3: Create controllers and services ─────────────────
  const sessionCtrl = sdkReady
    ? new SessionController(sdk!)
    : null;

  monitorCtrl = sdkReady
    ? new MonitorController(sdk!, eventStore, sessionStore, delegationTracker)
    : null;

  // TaskExecutionService: uses real SDK if available, otherwise falls back to skeleton
  const taskExecService = sessionCtrl
    ? new TaskExecutionService(sessionCtrl, sessionStore, eventStore)
    : createSkeletonTaskExecService(sessionStore, eventStore);
  agentBusDelegationService = new AgentBusDelegationService({
    enabled: true,
    command: agentBusWatchCommand,
    workspacePathsProvider: () => vscode.workspace.workspaceFolders?.map((folder) => folder.uri.fsPath) ?? [],
    outputChannel,
    sessionCtrl: (sessionCtrl ?? {
      async createBackgroundSession() {
        return { ok: false, session_id: "", error: "Antigravity SDK not initialized" };
      },
    }) as SessionController,
    tracker: delegationTracker,
    staleAfterMs: delegationTaskTimeoutMs,
  });

  const extensionVersion =
    typeof context.extension?.packageJSON?.version === "string"
      ? context.extension.packageJSON.version
      : "0.0.0";
  agentBusWatchService = new AgentBusWatchService({
    enabled: agentBusWatchEnabled,
    command: agentBusWatchCommand,
    intervalMs: agentBusWatchInterval,
    workspacePathsProvider: () => vscode.workspace.workspaceFolders?.map((folder) => folder.uri.fsPath) ?? [],
    outputChannel,
    notify: (message: string) => {
      void vscode.window.showInformationMessage(message);
    },
    onMessages: (messages) => agentBusDelegationService?.handleMessages(messages) ?? Promise.resolve(),
    lockPath: path.join(agpairDir, `agent_bus_watch_code_${wsSlug}.lock.json`),
    inboxPath: path.join(agpairDir, `agent_bus_inbox_code_${wsSlug}.jsonl`),
  });
  const healthService = new HealthService(
    sdk,
    monitorCtrl,
    sessionStore,
    extensionVersion,
    {
      id: typeof context.extension?.id === "string" ? context.extension.id : null,
      path: typeof context.extension?.extensionPath === "string" ? context.extension.extensionPath : null,
    },
    () => vscode.workspace.workspaceFolders?.map((folder) => folder.uri.fsPath) ?? [],
    () => agentBusWatchService?.getStatus() ?? { running: false, mode: "disabled", pid: null },
    () => agentBusDelegationService?.getDelegationStatus() ?? {
      enabled: false,
      receipt_watcher_running: false,
      heartbeat_running: false,
      heartbeat_interval_ms: 0,
      receipt_dir: "",
      tracker_summary: { total: 0, pending: 0, completed: 0, tasks: [] },
    },
    () => delegationTaskTimeoutMs,
  );

  // ── Step 4: Start bridge HTTP server ────────────────────────
  const bridgeConfig: BridgeConfig = {
    port,
    authToken: effectiveToken,
    authMode: authResolution.mode,
    taskExecService,
    healthService,
    sessionStore,
    eventStore,
    delegationTracker: delegationTracker ?? undefined,
    onTaskDispatched: (repoPath: string) => {
      if (activeBridgePort > 0) {
        writeWorkspaceBridgeMarker(repoPath, activeBridgePort);
        writeWorkspaceBridgeAuthTokenMarker(repoPath, effectiveToken);
      }
    },
  };

  try {
    const { server, actualPort } = await startBridgeServer(bridgeConfig);
    bridgeServer = server;
    activeBridgePort = actualPort;
    healthService.setBridgePort(actualPort);
    healthService.setBridgeAuthMode(authResolution.mode);
    taskExecService.setBridgeContext({ port: actualPort, authToken: effectiveToken });
    if (actualPort !== port) {
      outputChannel.appendLine(
        `[companion] ⚠ Port ${port} was in use. Bridge bound to fallback port ${actualPort}.`
      );
    }
    outputChannel.appendLine(`[companion] Bridge server started on http://127.0.0.1:${actualPort}`);

    // Write port marker file for external discovery
    writeBridgePortMarker(actualPort);
    writeBridgeAuthTokenMarker(effectiveToken);

    // If folders are added/removed after activation, refresh workspace-scoped
    // markers so repo-aware routing does not fall back to the global marker.
    context.subscriptions.push(
      vscode.workspace.onDidChangeWorkspaceFolders(() => {
        if (activeBridgePort > 0) {
          writeBridgePortMarker(activeBridgePort);
          writeBridgeAuthTokenMarker(effectiveToken);
          outputChannel.appendLine("[companion] Workspace folders changed; refreshed bridge markers.");
        }
      }),
    );
  } catch (err: any) {
    outputChannel.appendLine(
      `[companion] ❌ Bridge failed to start: ${err.message}. ` +
      `Extension is running but NOT serving HTTP. Close other Antigravity windows and reload.`
    );
  }

  // ── Step 5: Start monitor ───────────────────────────────────
  if (monitorCtrl) {
    monitorCtrl.start(monitorInterval, trajectoryInterval);
    outputChannel.appendLine(
      `[companion] Monitor started (uss=${monitorInterval}ms, traj=${trajectoryInterval}ms).`
    );
  }

  agentBusWatchService.start();

  // Register dispose
  context.subscriptions.push({
    dispose: () => {
      agentBusDelegationService?.dispose();
      agentBusDelegationService = null;
      delegationTracker = null;
      agentBusWatchService?.dispose();
      agentBusWatchService = null;
      monitorCtrl?.dispose();
      sdk?.dispose();
      if (bridgeServer) {
        bridgeServer.close();
        bridgeServer = null;
      }
      removeBridgeMarkers();
    },
  });

  outputChannel.appendLine("[companion] Activation complete.");
}

/**
 * Extension deactivation — called by the host on shutdown/disable.
 */
export function deactivate(): void {
  agentBusDelegationService?.dispose();
  agentBusDelegationService = null;
  delegationTracker = null;
  agentBusWatchService?.dispose();
  agentBusWatchService = null;

  monitorCtrl?.dispose();
  monitorCtrl = null;

  sdk?.dispose();
  sdk = null;

  if (bridgeServer) {
    bridgeServer.close();
    bridgeServer = null;
  }

  removeBridgeMarkers();
  console.log("[companion] Deactivated.");
}

// ── Port marker helpers ─────────────────────────────────────────

/** Track all marker paths written so we can clean them up on deactivate. */
const writtenMarkerPaths: string[] = [];

/**
 * Write a discovery marker to a single directory.
 * @returns true if written successfully.
 */
function writeDiscoveryMarker(dir: string, markerName: string, value: string, mode?: number): boolean {
  return writeMarkerToDir({
    dir,
    markerName,
    value,
    writtenPaths: writtenMarkerPaths,
    mode,
  });
}

/**
 * Write the actual bridge port to well-known files so the supervisor CLI
 * (or other external tooling) can discover which port to connect to.
 *
 * Writes to:
 *   1. ~/.agpair/bridge_port (global fallback)
 *   2. {workspaceFolder}/.agpair/bridge_port (per workspace)
 */
function writeBridgePortMarker(port: number): void {
  // Global marker
  const homeDir = os.homedir();
  if (writeDiscoveryMarker(homeDir, BRIDGE_PORT_MARKER, String(port))) {
    console.log(`[companion] Wrote global port marker: ${path.join(homeDir, ".agpair", BRIDGE_PORT_MARKER)} = ${port}`);
  } else {
    console.warn("[companion] Failed to write global port marker.");
  }

  // Workspace-scoped markers
  const folders = vscode.workspace.workspaceFolders;
  if (folders) {
    for (const folder of folders) {
      const wsPath = folder.uri.fsPath;
      if (writeDiscoveryMarker(wsPath, BRIDGE_PORT_MARKER, String(port))) {
        console.log(`[companion] Wrote workspace port marker: ${wsPath}/.agpair/bridge_port = ${port}`);
      }
    }
  }
}

function writeBridgeAuthTokenMarker(authToken: string): void {
  if (!authToken) {
    return;
  }
  const homeDir = os.homedir();
  if (writeDiscoveryMarker(homeDir, BRIDGE_AUTH_TOKEN_MARKER, authToken, BRIDGE_AUTH_TOKEN_MODE)) {
    console.log(
      `[companion] Wrote global auth marker: ${path.join(homeDir, ".agpair", BRIDGE_AUTH_TOKEN_MARKER)}`
    );
  } else {
    console.warn("[companion] Failed to write global auth marker.");
  }

  const folders = vscode.workspace.workspaceFolders;
  if (folders) {
    for (const folder of folders) {
      const wsPath = folder.uri.fsPath;
      if (writeDiscoveryMarker(wsPath, BRIDGE_AUTH_TOKEN_MARKER, authToken, BRIDGE_AUTH_TOKEN_MODE)) {
        console.log(`[companion] Wrote workspace auth marker: ${wsPath}/.agpair/${BRIDGE_AUTH_TOKEN_MARKER}`);
      }
    }
  }
}

/**
 * Write a workspace-scoped bridge_port marker for a specific repo_path.
 * Called from TaskExecutionService on /run_task to ensure the task's
 * workspace has a marker even if it's not a VS Code workspace folder.
 */
export function writeWorkspaceBridgeMarker(repoPath: string, port: number): void {
  if (!repoPath) return;
  if (writeDiscoveryMarker(repoPath, BRIDGE_PORT_MARKER, String(port))) {
    console.log(`[companion] Wrote repo port marker: ${repoPath}/.agpair/bridge_port = ${port}`);
  }
}

function writeWorkspaceBridgeAuthTokenMarker(repoPath: string, authToken: string): void {
  if (!repoPath || !authToken) return;
  if (writeDiscoveryMarker(repoPath, BRIDGE_AUTH_TOKEN_MARKER, authToken, BRIDGE_AUTH_TOKEN_MODE)) {
    console.log(`[companion] Wrote repo auth marker: ${repoPath}/.agpair/${BRIDGE_AUTH_TOKEN_MARKER}`);
  }
}

/** Remove all written discovery marker files on deactivation. */
function removeBridgeMarkers(): void {
  removeWrittenMarkers(writtenMarkerPaths);
}

// ── Skeleton fallback for when SDK is unavailable ───────────────

/**
 * Creates a skeleton TaskExecutionService that produces ACK-only responses
 * without a real SDK connection. Used when SDK init fails (degraded mode).
 *
 * /health will correctly report sdk_initialized=false in this mode.
 */
function createSkeletonTaskExecService(
  sessionStore: TaskSessionStore,
  eventStore: PendingEventStore,
): TaskExecutionService {
  // Create a dummy session controller that just generates local session IDs
  const dummyCtrl = {
    async createBackgroundSession(prompt: string) {
      const sessionId = `ag-skeleton-${Date.now()}`;
      return { ok: true, session_id: sessionId };
    },
    async focusSession() { return false; },
    async sendPrompt() { return { ok: false, error: "SDK not initialized" }; },
    async sessionExists() { return false; },
  } as any;

  return new TaskExecutionService(dummyCtrl, sessionStore, eventStore);
}

// Re-export for programmatic use
export { TaskSessionStore } from "./state/taskSessionStore";
export { PendingEventStore } from "./state/pendingEventStore";
export { startBridgeServer, createBridgeServer } from "./bridge/httpServer";
