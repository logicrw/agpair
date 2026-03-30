import { describe, it } from "node:test";
import * as assert from "node:assert/strict";

import {
  extractCliArg,
  parseListeningPorts,
  parseLsProcessLine,
  selectLsProcessInfo,
} from "../sdk/lsConnectionRepair";

describe("lsConnectionRepair helpers", () => {
  it("extracts spaced CLI args", () => {
    const line = "--csrf_token abc --extension_server_port 57335";
    assert.equal(extractCliArg(line, "csrf_token"), "abc");
    assert.equal(extractCliArg(line, "extension_server_port"), "57335");
  });

  it("parses a language_server process line with both csrf tokens", () => {
    const workspaceId = "file_Users_logicrw_Projects_agpair";
    const line =
      " 5178 /Applications/Antigravity.app/.../language_server_macos_arm " +
      "--enable_lsp --csrf_token main-token --extension_server_port 57335 " +
      `--extension_server_csrf_token ext-token --workspace_id ${workspaceId}`;
    const parsed = parseLsProcessLine(line);
    assert.ok(parsed);
    assert.equal(parsed!.pid, 5178);
    assert.equal(parsed!.csrfToken, "main-token");
    assert.equal(parsed!.extensionServerPort, 57335);
    assert.equal(parsed!.extensionServerCsrfToken, "ext-token");
  });

  it("prefers the workspace-matching language_server process", () => {
    const workspaceId = "file_Users_logicrw_Projects_agpair";
    const output = [
      " 1111 /bin/language_server --csrf_token old-token --workspace_id file_Users_someone_else_Project",
      ` 2222 /bin/language_server --csrf_token right-token --workspace_id ${workspaceId}`,
    ].join("\n");
    const selected = selectLsProcessInfo(output, workspaceId);
    assert.ok(selected);
    assert.equal(selected!.pid, 2222);
    assert.equal(selected!.csrfToken, "right-token");
  });

  it("parses listening ports from lsof output", () => {
    const output = [
      "language_ 5178 logicrw 4u IPv4 0x0 0t0 TCP 127.0.0.1:57339 (LISTEN)",
      "language_ 5178 logicrw 5u IPv4 0x0 0t0 TCP 127.0.0.1:57340 (LISTEN)",
      "language_ 5178 logicrw 6u IPv4 0x0 0t0 TCP 127.0.0.1:57340 (LISTEN)",
    ].join("\n");
    assert.deepEqual(parseListeningPorts(output), [57339, 57340]);
  });
});
