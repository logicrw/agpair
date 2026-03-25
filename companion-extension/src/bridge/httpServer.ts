/**
 * Antigravity Companion Extension — Bridge HTTP Server.
 *
 * Thin HTTP layer that delegates to services:
 *   POST /run_task       → TaskExecutionService.runTask
 *   POST /continue_task  → TaskExecutionService.continueTask
 *   POST /cancel_task    → sessionStore.remove
 *   POST /events/ack     → eventStore.markDeliveredByIds
 *   POST /wake           → wake signal (future: trigger IDE focus)
 *   GET  /task_status    → eventStore.getPending + sessionStore.get
 *   GET  /health         → HealthService.getHealth
 *
 * Uses Node.js built-in http module — no Express dependency.
 * Preserves the frozen bridge contract shape.
 *
 * Spec: codex_antigravity_companion_extension_ts_spec.md §12
 */

import * as http from "http";
import * as crypto from "crypto";
import { TaskSessionStore } from "../state/taskSessionStore";
import { PendingEventStore, PendingEvent } from "../state/pendingEventStore";
import { TaskExecutionService } from "../services/taskExecutionService";
import { HealthService } from "../services/healthService";
import { MonitorController } from "../sdk/monitorController";
import type { BridgeAuthMode } from "./authResolver";

export interface BridgeConfig {
  port: number;
  /**
   * Effective auth token for the bridge.
   *
   * - Non-empty string: mutating routes require `Authorization: Bearer <token>`.
   * - Empty string: auth is disabled (insecure mode).
   *
   * /health and /task_status are always readable without auth so that
   * `agpair doctor` keeps working out of the box.
   */
  authToken: string;
  /** Auth mode for metadata reporting (never includes the token value). */
  authMode?: BridgeAuthMode;
  taskExecService: TaskExecutionService;
  healthService: HealthService;
  sessionStore: TaskSessionStore;
  eventStore: PendingEventStore;
  /** Called after a task is dispatched, with the repo_path. */
  onTaskDispatched?: (repoPath: string) => void;
}

/**
 * Maximum allowed request body size in bytes (1 MiB).
 *
 * POST requests exceeding this limit are rejected with 413 Payload Too Large
 * to prevent unbounded memory accumulation from oversized or malicious payloads.
 */
export const MAX_BODY_BYTES = 1 * 1024 * 1024; // 1 MiB

// ── Auth helper ─────────────────────────────────────────────────

/**
 * Constant-time bearer-token comparison.
 *
 * When the expected token is empty, auth is disabled and the function
 * returns true for any request. This only happens when the user
 * explicitly opts into insecure mode — in the default path a random
 * token is always generated.
 */
export function checkAuth(req: http.IncomingMessage, token: string): boolean {
  if (!token) {
    // Auth disabled — insecure/no-auth mode.
    return true;
  }
  const header = req.headers["authorization"] || "";
  const supplied = header.startsWith("Bearer ") ? header.slice(7) : "";
  return constantTimeEqual(supplied, token);
}

/**
 * Set of mutating endpoints that require auth when a token is configured.
 * Read-only routes (/health, /task_status, 404) are always accessible.
 */
const MUTATING_PATHS = new Set([
  "/run_task",
  "/continue_task",
  "/cancel_task",
  "/events/ack",
  "/write_receipt",
  "/wake",
]);

/**
 * Compare two strings in constant time to prevent timing side-channel attacks.
 * Uses crypto.timingSafeEqual when both strings are non-empty.
 * Returns false immediately only when one or both are empty (no secret leakage).
 */
export function constantTimeEqual(a: string, b: string): boolean {
  if (a.length === 0 || b.length === 0) return false;
  const bufA = Buffer.from(a, "utf-8");
  const bufB = Buffer.from(b, "utf-8");
  if (bufA.length !== bufB.length) {
    // Prevent length-oracle: compare bufA against itself so timing is constant,
    // but always return false.
    crypto.timingSafeEqual(bufA, bufA);
    return false;
  }
  return crypto.timingSafeEqual(bufA, bufB);
}

// ── Helpers ─────────────────────────────────────────────────────
function sendJson(res: http.ServerResponse, code: number, data: any): void {
  res.writeHead(code, { "Content-Type": "application/json" });
  res.end(JSON.stringify(data));
}

/**
 * Read the request body with an enforced size limit.
 *
 * @param req    Incoming HTTP request
 * @param limit  Maximum body size in bytes (default: MAX_BODY_BYTES)
 * @returns      The body string
 * @throws       Error with `statusCode = 413` when the limit is exceeded
 */
function readBody(req: http.IncomingMessage, limit: number = MAX_BODY_BYTES): Promise<string> {
  return new Promise((resolve, reject) => {
    let data = "";
    let bytes = 0;
    let exceeded = false;
    req.on("data", (chunk: Buffer | string) => {
      if (exceeded) return; // drain remaining chunks silently
      const chunkBytes = typeof chunk === "string" ? Buffer.byteLength(chunk) : chunk.length;
      bytes += chunkBytes;
      if (bytes > limit) {
        exceeded = true;
        // Do NOT destroy the socket — we need it alive to send 413 back.
        // Just stop accumulating data; remaining chunks are silently drained.
        return;
      }
      data += chunk;
    });
    req.on("end", () => {
      if (exceeded) {
        const err: any = new Error(
          `Request body exceeds maximum size of ${limit} bytes`
        );
        err.statusCode = 413;
        reject(err);
      } else {
        resolve(data);
      }
    });
    req.on("error", reject);
  });
}

function makeStructuredReceiptText(body: {
  status: string;
  task_id: string;
  attempt_no: number;
  review_round: number;
  summary: string;
}): string {
  return [
    `STATUS: ${body.status}`,
    `TASK_ID: ${body.task_id}`,
    `ATTEMPT_NO: ${body.attempt_no}`,
    `REVIEW_ROUND: ${body.review_round}`,
    `SUMMARY: ${body.summary}`,
  ].join("\n");
}

// ── Server factory ──────────────────────────────────────────────
export function createBridgeServer(config: BridgeConfig): http.Server {
  const { authToken, taskExecService, healthService, sessionStore, eventStore } = config;

  return http.createServer(async (req, res) => {
    const url = new URL(req.url || "/", `http://127.0.0.1:${config.port}`);
    const routePath = url.pathname;

    // Auth gate: only enforce on mutating routes.
    // /health and /task_status remain readable without auth so that
    // `agpair doctor` and other probes keep working zero-config.
    if (MUTATING_PATHS.has(routePath) && !checkAuth(req, authToken)) {
      sendJson(res, 401, { ok: false, message: "Unauthorized" });
      return;
    }

    const path = routePath;
    const method = req.method?.toUpperCase();

    try {
      if (method === "POST" && path === "/run_task") {
        const body = JSON.parse(await readBody(req));
        const result = await taskExecService.runTask(body);
        if (result.ok && body.repo_path && config.onTaskDispatched) {
          try { config.onTaskDispatched(body.repo_path); } catch { /* best-effort */ }
        }
        sendJson(res, result.ok ? 200 : 500, result);

      } else if (method === "POST" && path === "/continue_task") {
        const body = JSON.parse(await readBody(req));
        const result = await taskExecService.continueTask(body);
        sendJson(res, result.ok ? 200 : 409, result);

      } else if (method === "POST" && path === "/cancel_task") {
        const body = JSON.parse(await readBody(req));
        sessionStore.remove(body.task_id, body.attempt_no);
        sendJson(res, 200, { ok: true, task_id: body.task_id, message: "cancelled" });

      } else if (method === "POST" && path === "/events/ack") {
        const body = JSON.parse(await readBody(req));
        if (!body.task_id || !Array.isArray(body.source_event_ids)) {
          sendJson(res, 400, { ok: false, message: "task_id and source_event_ids[] required" });
          return;
        }
        const acked = eventStore.markDeliveredByIds(body.task_id, body.source_event_ids);
        sendJson(res, 200, { ok: true, task_id: body.task_id, acked_count: acked });

      } else if (method === "POST" && path === "/write_receipt") {
        const body = JSON.parse(await readBody(req));
        if (!body.task_id || body.attempt_no === undefined || body.review_round === undefined ||
            !body.status || !body.summary) {
          sendJson(res, 400, {
            ok: false,
            message: "task_id, attempt_no, review_round, status, and summary are required",
          });
          return;
        }
        const validStatuses = ["EVIDENCE_PACK", "BLOCKED", "COMMITTED", "FAILED"];
        if (!validStatuses.includes(body.status)) {
          sendJson(res, 400, {
            ok: false,
            message: `status must be one of: ${validStatuses.join(", ")}`,
          });
          return;
        }
        // Validate task_id
        const idErr = TaskExecutionService.validateTaskId(body.task_id);
        if (idErr) {
          sendJson(res, 400, { ok: false, message: idErr });
          return;
        }
        // Derive repo_path ONLY from bound session — do not accept caller-supplied path
        const sess = sessionStore.get(body.task_id, body.attempt_no);
        if (!sess || !sess.repo_path) {
          sendJson(res, 409, {
            ok: false,
            message: "No bound session for this task/attempt. Cannot determine receipt path.",
          });
          return;
        }
        const repoPath = sess.repo_path;
        const receiptPath = MonitorController.outputFilePath(
          repoPath, body.task_id, body.attempt_no, body.review_round
        );
        try {
          const fs = require("fs");
          MonitorController.ensureReceiptDir(repoPath);
          const rawText = makeStructuredReceiptText(body);
          const receipt = JSON.stringify({
            status: body.status,
            task_id: body.task_id,
            attempt_no: body.attempt_no,
            review_round: body.review_round,
            summary: body.summary,
          });
          fs.writeFileSync(receiptPath, receipt, "utf-8");

          // Emit the terminal event immediately so supervisor polling does not
          // depend on a later step-count change to discover the written receipt.
          const now = new Date().toISOString();
          const seq = (sess.last_step_count || 0) + 1000;
          sess.last_known_status = body.status;
          const terminalEvt: PendingEvent = {
            source_event_id: `${sess.session_id}:evt:${seq}`,
            task_id: body.task_id,
            attempt_no: body.attempt_no,
            review_round: body.review_round,
            session_id: sess.session_id,
            source_seq: seq,
            status: body.status,
            payload: {
              summary: body.summary,
              raw_text: rawText,
              step_count: sess.last_step_count || 0,
            },
            emitted_at: now,
            delivered_at: null,
          };
          eventStore.push(body.task_id, terminalEvt);

          console.log(`[bridge] Wrote receipt: ${receiptPath}`);
          sendJson(res, 200, {
            ok: true, task_id: body.task_id,
            receipt_path: receiptPath,
            message: "receipt written",
          });
        } catch (err: any) {
          console.error(`[bridge] Failed to write receipt:`, err);
          sendJson(res, 500, {
            ok: false,
            message: "Failed to write receipt",
          });
        }

      } else if (method === "POST" && path === "/wake") {
        const body = JSON.parse(await readBody(req));
        if (!body.wake_id || !body.task_id || !body.reason) {
          sendJson(res, 400, { ok: false, message: "wake_id, task_id, and reason required" });
          return;
        }
        console.log(`[wake] wake_id=${body.wake_id} task=${body.task_id} reason=${body.reason}`);
        sendJson(res, 200, {
          ok: true, wake_id: body.wake_id, task_id: body.task_id,
          reason: body.reason, message: "wake acknowledged",
        });

      } else if (method === "GET" && path === "/task_status") {
        const task_id = url.searchParams.get("task_id") || "";
        const attempt_no = parseInt(url.searchParams.get("attempt_no") || "0", 10);
        const session = sessionStore.get(task_id, attempt_no);
        const pending = eventStore.getPending(task_id);

        sendJson(res, 200, {
          ok: true, task_id, attempt_no,
          session_id: session?.session_id ?? null,
          session_state: session?.last_known_status ?? "unknown",
          last_step_count: session?.last_step_count ?? 0,
          last_heartbeat_at: session?.last_heartbeat_at ?? null,
          pending_events: pending,
        });

      } else if (method === "GET" && path === "/health") {
        sendJson(res, 200, healthService.getHealth());

      } else {
        sendJson(res, 404, { ok: false, message: "Not found" });
      }
    } catch (err: any) {
      if (err.statusCode === 413) {
        sendJson(res, 413, { ok: false, message: "Request body too large" });
        return;
      }
      if (err instanceof SyntaxError && err.message.includes("JSON")) {
        sendJson(res, 400, { ok: false, message: "Invalid JSON in request body" });
        return;
      }
      console.error(`[bridge] Unhandled request error:`, err);
      sendJson(res, 500, { ok: false, message: "Internal server error" });
    }
  });
}

/** Maximum port-collision retries before giving up. */
const MAX_PORT_RETRIES = 5;

export interface BridgeStartResult {
  server: http.Server;
  actualPort: number;
}

/**
 * Start the Bridge HTTP server with EADDRINUSE collision detection.
 *
 * If the configured port is already in use (another Antigravity window),
 * retries on consecutive ports (port+1, port+2, ...) up to MAX_PORT_RETRIES.
 *
 * @returns Promise with the server and the actual bound port.
 * @throws Error if all ports are in use.
 */
export async function startBridgeServer(config: BridgeConfig): Promise<BridgeStartResult> {
  const server = createBridgeServer(config);
  let lastError: Error | null = null;

  for (let attempt = 0; attempt < MAX_PORT_RETRIES; attempt++) {
    const port = config.port + attempt;
    try {
      await listenOnPort(server, port);
      if (attempt > 0) {
        console.warn(
          `[companion] Port ${config.port} was in use. ` +
          `Bridge bound to fallback port ${port} instead.`
        );
      }
      console.log(`[companion] Bridge listening on http://127.0.0.1:${port}`);
      return { server, actualPort: port };
    } catch (err: any) {
      if (err.code === "EADDRINUSE") {
        lastError = err;
        console.warn(`[companion] Port ${port} in use, trying next...`);
        continue;
      }
      throw err; // Non-port-collision error — fail immediately
    }
  }

  throw new Error(
    `Bridge failed to bind: ports ${config.port}-${config.port + MAX_PORT_RETRIES - 1} ` +
    `all in use. Close other Antigravity windows. Last error: ${lastError?.message}`
  );
}

/**
 * Wrap server.listen in a promise so we can catch EADDRINUSE.
 */
function listenOnPort(server: http.Server, port: number): Promise<void> {
  return new Promise((resolve, reject) => {
    const onError = (err: Error) => {
      server.removeListener("listening", onListening);
      reject(err);
    };
    const onListening = () => {
      server.removeListener("error", onError);
      resolve();
    };
    server.once("error", onError);
    server.once("listening", onListening);
    server.listen(port, "127.0.0.1");
  });
}
