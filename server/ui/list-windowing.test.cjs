const assert = require("node:assert/strict");
const test = require("node:test");

const {
  clampWindowStart,
  DEFAULT_RENDER_WINDOWS,
  nextVisibleCount,
  windowStartForIndex,
  windowStartForScroll,
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

test("keeps keyboard jumps inside a fixed conversation window", () => {
  assert.equal(windowStartForIndex(0, 25, 1984, 48), 0);
  assert.equal(windowStartForIndex(0, 55, 1984, 48), 8);
  assert.equal(windowStartForIndex(8, 7, 1984, 48), 7);
  assert.equal(windowStartForIndex(0, 1983, 1984, 48), 1936);
});

test("derives a bounded overscanned window from list scroll position", () => {
  assert.equal(windowStartForScroll(0, 76, 1984, 48, 8), 0);
  assert.equal(windowStartForScroll(7600, 76, 1984, 48, 8), 92);
  assert.equal(windowStartForScroll(999999, 76, 1984, 48, 8), 1936);
});

test("clamps windows when a filtered list is shorter than the viewport", () => {
  assert.equal(clampWindowStart(-10, 18, 48), 0);
  assert.equal(clampWindowStart(10, 18, 48), 0);
  assert.equal(clampWindowStart(100, 80, 48), 32);
});
