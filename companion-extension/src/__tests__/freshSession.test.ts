import { describe, it } from "node:test";
import * as assert from "node:assert/strict";

import { pickFreshSessionId } from "../sdk/freshSession";

describe("pickFreshSessionId", () => {
  it("accepts a returned session id when it was not present before", () => {
    const sessionId = pickFreshSessionId(
      new Set(["sess-old-1", "sess-old-2"]),
      "sess-new-1",
      [{ id: "sess-old-1" }, { id: "sess-old-2" }, { id: "sess-new-1" }],
    );

    assert.equal(sessionId, "sess-new-1");
  });

  it("prefers a newly discovered session when LS returned an existing session id", () => {
    const sessionId = pickFreshSessionId(
      new Set(["sess-old-1"]),
      "sess-old-1",
      [{ id: "sess-old-1" }, { id: "sess-new-2" }],
    );

    assert.equal(sessionId, "sess-new-2");
  });

  it("returns null when there is no fresh session at all", () => {
    const sessionId = pickFreshSessionId(
      new Set(["sess-old-1"]),
      "sess-old-1",
      [{ id: "sess-old-1" }],
    );

    assert.equal(sessionId, null);
  });
});
