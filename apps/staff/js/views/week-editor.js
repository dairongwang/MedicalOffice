import { button, callout, formatTime, h, input, render as replace } from "../ui.js";
import { WEEKDAYS, standardWeek, validateWeek } from "../validation.js";

/**
 * The seven-day availability editor shared by the public "My availability" page and the
 * manager's roster view. `rows` = [{ weekday, available, start, end, note }].
 * Returns { el, rows(), setRows(), summary() }.
 */
export function weekEditor(initialRows, { onChange } = {}) {
  let rows = initialRows.map((r) => ({ ...r }));
  const list = h("ul", { class: "week", "aria-label": "Days of the week" });
  const summary = h("p", { class: "small muted", id: "week-summary" });
  const errors = h("div");

  const update = (weekday, patch) => {
    rows = rows.map((r) => (r.weekday === weekday ? { ...r, ...patch } : r));
    paint();
    onChange?.(rows);
  };

  function paint() {
    replace(
      list,
      ...rows.map((d) =>
        h(
          "li",
          { class: `day ${d.available ? "" : "off"}` },
          h(
            "label",
            { class: "day-toggle", for: `day-on-${d.weekday}` },
            h("input", {
              type: "checkbox",
              id: `day-on-${d.weekday}`,
              checked: d.available,
              onchange: (e) => update(d.weekday, { available: e.target.checked }),
            }),
            h("span", { class: "day-name" }, WEEKDAYS[d.weekday]),
            h(
              "span",
              { class: "day-state muted small" },
              d.available ? `${formatTime(d.start)} – ${formatTime(d.end)}` : "Not working",
            ),
          ),
          d.available
            ? h(
                "div",
                { class: "day-times" },
                input({
                  type: "time",
                  value: d.start,
                  step: 900,
                  required: true,
                  id: `day-start-${d.weekday}`,
                  "aria-label": `${WEEKDAYS[d.weekday]} start`,
                  onchange: (e) => update(d.weekday, { start: e.target.value }),
                }),
                h("span", { class: "time-sep", "aria-hidden": "true" }, "–"),
                input({
                  type: "time",
                  value: d.end,
                  step: 900,
                  required: true,
                  id: `day-end-${d.weekday}`,
                  "aria-label": `${WEEKDAYS[d.weekday]} end`,
                  onchange: (e) => update(d.weekday, { end: e.target.value }),
                }),
                input({
                  class: "input day-note",
                  value: d.note || "",
                  maxlength: 200,
                  placeholder: "Note (optional)",
                  id: `day-note-${d.weekday}`,
                  "aria-label": `${WEEKDAYS[d.weekday]} note`,
                  onchange: (e) => update(d.weekday, { note: e.target.value }),
                }),
              )
            : null,
        ),
      ),
    );
    const v = validateWeek(rows);
    const working = rows.filter((r) => r.available).length;
    const std = isStandard(rows);
    summary.textContent = `${working} working day${working === 1 ? "" : "s"} · ${v.hours.toFixed(v.hours % 1 ? 1 : 0)} hours a week${std ? " · standard week" : ""}`;
    replace(
      errors,
      v.ok
        ? null
        : callout(
            "error",
            "Please check the times",
            h(
              "ul",
              {},
              v.errors.map((e) => h("li", {}, e)),
            ),
          ),
    );
    stdBtn.disabled = std;
  }

  const stdBtn = button("Standard week (Mon–Fri 9–5)", {
    id: "btn-week-standard",
    onclick: () => {
      rows = standardWeek().map((s) => ({
        ...s,
        note: rows.find((r) => r.weekday === s.weekday)?.note || "",
      }));
      paint();
      onChange?.(rows);
    },
  });
  const copyBtn = button("Copy Monday's hours to all days", {
    id: "btn-week-copy",
    onclick: () => {
      const monday = rows[0];
      rows = rows.map((r) => (r.available ? { ...r, start: monday.start, end: monday.end } : r));
      paint();
      onChange?.(rows);
    },
  });

  const el = h(
    "div",
    { class: "week-editor" },
    h("div", { class: "week-tools" }, stdBtn, copyBtn),
    errors,
    list,
    summary,
  );
  paint();
  return {
    el,
    rows: () => rows,
    setRows(next) {
      rows = next.map((r) => ({ ...r }));
      paint();
    },
    validate: () => validateWeek(rows),
  };
}

export function isStandard(rows) {
  const std = standardWeek();
  return rows.every((r) => {
    const s = std[r.weekday];
    return r.available === s.available && (!r.available || (r.start === s.start && r.end === s.end));
  });
}

/** API rows ({ start_time: "09:00:00" }) → editor rows. */
export function fromApi(days) {
  return days.map((d) => ({
    weekday: d.weekday,
    available: d.available,
    start: d.start_time.slice(0, 5),
    end: d.end_time.slice(0, 5),
    note: d.note || "",
  }));
}

export function toApi(rows) {
  return rows.map((r) => ({
    weekday: r.weekday,
    available: r.available,
    start_time: r.start,
    end_time: r.end,
    note: r.note?.trim() || null,
  }));
}
