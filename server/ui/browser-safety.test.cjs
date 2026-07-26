const assert = require("node:assert/strict");
const test = require("node:test");

const {
  isReadOnlyBrowserSession,
  requestMethodMutates,
} = require("./browser-safety.js");

test("treats automated browsers as read only", () => {
  assert.equal(isReadOnlyBrowserSession({ webdriver: true, search: "" }), true);
});

test("supports an explicit read-only query parameter for QA", () => {
  assert.equal(
    isReadOnlyBrowserSession({
      webdriver: false,
      search: "?penguin_readonly=1",
    }),
    true,
  );
});

test("recognizes QA mutation URLs as read-only sessions", () => {
  assert.equal(
    isReadOnlyBrowserSession({
      webdriver: false,
      search: "?slack-mutations-qa=4",
    }),
    true,
  );
});

test("does not change normal browser sessions", () => {
  assert.equal(
    isReadOnlyBrowserSession({
      webdriver: false,
      search: "?source=slack",
    }),
    false,
  );
});

test("blocks every state-changing HTTP method", () => {
  assert.equal(requestMethodMutates("GET"), false);
  assert.equal(requestMethodMutates("HEAD"), false);
  assert.equal(requestMethodMutates("POST"), true);
  assert.equal(requestMethodMutates("PUT"), true);
  assert.equal(requestMethodMutates("PATCH"), true);
  assert.equal(requestMethodMutates("DELETE"), true);
});
