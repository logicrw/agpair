import { afterEach, describe, it } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { removeWrittenMarkers, writeMarkerToDir } from "../bridge/discoveryMarkers";

describe("discovery marker helpers", () => {
  const tempRoots: string[] = [];

  afterEach(() => {
    for (const root of tempRoots.splice(0)) {
      fs.rmSync(root, { recursive: true, force: true });
    }
  });

  it("writes port and auth markers under .agpair and cleans them up", () => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), "agpair-marker-test-"));
    tempRoots.push(root);
    const writtenPaths: string[] = [];

    const wrotePort = writeMarkerToDir({
      dir: root,
      markerName: "bridge_port",
      value: "8765",
      writtenPaths,
    });
    const wroteToken = writeMarkerToDir({
      dir: root,
      markerName: "bridge_auth_token",
      value: "secret-token-123",
      writtenPaths,
      mode: 0o600,
    });

    assert.equal(wrotePort, true);
    assert.equal(wroteToken, true);
    assert.equal(
      fs.readFileSync(path.join(root, ".agpair", "bridge_port"), "utf-8"),
      "8765",
    );
    assert.equal(
      fs.readFileSync(path.join(root, ".agpair", "bridge_auth_token"), "utf-8"),
      "secret-token-123",
    );
    assert.equal(
      fs.statSync(path.join(root, ".agpair", "bridge_auth_token")).mode & 0o777,
      0o600,
    );

    removeWrittenMarkers(writtenPaths);

    assert.equal(fs.existsSync(path.join(root, ".agpair", "bridge_port")), false);
    assert.equal(fs.existsSync(path.join(root, ".agpair", "bridge_auth_token")), false);
    assert.deepEqual(writtenPaths, []);
  });
});
