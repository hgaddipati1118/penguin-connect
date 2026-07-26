const assert = require("node:assert/strict");
const test = require("node:test");

const {
  DEFAULT_RENDER_WINDOWS,
  nextVisibleCount,
} = require("./list-windowing.js");

test("uses small render windows for rich files and conversation rows", () => {
  assert.equal(DEFAULT_RENDER_WINDOWS.files, 24);
  assert.equal(DEFAULT_RENDER_WINDOWS.conversations, 48);
});

test("reveals one bounded window instead of every newly loaded row", () => {
  assert.equal(nextVisibleCount(24, 200, 24), 48);
  assert.equal(nextVisibleCount(200, 400, 24), 224);
});

test("never grows past the number of loaded rows", () => {
  assert.equal(nextVisibleCount(190, 200, 24), 200);
  assert.equal(nextVisibleCount(200, 200, 24), 200);
  assert.equal(nextVisibleCount(0, 0, 24), 0);
});
