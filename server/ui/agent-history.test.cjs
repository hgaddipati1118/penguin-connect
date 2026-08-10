const assert = require("node:assert/strict");
const test = require("node:test");

const {
  normalizeAgentSession,
  recentAgentSessions,
  sessionTranscript,
} = require("./agent-history.js");

test("normalizes a persisted agent session into bounded local history", () => {
  const record = normalizeAgentSession({
    id: "agent-1",
    title: "",
    createdAt: 100,
    updatedAt: 200,
    conversationId: "amc-1",
    conversationName: "Anh the goat",
    provider: "iMessage",
    history: [
      { role: "system", text: "ignore" },
      { role: "user", text: "  Find the internship form  ", timestamp: 101 },
      { role: "assistant", text: "I found it.", timestamp: 102 },
      { role: "assistant", text: "" },
    ],
    activity: [
      { id: "tool-1", kind: "command", text: "Command: pwd", status: "completed" },
      { text: "" },
    ],
    references: [
      {
        conversationId: "amc-1",
        label: "Anh the goat",
        provider: "iMessage",
        reason: "Relevant match",
      },
    ],
  });

  assert.equal(record.title, "Find the internship form");
  assert.equal(record.history.length, 2);
  assert.equal(record.history[0].text, "Find the internship form");
  assert.equal(record.activity.length, 1);
  assert.equal(record.references.length, 1);
});

test("sorts and deduplicates recent sessions by latest activity", () => {
  const sessions = recentAgentSessions([
    { id: "older", updatedAt: 100, history: [] },
    { id: "newer", updatedAt: 300, history: [] },
    { id: "older", updatedAt: 200, history: [] },
  ]);

  assert.deepEqual(sessions.map((session) => session.id), ["newer", "older"]);
  assert.equal(sessions[1].updatedAt, 200);
});

test("builds compact multi-turn context without repeating the trailing question", () => {
  const transcript = sessionTranscript([
    { role: "user", text: "Who sent the file?" },
    { role: "assistant", text: "Anh sent it." },
    { role: "user", text: "What was it called?" },
  ], { excludeTrailingUser: true });

  assert.equal(transcript, "USER: Who sent the file?\nASSISTANT: Anh sent it.");
});
