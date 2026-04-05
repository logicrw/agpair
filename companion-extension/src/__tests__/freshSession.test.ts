import { describe, it } from "node:test";
import * as assert from "node:assert/strict";

import {
  pickFreshSessionId,
  pickFreshTrajectoryId,
} from "../sdk/freshSession";

describe("freshSession helpers", () => {
  it("prefers a returned session id when it is new", () => {
    const beforeIds = new Set(["sess-old"]);

    const result = pickFreshSessionId(beforeIds, "sess-new", [
      { id: "sess-old" },
      { id: "sess-new" },
    ]);

    assert.equal(result, "sess-new");
  });

  it("selects the newest fresh session id from a session snapshot diff", () => {
    const beforeIds = new Set(["sess-old"]);

    const result = pickFreshSessionId(beforeIds, "", [
      { id: "sess-old" },
      { id: "sess-new-1" },
      { id: "sess-new-2" },
    ]);

    assert.equal(result, "sess-new-2");
  });

  it("picks the first fresh trajectory id from diagnostics order", () => {
    const beforeIds = new Set(["traj-old"]);

    const result = pickFreshTrajectoryId(beforeIds, [
      { googleAgentId: "traj-new" },
      { googleAgentId: "traj-older-new" },
      { googleAgentId: "traj-old" },
    ]);

    assert.equal(result, "traj-new");
  });

  it("returns null when diagnostics contain no fresh trajectory id", () => {
    const beforeIds = new Set(["traj-old"]);

    const result = pickFreshTrajectoryId(beforeIds, [
      { googleAgentId: "traj-old" },
      { googleAgentId: "" },
      {},
    ]);

    assert.equal(result, null);
  });
});
