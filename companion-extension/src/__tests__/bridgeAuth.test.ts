/**
 * Tests for bridge auth resolver and secure-by-default behavior.
 *
 * Covers:
 *   - Generated-token default path protects mutating routes but leaves /health readable
 *   - Explicit configured token overrides generated token
 *   - Explicit insecure/no-auth mode allows unauthenticated mutating requests
 *   - /write_receipt prompt wiring uses the effective token
 *   - No secret leaks in /health payload
 *   - Auth resolver produces correct modes for each configuration
 */

import { describe, it, afterEach } from "node:test";
import * as assert from "node:assert/strict";
import * as http from "node:http";
import { createBridgeServer, checkAuth } from "../bridge/httpServer";
import { resolveAuth, SECRET_STORAGE_KEY } from "../bridge/authResolver";
import type { BridgeAuthMode, SecretStorage } from "../bridge/authResolver";

// ── In-memory SecretStorage stub ────────────────────────────────

class InMemorySecretStorage implements SecretStorage {
  private _data = new Map<string, string>();

  async get(key: string): Promise<string | undefined> {
    return this._data.get(key);
  }

  async store(key: string, value: string): Promise<void> {
    this._data.set(key, value);
  }

  /** Test helper: read the stored value directly. */
  peek(key: string): string | undefined {
    return this._data.get(key);
  }
}

// ── Auth resolver unit tests ────────────────────────────────────

describe("resolveAuth", () => {
  it("uses configured token when provided", async () => {
    const secrets = new InMemorySecretStorage();
    const result = await resolveAuth("my-configured-token", false, secrets);
    assert.equal(result.mode, "configured");
    assert.equal(result.effectiveToken, "my-configured-token");
    // Should NOT generate a token in secret storage
    assert.equal(secrets.peek(SECRET_STORAGE_KEY), undefined);
  });

  it("configured token wins over insecure mode", async () => {
    const secrets = new InMemorySecretStorage();
    const result = await resolveAuth("my-token", true, secrets);
    assert.equal(result.mode, "configured");
    assert.equal(result.effectiveToken, "my-token");
  });

  it("returns insecure mode with empty token when bridgeInsecure is true", async () => {
    const secrets = new InMemorySecretStorage();
    const result = await resolveAuth("", true, secrets);
    assert.equal(result.mode, "insecure");
    assert.equal(result.effectiveToken, "");
  });

  it("generates a random token and stores in secrets on first run", async () => {
    const secrets = new InMemorySecretStorage();
    const result = await resolveAuth("", false, secrets);
    assert.equal(result.mode, "generated");
    assert.ok(
      result.effectiveToken.length > 0,
      "generated token should be non-empty",
    );
    assert.equal(
      result.effectiveToken.length,
      64,
      "should be 32 bytes = 64 hex chars",
    );
    // Verify it was persisted
    assert.equal(secrets.peek(SECRET_STORAGE_KEY), result.effectiveToken);
  });

  it("reuses previously generated token on subsequent runs", async () => {
    const secrets = new InMemorySecretStorage();
    const first = await resolveAuth("", false, secrets);
    const second = await resolveAuth("", false, secrets);
    assert.equal(first.effectiveToken, second.effectiveToken);
    assert.equal(second.mode, "generated");
  });
});

// ── Stub services ───────────────────────────────────────────────

function makeStubConfig(
  authToken: string,
  authMode: BridgeAuthMode = "generated",
) {
  return {
    port: 0,
    authToken,
    authMode,
    taskExecService: {
      runTask: async () => ({ ok: true }),
      continueTask: async () => ({ ok: true }),
    } as any,
    healthService: {
      getHealth: () => ({
        ok: true,
        status: "healthy",
        bridge_auth_mode: authMode,
        bridge_mutating_auth_required: authMode !== "insecure",
      }),
    } as any,
    sessionStore: { get: () => null, remove: () => {} } as any,
    eventStore: {
      getPending: () => [],
      markDeliveredByIds: () => 0,
      push: () => {},
    } as any,
  };
}

function listenOnRandomPort(server: http.Server): Promise<number> {
  return new Promise((resolve, reject) => {
    server.listen(0, "127.0.0.1", () => {
      const addr = server.address();
      if (addr && typeof addr === "object") {
        resolve(addr.port);
      } else {
        reject(new Error("Failed to get server address"));
      }
    });
    server.on("error", reject);
  });
}

function httpRequest(
  port: number,
  options: {
    method: string;
    path: string;
    headers?: Record<string, string>;
    body?: string;
  },
): Promise<{ status: number; body: any }> {
  return new Promise((resolve, reject) => {
    const req = http.request(
      {
        hostname: "127.0.0.1",
        port,
        path: options.path,
        method: options.method,
        headers: {
          "Content-Type": "application/json",
          ...options.headers,
        },
      },
      (res) => {
        let data = "";
        res.on("data", (chunk: string) => (data += chunk));
        res.on("end", () => {
          try {
            resolve({ status: res.statusCode || 0, body: JSON.parse(data) });
          } catch {
            resolve({ status: res.statusCode || 0, body: data });
          }
        });
      },
    );
    req.on("error", reject);
    if (options.body) req.write(options.body);
    req.end();
  });
}

// ── Secure-by-default integration tests ─────────────────────────

describe("Secure-by-default (generated token mode)", () => {
  let server: http.Server;
  let port: number;

  afterEach(() => {
    if (server) server.close();
  });

  it("GET /health succeeds WITHOUT auth header", async () => {
    server = createBridgeServer(
      makeStubConfig("generated-secret-token", "generated"),
    );
    port = await listenOnRandomPort(server);
    const res = await httpRequest(port, { method: "GET", path: "/health" });
    assert.equal(res.status, 200);
    assert.equal(res.body.ok, true);
  });

  it("POST /run_task returns 401 WITHOUT auth header", async () => {
    const token = "generated-secret-token-abc123";
    server = createBridgeServer(makeStubConfig(token, "generated"));
    port = await listenOnRandomPort(server);
    const res = await httpRequest(port, {
      method: "POST",
      path: "/run_task",
      body: JSON.stringify({
        task_id: "t1",
        attempt_no: 0,
        review_round: 0,
        prompt: "test",
        repo_path: "/tmp",
      }),
    });
    assert.equal(res.status, 401);
    assert.equal(res.body.ok, false);
  });

  it("POST /run_task succeeds WITH correct auth header", async () => {
    const token = "generated-secret-token-xyz789";
    server = createBridgeServer(makeStubConfig(token, "generated"));
    port = await listenOnRandomPort(server);
    const res = await httpRequest(port, {
      method: "POST",
      path: "/run_task",
      headers: { Authorization: `Bearer ${token}` },
      body: JSON.stringify({
        task_id: "t1",
        attempt_no: 0,
        review_round: 0,
        prompt: "test",
        repo_path: "/tmp",
      }),
    });
    assert.equal(res.status, 200);
    assert.equal(res.body.ok, true);
  });

  it("POST /wake returns 401 without auth", async () => {
    const token = "secret-wake-token";
    server = createBridgeServer(makeStubConfig(token, "generated"));
    port = await listenOnRandomPort(server);
    const res = await httpRequest(port, {
      method: "POST",
      path: "/wake",
      body: JSON.stringify({ wake_id: "w1", task_id: "t1", reason: "test" }),
    });
    assert.equal(res.status, 401);
  });

  it("POST /write_receipt returns 401 without auth", async () => {
    const token = "secret-receipt-token";
    server = createBridgeServer(makeStubConfig(token, "generated"));
    port = await listenOnRandomPort(server);
    const res = await httpRequest(port, {
      method: "POST",
      path: "/write_receipt",
      body: JSON.stringify({
        task_id: "t1",
        attempt_no: 0,
        review_round: 0,
        status: "EVIDENCE_PACK",
        summary: "done",
      }),
    });
    assert.equal(res.status, 401);
  });

  it("POST /cancel_task returns 401 without auth", async () => {
    const token = "secret-cancel-token";
    server = createBridgeServer(makeStubConfig(token, "generated"));
    port = await listenOnRandomPort(server);
    const res = await httpRequest(port, {
      method: "POST",
      path: "/cancel_task",
      body: JSON.stringify({ task_id: "t1", attempt_no: 0 }),
    });
    assert.equal(res.status, 401);
  });

  it("POST /events/ack returns 401 without auth", async () => {
    const token = "secret-ack-token";
    server = createBridgeServer(makeStubConfig(token, "generated"));
    port = await listenOnRandomPort(server);
    const res = await httpRequest(port, {
      method: "POST",
      path: "/events/ack",
      body: JSON.stringify({ task_id: "t1", source_event_ids: ["e1"] }),
    });
    assert.equal(res.status, 401);
  });

  it("GET /task_status is readable without auth", async () => {
    const token = "secret-status-token";
    server = createBridgeServer(makeStubConfig(token, "generated"));
    port = await listenOnRandomPort(server);
    const res = await httpRequest(port, {
      method: "GET",
      path: "/task_status?task_id=t1&attempt_no=0",
    });
    assert.equal(res.status, 200);
    assert.equal(res.body.ok, true);
  });
});

// ── Insecure mode tests ─────────────────────────────────────────

describe("Insecure mode (bridgeInsecure=true)", () => {
  let server: http.Server;
  let port: number;

  afterEach(() => {
    if (server) server.close();
  });

  it("POST /run_task succeeds without auth header", async () => {
    server = createBridgeServer(makeStubConfig("", "insecure"));
    port = await listenOnRandomPort(server);
    const res = await httpRequest(port, {
      method: "POST",
      path: "/run_task",
      body: JSON.stringify({
        task_id: "t1",
        attempt_no: 0,
        review_round: 0,
        prompt: "test",
        repo_path: "/tmp",
      }),
    });
    assert.equal(res.status, 200);
    assert.equal(res.body.ok, true);
  });

  it("POST /wake succeeds without auth header", async () => {
    server = createBridgeServer(makeStubConfig("", "insecure"));
    port = await listenOnRandomPort(server);
    const res = await httpRequest(port, {
      method: "POST",
      path: "/wake",
      body: JSON.stringify({ wake_id: "w1", task_id: "t1", reason: "test" }),
    });
    assert.equal(res.status, 200);
    assert.equal(res.body.ok, true);
  });

  it("GET /health succeeds without auth header", async () => {
    server = createBridgeServer(makeStubConfig("", "insecure"));
    port = await listenOnRandomPort(server);
    const res = await httpRequest(port, { method: "GET", path: "/health" });
    assert.equal(res.status, 200);
    assert.equal(res.body.ok, true);
  });
});

// ── Configured token tests ──────────────────────────────────────

describe("Configured token mode", () => {
  let server: http.Server;
  let port: number;

  afterEach(() => {
    if (server) server.close();
  });

  it("POST /run_task returns 401 with wrong token", async () => {
    server = createBridgeServer(
      makeStubConfig("configured-secret", "configured"),
    );
    port = await listenOnRandomPort(server);
    const res = await httpRequest(port, {
      method: "POST",
      path: "/run_task",
      headers: { Authorization: "Bearer wrong-token" },
      body: JSON.stringify({
        task_id: "t1",
        attempt_no: 0,
        review_round: 0,
        prompt: "test",
        repo_path: "/tmp",
      }),
    });
    assert.equal(res.status, 401);
  });

  it("POST /run_task succeeds with correct configured token", async () => {
    server = createBridgeServer(
      makeStubConfig("configured-secret", "configured"),
    );
    port = await listenOnRandomPort(server);
    const res = await httpRequest(port, {
      method: "POST",
      path: "/run_task",
      headers: { Authorization: "Bearer configured-secret" },
      body: JSON.stringify({
        task_id: "t1",
        attempt_no: 0,
        review_round: 0,
        prompt: "test",
        repo_path: "/tmp",
      }),
    });
    assert.equal(res.status, 200);
    assert.equal(res.body.ok, true);
  });

  it("GET /health still accessible without auth", async () => {
    server = createBridgeServer(
      makeStubConfig("configured-secret", "configured"),
    );
    port = await listenOnRandomPort(server);
    const res = await httpRequest(port, { method: "GET", path: "/health" });
    assert.equal(res.status, 200);
    assert.equal(res.body.ok, true);
  });
});

// ── /write_receipt prompt wiring test ───────────────────────────

describe("/write_receipt prompt wiring", () => {
  it("appendReceiptInstruction includes the effective token in Authorization header", () => {
    // We test this by importing TaskExecutionService and calling the private method
    // through a test wrapper.
    const {
      TaskExecutionService,
    } = require("../services/taskExecutionService");
    const service = new TaskExecutionService(
      {
        createBackgroundSession: async () => ({ ok: true, session_id: "s1" }),
      } as any,
      { get: () => null, count: () => 0 } as any,
      { push: () => {} } as any,
    );

    service.setBridgeContext({
      port: 9999,
      authToken: "effective-secret-token",
    });

    // Call the private method via bracket notation
    const result = (service as any).appendReceiptInstruction(
      "do the task",
      { task_id: "t1", attempt_no: 0, review_round: 0, repo_path: "/tmp" },
      "/tmp/.agpair/receipts/t1-0-0.json",
    );

    assert.ok(
      result.includes("Authorization: Bearer effective-secret-token"),
      "Prompt should include the effective token in Authorization header",
    );
    assert.ok(
      result.includes("http://127.0.0.1:9999/write_receipt"),
      "Prompt should include the bridge URL",
    );
    assert.ok(
      result.includes('"schema_version": "1"'),
      "Prompt should instruct schema_version=1",
    );
    assert.ok(
      result.includes('"payload"'),
      "Prompt should mention the payload field",
    );
  });

  it("appendReceiptInstruction omits Authorization header when token is empty", () => {
    const {
      TaskExecutionService,
    } = require("../services/taskExecutionService");
    const service = new TaskExecutionService(
      {
        createBackgroundSession: async () => ({ ok: true, session_id: "s1" }),
      } as any,
      { get: () => null, count: () => 0 } as any,
      { push: () => {} } as any,
    );

    service.setBridgeContext({ port: 9999, authToken: "" });

    const result = (service as any).appendReceiptInstruction(
      "do the task",
      { task_id: "t1", attempt_no: 0, review_round: 0, repo_path: "/tmp" },
      "/tmp/.agpair/receipts/t1-0-0.json",
    );

    assert.ok(
      !result.includes("Authorization: Bearer"),
      "Prompt should NOT include Authorization header when token is empty",
    );
  });
});

// ── Health payload safety ───────────────────────────────────────

describe("Health payload does not leak secrets", () => {
  let server: http.Server;
  let port: number;

  afterEach(() => {
    if (server) server.close();
  });

  it("health payload contains auth mode but NOT the token value", async () => {
    const secretToken = "super-secret-never-leak-this-value";
    server = createBridgeServer(makeStubConfig(secretToken, "generated"));
    port = await listenOnRandomPort(server);
    const res = await httpRequest(port, { method: "GET", path: "/health" });
    assert.equal(res.status, 200);

    const raw = JSON.stringify(res.body);
    assert.ok(
      !raw.includes(secretToken),
      "The secret token must NEVER appear in the health payload",
    );
    assert.equal(res.body.bridge_auth_mode, "generated");
    assert.equal(res.body.bridge_mutating_auth_required, true);
  });

  it("insecure mode health payload shows correct metadata", async () => {
    server = createBridgeServer(makeStubConfig("", "insecure"));
    port = await listenOnRandomPort(server);
    const res = await httpRequest(port, { method: "GET", path: "/health" });
    assert.equal(res.status, 200);
    assert.equal(res.body.bridge_auth_mode, "insecure");
    assert.equal(res.body.bridge_mutating_auth_required, false);
  });
});

// ── checkAuth unit tests ────────────────────────────────────────

describe("checkAuth", () => {
  it("returns true when token is empty (no auth mode)", () => {
    const req = { headers: {} } as http.IncomingMessage;
    assert.ok(checkAuth(req, ""));
  });

  it("returns false when token is set but no header provided", () => {
    const req = { headers: {} } as http.IncomingMessage;
    assert.ok(!checkAuth(req, "secret"));
  });

  it("returns true when correct bearer token is provided", () => {
    const req = { headers: { authorization: "Bearer correct" } } as any;
    assert.ok(checkAuth(req, "correct"));
  });

  it("returns false when wrong bearer token is provided", () => {
    const req = { headers: { authorization: "Bearer wrong" } } as any;
    assert.ok(!checkAuth(req, "correct"));
  });
});
