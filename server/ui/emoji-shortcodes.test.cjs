const assert = require("node:assert/strict");
const test = require("node:test");

const { completedShortcodeAt, emojiQueryAt, searchEmoji } = require("./emoji-shortcodes.js");

test("ranks exact and prefix shortcode matches first", () => {
  assert.equal(searchEmoji("fire")[0].emoji, "🔥");
  assert.equal(searchEmoji("thu")[0].name, "thumbsup");
  assert.equal(searchEmoji("joy")[0].emoji, "😂");
  assert.equal(searchEmoji("heart")[0].emoji, "❤️");
});

test("matches words inside shortcodes and keywords", () => {
  assert.ok(searchEmoji("eyes").some((item) => item.name === "heart_eyes"));
  assert.equal(searchEmoji("lol")[0].emoji, "😂");
  assert.equal(searchEmoji("congrats")[0].emoji, "🎉");
  assert.equal(searchEmoji("+1")[0].emoji, "👍");
});

test("limits results and ignores empty queries", () => {
  assert.equal(searchEmoji("").length, 0);
  assert.ok(searchEmoji("s", 5).length <= 5);
  assert.deepEqual(searchEmoji("zzzzqqq"), []);
});

test("detects an in-progress shortcode but not times or smileys", () => {
  assert.deepEqual(emojiQueryAt("nice :fi", 8), { start: 5, end: 8, query: "fi" });
  assert.deepEqual(emojiQueryAt(":tada", 5), { start: 0, end: 5, query: "tada" });
  assert.equal(emojiQueryAt("see you at 10:30", 16), null);
  assert.equal(emojiQueryAt("ok :)", 5), null);
  assert.equal(emojiQueryAt("ok :f", 5), null);
  assert.equal(emojiQueryAt("http://x", 8), null);
});

test("expands a completed :shortcode: and leaves unknown ones alone", () => {
  assert.deepEqual(completedShortcodeAt("lets go :fire:", 14), { start: 8, end: 14, emoji: "🔥" });
  assert.equal(completedShortcodeAt("lets go :nope:", 14), null);
  assert.equal(completedShortcodeAt("at 10:30:", 9), null);
});
