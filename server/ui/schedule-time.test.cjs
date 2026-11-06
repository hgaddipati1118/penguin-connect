const assert = require("node:assert/strict");
const test = require("node:test");

const { parseScheduleText, schedulePresets } = require("./schedule-time.js");

// Thursday, Nov 5 2026, 10:07 local time.
const NOW = new Date(2026, 10, 5, 10, 7, 0);

function local(year, month, day, hour, minute = 0) {
  return new Date(year, month - 1, day, hour, minute, 0, 0).getTime();
}

function parsed(text, now = NOW) {
  const date = parseScheduleText(text, now);
  return date ? date.getTime() : null;
}

test("parses relative durations rounded to five minutes", () => {
  assert.equal(parsed("2h"), local(2026, 11, 5, 12, 10));
  assert.equal(parsed("in 30 min"), local(2026, 11, 5, 10, 40));
  assert.equal(parsed("90m"), local(2026, 11, 5, 11, 40));
  assert.equal(parsed("in an hour"), local(2026, 11, 5, 11, 10));
  assert.equal(parsed("2 days"), local(2026, 11, 7, 10));
});

test("parses clock times as the next future occurrence", () => {
  assert.equal(parsed("3pm"), local(2026, 11, 5, 15));
  assert.equal(parsed("9am"), local(2026, 11, 6, 9));
  assert.equal(parsed("3:30 pm"), local(2026, 11, 5, 15, 30));
  assert.equal(parsed("15:45"), local(2026, 11, 5, 15, 45));
  assert.equal(parsed("at 3"), local(2026, 11, 5, 15));
  assert.equal(parsed("10"), local(2026, 11, 5, 22));
});

test("parses day words with and without times", () => {
  assert.equal(parsed("tomorrow"), local(2026, 11, 6, 9));
  assert.equal(parsed("tom 3"), local(2026, 11, 6, 15));
  assert.equal(parsed("tmrw at 8"), local(2026, 11, 6, 8));
  assert.equal(parsed("tomorrow 7:30pm"), local(2026, 11, 6, 19, 30));
  assert.equal(parsed("tonight"), local(2026, 11, 5, 20));
  assert.equal(parsed("tonight 9"), local(2026, 11, 5, 21));
  assert.equal(parsed("tomorrow evening"), local(2026, 11, 6, 18));
  assert.equal(parsed("today 4pm"), local(2026, 11, 5, 16));
});

test("weekday names always mean a future day", () => {
  assert.equal(parsed("fri"), local(2026, 11, 6, 9));
  assert.equal(parsed("friday 2pm"), local(2026, 11, 6, 14));
  assert.equal(parsed("thu"), local(2026, 11, 12, 9));
  assert.equal(parsed("wed"), local(2026, 11, 11, 9));
  assert.equal(parsed("next mon 10am"), local(2026, 11, 9, 10));
  assert.equal(parsed("next week"), local(2026, 11, 9, 9));
});

test("parses calendar dates and rolls past dates into next year", () => {
  assert.equal(parsed("11/12"), local(2026, 11, 12, 9));
  assert.equal(parsed("nov 20 3pm"), local(2026, 11, 20, 15));
  assert.equal(parsed("20th december"), local(2026, 12, 20, 9));
  assert.equal(parsed("jan 2"), local(2027, 1, 2, 9));
  assert.equal(parsed("dec 3 4"), local(2026, 12, 3, 16));
  assert.equal(parsed("feb 30"), null);
});

test("rolls a passed part of day forward but rejects pinned past times", () => {
  const afternoon = new Date(2026, 10, 5, 15, 0, 0);
  assert.equal(parsed("morning", afternoon), local(2026, 11, 6, 9));
  assert.equal(parsed("today 9am", afternoon), null);
  const late = new Date(2026, 10, 5, 23, 0, 0);
  assert.equal(parsed("tonight", late), null);
});

test("returns null for nonsense", () => {
  assert.equal(parsed(""), null);
  assert.equal(parsed("whenever"), null);
  assert.equal(parsed("13pm"), null);
  assert.equal(parsed("0h"), null);
});

test("presets are future, ordered, and adapt to time of day", () => {
  const presets = schedulePresets(NOW);
  assert.deepEqual(presets.map((preset) => preset.label), [
    "In 1 hour",
    "In 3 hours",
    "This evening",
    "Tomorrow morning",
    "Tomorrow afternoon",
    "This weekend",
    "Next week",
  ]);
  for (let index = 1; index < presets.length; index += 1) {
    assert.ok(presets[index].date > presets[index - 1].date);
  }
  assert.ok(presets.every((preset) => preset.date > NOW));

  const friday = new Date(2026, 10, 6, 18, 5, 0);
  const fridayLabels = schedulePresets(friday).map((preset) => preset.label);
  assert.ok(fridayLabels.includes("Tonight"));
  assert.ok(!fridayLabels.includes("This weekend"));
});

test("presets stay chronological late in the day", () => {
  const afternoon = new Date(2026, 10, 5, 16, 25, 0);
  const presets = schedulePresets(afternoon);
  for (let index = 1; index < presets.length; index += 1) {
    assert.ok(presets[index].date > presets[index - 1].date, presets[index].label);
  }
  assert.equal(presets[1].label, "This evening");
  // At 4:50pm "In 1 hour" (5:50pm) is within 30 min of "This evening" (6pm), so 6pm is dropped.
  const lateAfternoon = new Date(2026, 10, 5, 16, 50, 0);
  const labels = schedulePresets(lateAfternoon).map((preset) => preset.label);
  assert.deepEqual(labels.slice(0, 2), ["In 1 hour", "In 3 hours"]);
});
