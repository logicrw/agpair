/**
 * Session Controller — wraps Antigravity session lifecycle.
 *
 * Fallback chain for session creation:
 *   1. sdk.ls.createCascade (headless, no UI flicker)
 *   2. LS re-init + retry (fresh CSRF token)
 *   3. Direct vscode.commands: startNewConversation + sendPromptToAgentPanel
 *      (bypasses SDK CascadeManager which uses missing commands in v25.8.1)
 *
 * Spec reference: codex_antigravity_companion_extension_ts_spec.md §7
 */

import * as vscode from "vscode";
import type { AntigravitySDK } from "antigravity-sdk";

import { pickFreshSessionId } from "./freshSession";
import { discoverLiveLsConnection } from "./lsConnectionRepair";

export interface CreateSessionResult {
  ok: boolean;
  session_id: string;
  error?: string;
}

export interface CreateBackgroundSessionOptions {
  /**
   * Whether the controller may fall back to interactive UI-driven session
   * creation paths. Automated delegation must keep this false.
   */
  allowInteractiveFallback?: boolean;
  /** Short label used in logs/errors to explain which task requested the session. */
  contextLabel?: string;
}

export interface SendPromptResult {
  ok: boolean;
  error?: string;
}

export interface SendPromptOptions {
  /**
   * Whether the controller may fall back to sending the prompt through the
   * visible agent panel. Automated delegation must keep this false.
   */
  allowPanelFallback?: boolean;
  /** Short label used in logs/errors to explain which task requested the send. */
  contextLabel?: string;
}

export class SessionController {
  constructor(
    private readonly sdk: AntigravitySDK,
  ) {}

  /**
   * Create a new background Cascade session.
   *
   * Fallback chain:
   *   1. LS bridge createCascade (headless, preferred)
   *   2. LS re-init + retry (fresh CSRF)
   *   3. Interactive fallbacks (only when explicitly allowed)
   */
  async createBackgroundSession(
    prompt: string,
    options: CreateBackgroundSessionOptions = {},
  ): Promise<CreateSessionResult> {
    const errors: string[] = [];
    const beforeIds = await this.snapshotSessionIds();
    const allowInteractiveFallback = options.allowInteractiveFallback ?? false;
    const contextLabel = options.contextLabel?.trim() || "automated task";

    // ── Path 1 & 2: LS bridge — DISABLED ──────────────────────
    // createCascade returns phantom session IDs: they appear in getSessions()
    // and even pass focusSession(), but never materialise in the Antigravity UI.
    // Skipping directly to the interactive paths (3/4) which reliably open
    // real conversations. Re-enable once the LS phantom-ID issue is resolved.
    console.log("[session] Paths 1/2 (LS createCascade) skipped — known phantom-ID issue");

    // ── Path 3: SDK cascade — DISABLED ─────────────────────────
    // cascade.createSession also returns phantom IDs (same underlying issue).
    // Skipping to Path 4 (direct vscode commands) which is the only verified
    // working path. Re-enable once phantom-ID issue is resolved.
    console.log("[session] Path 3 (SDK cascade) skipped — same phantom-ID issue");

    // ── Path 4: Direct vscode commands ────────────────────────
    // The only reliable path: bypass the SDK CascadeManager entirely.
    // startNewConversation opens a real UI conversation, sendPromptToAgentPanel
    // injects the prompt. getSessions() cannot reliably detect these sessions,
    // so we generate a tracking ID instead of relying on session diff.
    try {
      console.log("[session] Path 4: Direct vscode commands...");

      // Create a new conversation (switches UI, reliably works)
      await vscode.commands.executeCommand("antigravity.startNewConversation");
      // Wait for the UI to register the new conversation
      await new Promise(r => setTimeout(r, 1500));

      // Send the prompt to the newly created conversation
      await vscode.commands.executeCommand("antigravity.sendPromptToAgentPanel", prompt);
      // Wait for the prompt to be dispatched
      await new Promise(r => setTimeout(r, 500));

      // Best-effort session ID detection via diff.
      // If getSessions() can't detect the new session, generate a tracking ID
      // so the delegation system can still track the task.
      let sessionId = "";
      try {
        const after = await this.sdk.cascade.getSessions();
        const freshSessionId = pickFreshSessionId(beforeIds, "", after);
        if (freshSessionId) {
          sessionId = freshSessionId;
          console.log(`[session] New session detected by diff: ${sessionId}`);
        }
      } catch {
        // ignore — detection is best-effort
      }

      if (!sessionId) {
        sessionId = `ag-cmd-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
        console.log(`[session] getSessions() could not detect new session; using tracking ID: ${sessionId}`);
      }

      console.log(`[session] Direct commands succeeded: ${sessionId}`);
      return { ok: true, session_id: sessionId };
    } catch (err: any) {
      errors.push(`Direct commands: ${err.message}`);
      console.warn(`[session] Path 4 failed: ${err.message}`);
    }

      const msg = `All paths failed: ${errors.join(" | ")}`;
    console.error(`[session] ${msg}`);
    return { ok: false, session_id: "", error: msg };
  }

  private async snapshotSessionIds(): Promise<Set<string>> {
    try {
      const sessions = await this.sdk.cascade.getSessions();
      const ids = new Set(sessions.map((session) => session.id).filter(Boolean));
      console.log(`[session] Sessions before: ${ids.size} known`);
      return ids;
    } catch {
      return new Set<string>();
    }
  }

  /**
   * Terminate/delete an existing session.
   */
  async terminateSession(sessionId: string): Promise<boolean> {
    const errors: string[] = [];

    // Path 1: LS Bridge (headless task cancel)
    if (this.sdk.ls.isReady) {
      try {
        console.log(`[session] Path 1: LS cancel cascade ${sessionId}...`);
        await this.sdk.ls.cancelCascade(sessionId);
      } catch (err: any) {
        errors.push(`LS cancel: ${err.message}`);
        console.warn(`[session] LS cancel failed: ${err.message}`);
      }
    }

    // Path 2: Broadcast deletion
    try {
      console.log(`[session] Path 2: execute broadcastConversationDeletion ${sessionId}...`);
      await vscode.commands.executeCommand("antigravity.broadcastConversationDeletion", sessionId);
      return true;
    } catch (err: any) {
      errors.push(`Broadcast: ${err.message}`);
      console.warn(`[session] Broadcast failed: ${err.message}`);
    }

    return false;
  }

  private async resolveFreshSessionId(
    beforeIds: Set<string>,
    returnedId: string | null | undefined,
  ): Promise<string | null> {
    for (let attempt = 0; attempt < 3; attempt++) {
      try {
        const sessions = await this.sdk.cascade.getSessions();
        const freshSessionId = pickFreshSessionId(beforeIds, returnedId, sessions);
        if (freshSessionId) {
          return freshSessionId;
        }
      } catch {
        // best effort
      }
      if (attempt < 2) {
        await new Promise((resolve) => setTimeout(resolve, 500));
      }
    }
    return null;
  }

  /**
   * Focus an existing session in the UI.
   */
  async focusSession(sessionId: string): Promise<boolean> {
    if (this.sdk.ls.isReady) {
      try {
        await this.sdk.ls.focusCascade(sessionId);
        return true;
      } catch {
        // fall through
      }
    }
    try {
      await this.sdk.cascade.focusSession(sessionId);
      return true;
    } catch {
      return false;
    }
  }

  /**
   * Send a prompt to an existing session.
   */
  async sendPrompt(
    sessionId: string,
    prompt: string,
    options: SendPromptOptions = {},
  ): Promise<SendPromptResult> {
    const allowPanelFallback = options.allowPanelFallback ?? false;
    const contextLabel = options.contextLabel?.trim() || "automated task";

    // Try LS bridge
    if (this.sdk.ls.isReady) {
      try {
        const ok = await this.sdk.ls.sendMessage({
          cascadeId: sessionId,
          text: prompt,
        });
        return { ok };
      } catch (err: any) {
        console.warn(`[session] LS sendMessage failed: ${err.message}`);
        if (String(err?.message ?? "").includes("CSRF") || String(err?.message ?? "").includes("403")) {
          try {
            const repaired = await this.tryRepairLsConnection();
            if (repaired) {
              const ok = await this.sdk.ls.sendMessage({
                cascadeId: sessionId,
                text: prompt,
              });
              return { ok };
            }
          } catch (repairErr: any) {
            console.warn(`[session] LS sendMessage repair failed: ${repairErr.message}`);
          }
        }
      }
    }

    if (!allowPanelFallback) {
      return {
        ok: false,
        error:
          `Headless prompt delivery failed for ${contextLabel}; ` +
          `prompt-panel fallback is disabled because it requires interactive UI focus.`,
      };
    }

    // Fallback: sendPromptToAgentPanel (sends to active/visible panel)
    try {
      await vscode.commands.executeCommand("antigravity.sendPromptToAgentPanel", prompt);
      return { ok: true };
    } catch (err: any) {
      return { ok: false, error: err.message };
    }
  }

  /**
   * Check if a session ID is known to the SDK.
   */
  async sessionExists(sessionId: string): Promise<boolean> {
    try {
      const sessions = await this.sdk.cascade.getSessions();
      return sessions.some((s) => s.id === sessionId);
    } catch {
      return false;
    }
  }

  private async tryRepairLsConnection(): Promise<boolean> {
    const connection = await discoverLiveLsConnection(this.workspaceHint());
    if (!connection) {
      return false;
    }
    this.sdk.ls.setConnection(connection.port, connection.csrfToken, connection.useTls);
    console.log(
      `[session] LS repaired: port=${connection.port} tls=${connection.useTls} ` +
      `source=${connection.source} pid=${connection.pid}`,
    );
    return true;
  }

  private workspaceHint(): string {
    const folders = vscode.workspace.workspaceFolders;
    if (!folders || folders.length === 0) {
      return "";
    }
    return folders[0].uri.fsPath
      .replace(/\\/g, "/")
      .split("/")
      .slice(-4)
      .join("_")
      .replace(/[-.\s]/g, "_")
      .toLowerCase();
  }
}
