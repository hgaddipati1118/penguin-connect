(function exposePenguinScheduleTime(root) {
  // Default hour when the user names a day but no time ("tomorrow", "fri").
  const DEFAULT_DAY_HOUR = 9;
  const PART_OF_DAY_HOURS = Object.freeze({
    morning: 9,
    noon: 12,
    midday: 12,
    afternoon: 14,
    evening: 18,
    tonight: 20,
    night: 20,
  });
  const WEEKDAYS = [
    ["sunday", "sun"],
    ["monday", "mon"],
    ["tuesday", "tue", "tues"],
    ["wednesday", "wed"],
    ["thursday", "thu", "thur", "thurs"],
    ["friday", "fri"],
    ["saturday", "sat"],
  ];
  const MONTHS = [
    "jan", "feb", "mar", "apr", "may", "jun",
    "jul", "aug", "sep", "oct", "nov", "dec",
  ];
  const UNIT_MS = {
    m: 60 * 1000,
    h: 60 * 60 * 1000,
    d: 24 * 60 * 60 * 1000,
    w: 7 * 24 * 60 * 60 * 1000,
  };

  function atHour(base, dayOffset, hour, minute = 0) {
    const date = new Date(base.getTime());
    date.setDate(date.getDate() + dayOffset);
    date.setHours(hour, minute, 0, 0);
    return date;
  }

  function roundUpToFiveMinutes(date) {
    const rounded = new Date(date.getTime());
    rounded.setSeconds(0, 0);
    const remainder = rounded.getMinutes() % 5;
    if (remainder) rounded.setMinutes(rounded.getMinutes() + (5 - remainder));
    return rounded;
  }

  function weekdayIndex(word) {
    return WEEKDAYS.findIndex((names) => names.includes(word));
  }

  function daysUntilWeekday(now, target) {
    const diff = (target - now.getDay() + 7) % 7;
    return diff === 0 ? 7 : diff;
  }

  // Parses "3", "3pm", "3:30 pm", "15:00", "9a". Returns {hour, minute, explicitMeridiem}.
  function parseClock(text) {
    const match = /^(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(a|am|p|pm)?$/.exec(text);
    if (!match) return null;
    let hour = Number(match[1]);
    const minute = match[2] ? Number(match[2]) : 0;
    const meridiem = match[3] ? match[3][0] : "";
    if (minute > 59) return null;
    if (meridiem) {
      if (hour < 1 || hour > 12) return null;
      if (meridiem === "p" && hour !== 12) hour += 12;
      if (meridiem === "a" && hour === 12) hour = 0;
    } else if (hour > 23) {
      return null;
    }
    return { hour, minute, explicitMeridiem: Boolean(meridiem) || hour > 12 || hour === 0 };
  }

  // Bare hours attached to a day ("tomorrow 3") read as daytime: 7–11 → AM, 12–6 → PM.
  function daytimeHour(clock) {
    if (clock.explicitMeridiem || clock.hour > 12) return clock.hour;
    if (clock.hour >= 1 && clock.hour <= 6) return clock.hour + 12;
    return clock.hour;
  }

  function splitTime(text) {
    // Pull a trailing clock time ("... at 3pm", "... 15:30") off the end of the phrase.
    const match = /^(.*?)(?:\s+(?:at\s+)?)(\d{1,2}(?::\d{2})?\s*(?:am|pm|a|p)?)$/.exec(text);
    if (match) {
      const clock = parseClock(match[2]);
      if (clock) return { rest: match[1].trim(), clock };
    }
    return { rest: text, clock: null };
  }

  function dayPhraseOffset(phrase, now) {
    if (!phrase || phrase === "today") return { offset: 0, hour: null };
    if (["tomorrow", "tom", "tmr", "tmrw", "tmw"].includes(phrase)) return { offset: 1, hour: null };
    if (phrase === "next week") {
      return { offset: daysUntilWeekday(now, 1), hour: null };
    }
    if (phrase === "weekend" || phrase === "this weekend") {
      return { offset: now.getDay() === 6 ? 7 : daysUntilWeekday(now, 6), hour: 10 };
    }
    const partWords = phrase.split(/\s+/);
    const last = partWords[partWords.length - 1];
    if (PART_OF_DAY_HOURS[last] !== undefined) {
      const head = partWords.slice(0, -1).join(" ").replace(/^this$/, "");
      const day = dayPhraseOffset(head, now);
      if (!day) return null;
      return { offset: day.offset, hour: PART_OF_DAY_HOURS[last] };
    }
    const weekday = weekdayIndex(phrase.replace(/^(next|this|on)\s+/, ""));
    if (weekday >= 0) return { offset: daysUntilWeekday(now, weekday), hour: null };
    return parseCalendarDate(phrase, now);
  }

  // "11/12", "nov 12", "12 nov", "november 12th"
  function parseCalendarDate(phrase, now) {
    let month = -1;
    let day = 0;
    let match = /^(\d{1,2})\/(\d{1,2})$/.exec(phrase);
    if (match) {
      month = Number(match[1]) - 1;
      day = Number(match[2]);
    } else {
      match = /^([a-z]+)\.?\s+(\d{1,2})(?:st|nd|rd|th)?$/.exec(phrase)
        || /^(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]+)$/.exec(phrase);
      if (!match) return null;
      const [word, num] = /^\d/.test(match[1]) ? [match[2], match[1]] : [match[1], match[2]];
      month = MONTHS.indexOf(word.slice(0, 3));
      day = Number(num);
    }
    if (month < 0 || month > 11 || day < 1 || day > 31) return null;
    let target = new Date(now.getFullYear(), month, day);
    if (target.getMonth() !== month) return null;
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    if (target < today) target = new Date(now.getFullYear() + 1, month, day);
    const offset = Math.round((target - today) / UNIT_MS.d);
    return { offset, hour: null };
  }

  function parseScheduleText(input, now = new Date()) {
    const text = String(input || "")
      .toLowerCase()
      .replace(/[,]/g, " ")
      .replace(/\s+/g, " ")
      .trim();
    if (!text) return null;

    // Relative durations: "2h", "in 30 min", "90m", "in 2 days", "1.5 hours"
    const relative = /^(?:in\s+)?(\d+(?:\.\d+)?)\s*(m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days|w|wk|week|weeks)$/.exec(text);
    if (relative) {
      const amount = Number(relative[1]);
      const unit = relative[2][0];
      if (!amount) return null;
      const date = new Date(now.getTime() + amount * UNIT_MS[unit]);
      return unit === "m" || unit === "h" ? roundUpToFiveMinutes(date) : atHour(date, 0, date.getHours(), 0);
    }
    if (/^in (an?|one) hour$/.test(text)) return roundUpToFiveMinutes(new Date(now.getTime() + UNIT_MS.h));

    // Time only: "3pm", "at 9", "15:30" → next future occurrence.
    const clockOnly = parseClock(text);
    if (clockOnly) {
      if (clockOnly.explicitMeridiem) {
        const today = atHour(now, 0, clockOnly.hour, clockOnly.minute);
        return today > now ? today : atHour(now, 1, clockOnly.hour, clockOnly.minute);
      }
      // Ambiguous bare hour: soonest of AM/PM that is still in the future.
      const candidates = [clockOnly.hour % 12, (clockOnly.hour % 12) + 12]
        .flatMap((hour) => [atHour(now, 0, hour, clockOnly.minute), atHour(now, 1, hour, clockOnly.minute)])
        .filter((date) => date > now)
        .sort((a, b) => a - b);
      return candidates[0] || null;
    }

    // Try the whole phrase as a day first so "jan 2" is a date, not "jan" at 2 o'clock.
    const wholeDay = dayPhraseOffset(text, now);
    const { rest, clock } = wholeDay ? { rest: text, clock: null } : splitTime(text);
    const day = wholeDay || dayPhraseOffset(rest, now);
    if (!day) return null;
    let hour = day.hour ?? DEFAULT_DAY_HOUR;
    let minute = 0;
    if (clock) {
      hour = daytimeHour(clock);
      minute = clock.minute;
      // "tonight 9" / "evening 7" mean PM.
      if (day.hour !== null && day.hour >= 12 && hour < 12) hour += 12;
    }
    const date = atHour(now, day.offset, hour, minute);
    if (date > now) return date;
    // A bare part of day that already passed ("morning" at 3pm) means the next one.
    // Anything pinned to today ("today 3pm", "tonight" at 11pm) is invalid instead.
    if (day.offset === 0 && PART_OF_DAY_HOURS[rest] !== undefined && rest !== "tonight") {
      return atHour(now, 1, hour, minute);
    }
    return null;
  }

  function schedulePresets(now = new Date()) {
    const presets = [
      { label: "In 1 hour", date: roundUpToFiveMinutes(new Date(now.getTime() + UNIT_MS.h)) },
      { label: "In 3 hours", date: roundUpToFiveMinutes(new Date(now.getTime() + 3 * UNIT_MS.h)) },
    ];
    if (now.getHours() < 18) presets.push({ label: "This evening", date: atHour(now, 0, 18) });
    else if (now.getHours() < 20) presets.push({ label: "Tonight", date: atHour(now, 0, 20) });
    presets.push({ label: "Tomorrow morning", date: atHour(now, 1, 9) });
    presets.push({ label: "Tomorrow afternoon", date: atHour(now, 1, 14) });
    const day = now.getDay();
    if (day >= 1 && day <= 4) {
      presets.push({ label: "This weekend", date: atHour(now, daysUntilWeekday(now, 6), 10) });
    }
    presets.push({ label: "Next week", date: atHour(now, daysUntilWeekday(now, 1), 9) });
    // Keep the list chronological and drop presets within 30 minutes of an earlier one.
    presets.sort((a, b) => a.date - b.date);
    return presets.filter((preset, index) => (
      index === 0 || preset.date - presets[index - 1].date >= 30 * UNIT_MS.m
    ));
  }

  const api = Object.freeze({
    parseScheduleText,
    schedulePresets,
  });
  root.PenguinScheduleTime = api;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
}(typeof globalThis === "undefined" ? this : globalThis));
