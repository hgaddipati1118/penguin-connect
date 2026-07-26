const assert = require("node:assert/strict");
const test = require("node:test");

const {
  planWorkspaceHydration,
} = require("./workspace-cache-policy.js");

test("hydrates only the remembered thread on the startup-critical path", () => {
  const plan = planWorkspaceHydration(
    ["thread-a", "thread-b", "thread-c", "thread-d"],
    {
      rememberedConversationId: "thread-c",
      eagerLimit: 3,
      totalLimit: 4,
    },
  );

  assert.deepEqual(plan.immediateIds, ["thread-c"]);
  assert.deepEqual(plan.eagerIds, ["thread-a", "thread-b"]);
  assert.deepEqual(plan.backgroundIds, ["thread-d"]);
});

test("does not duplicate or invent cached conversation ids", () => {
  const plan = planWorkspaceHydration(
    ["thread-a", "", "thread-a", "thread-b", null, "thread-c"],
    {
      rememberedConversationId: "missing",
      eagerLimit: 2,
      totalLimit: 3,
    },
  );

  assert.deepEqual(plan.immediateIds, []);
  assert.deepEqual(plan.eagerIds, ["thread-a", "thread-b"]);
  assert.deepEqual(plan.backgroundIds, ["thread-c"]);
});

test("keeps every hydration phase within its configured bound", () => {
  const ids = Array.from({ length: 20 }, (_, index) => `thread-${index + 1}`);
  const plan = planWorkspaceHydration(ids, {
    rememberedConversationId: "thread-10",
    eagerLimit: 4,
    totalLimit: 9,
  });

  assert.equal(plan.immediateIds.length, 1);
  assert.equal(plan.eagerIds.length, 3);
  assert.equal(plan.backgroundIds.length, 5);
  assert.equal(
    new Set([
      ...plan.immediateIds,
      ...plan.eagerIds,
      ...plan.backgroundIds,
    ]).size,
    9,
  );
});
