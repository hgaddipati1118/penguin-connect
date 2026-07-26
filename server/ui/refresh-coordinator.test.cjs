const assert = require("node:assert/strict");
const test = require("node:test");

const {
  createCompletionGate,
  createRefreshCoordinator,
} = require("./refresh-coordinator.js");

test("coalesces concurrent refreshes for the same conversation and mode", async () => {
  const coordinator = createRefreshCoordinator({ cooldownMs: 1200 });
  let calls = 0;
  let release;
  const pending = new Promise((resolve) => {
    release = resolve;
  });
  const task = async () => {
    calls += 1;
    await pending;
    return "fresh";
  };

  const first = coordinator.run("conversation-a:incremental", task);
  const second = coordinator.run("conversation-a:incremental", task);
  release();

  assert.equal(await first, "fresh");
  assert.equal(await second, "fresh");
  assert.equal(calls, 1);
});

test("skips a duplicate successful refresh inside the cooldown", async () => {
  let now = 1000;
  const coordinator = createRefreshCoordinator({
    cooldownMs: 1200,
    now: () => now,
  });
  let calls = 0;
  const task = async () => {
    calls += 1;
    return calls;
  };

  assert.equal(await coordinator.run("conversation-a:full", task), 1);
  now += 500;
  assert.deepEqual(
    await coordinator.run("conversation-a:full", task),
    { skipped: "fresh" },
  );
  assert.equal(calls, 1);
  now += 1200;
  assert.equal(await coordinator.run("conversation-a:full", task), 2);
});

test("does not share cooldowns between conversations or refresh modes", async () => {
  const coordinator = createRefreshCoordinator({ cooldownMs: 1200 });
  let calls = 0;
  const task = async () => {
    calls += 1;
    return calls;
  };

  await coordinator.run("conversation-a:incremental", task);
  await coordinator.run("conversation-b:incremental", task);
  await coordinator.run("conversation-a:full", task);

  assert.equal(calls, 3);
});

test("retries after a failed refresh instead of cooling it down", async () => {
  const coordinator = createRefreshCoordinator({ cooldownMs: 1200 });
  let calls = 0;
  const task = async () => {
    calls += 1;
    if (calls === 1) throw new Error("temporary");
    return "recovered";
  };

  await assert.rejects(
    coordinator.run("conversation-a:incremental", task),
    /temporary/,
  );
  assert.equal(
    await coordinator.run("conversation-a:incremental", task),
    "recovered",
  );
  assert.equal(calls, 2);
});

test("allows explicit refreshes to bypass the cooldown", async () => {
  const coordinator = createRefreshCoordinator({ cooldownMs: 1200 });
  let calls = 0;
  const task = async () => {
    calls += 1;
    return calls;
  };

  assert.equal(await coordinator.run("conversation-a:full", task), 1);
  assert.equal(
    await coordinator.run("conversation-a:full", task, { force: true }),
    2,
  );
});

test("stops completed history repairs for the rest of the session", async () => {
  const gate = createCompletionGate();
  let calls = 0;
  const task = async () => {
    calls += 1;
    return { completed: true, imported: 120 };
  };

  assert.deepEqual(await gate.run("conversation-a", task), {
    completed: true,
    imported: 120,
  });
  assert.deepEqual(await gate.run("conversation-a", task), {
    skipped: "complete",
  });
  assert.equal(calls, 1);
});

test("keeps incomplete history repairs eligible for another pass", async () => {
  const gate = createCompletionGate();
  let calls = 0;
  const task = async () => {
    calls += 1;
    return { completed: calls === 2 };
  };

  assert.deepEqual(await gate.run("conversation-a", task), {
    completed: false,
  });
  assert.deepEqual(await gate.run("conversation-a", task), {
    completed: true,
  });
  assert.equal(calls, 2);
});

test("coalesces concurrent history repairs before marking them complete", async () => {
  const gate = createCompletionGate();
  let calls = 0;
  let release;
  const pending = new Promise((resolve) => {
    release = resolve;
  });
  const task = async () => {
    calls += 1;
    await pending;
    return { completed: true };
  };

  const first = gate.run("conversation-a", task);
  const second = gate.run("conversation-a", task);
  release();
  await Promise.all([first, second]);

  assert.equal(calls, 1);
});
