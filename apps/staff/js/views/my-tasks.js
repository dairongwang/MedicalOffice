import { api } from "../api.js";
import {
  TASK_FILTERS,
  TASK_STATUS,
  groupTasksByDay,
  reportProblem,
  taskCounts,
  taskLine,
  taskTime,
} from "../assistant-tasks.js";
import { session } from "../auth.js";
import { addDays, ageOn, answerText, checkInLine, dayIn, officeDateTime, quickPeriod } from "../staff-appointments.js";
import { badge, button, callout, describeError, field, h, input, render, select, spinner, toast } from "../ui.js";
import { taskReviewPanel } from "./task-review.js";

/**
 * Signed-in medical assistant: the tasks the office assigned them — one per appointment to
 * prepare — grouped by the visit's day. For each task: take it; "Review & complete" (task-review.js):
 * the vital signs typed in, the medication and allergy lists — prepopulated from the chart and the
 * patient's check-in answers — cleared item by item with a note each, the check-in answers reviewed,
 * the provider told; or report that they cannot do it (why), so the office can assign someone else.
 * Times are in the office's time zone.
 *
 * Route: #/my-tasks  ·  #/my-tasks?from=YYYY-MM-DD&to=YYYY-MM-DD&status=open|assigned|accepted|…
 */
export function myTasksView(params) {
  const root = h("section", { class: "page wide", id: "my-tasks" });
  void renderTasks(root, params);
  return root;
}

const hashFor = (f) => {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(f)) if (v) q.set(k, v);
  const s = q.toString();
  return `#/my-tasks${s ? "?" + s : ""}`;
};

async function renderTasks(root, params) {
  const staff = session.staff;
  const tz = staff?.timezone || "UTC";
  const today = dayIn(new Date(), tz);
  const iso = (v) => (/^\d{4}-\d{2}-\d{2}$/.test(v || "") ? v : "");
  const filters = {
    from: iso(params.from) || today,
    to: iso(params.to) || addDays(iso(params.from) || today, 13),
    status: TASK_FILTERS.some(([v]) => v && v === params.status) ? params.status : "",
  };
  const w = staff?.worker;

  const from = input({ type: "date", value: filters.from, id: "tasks-from" });
  const to = input({ type: "date", value: filters.to, id: "tasks-to" });
  const status = select(TASK_FILTERS, { id: "tasks-status", value: filters.status });
  const apply = () => (location.hash = hashFor({ from: from.value, to: to.value, status: status.value }));
  for (const el of [from, to, status]) el.addEventListener("change", apply);
  const quick = (label, kind) => {
    const p = quickPeriod(kind, today);
    const active = p.from === filters.from && p.to === filters.to;
    return button(label, {
      class: `btn chip ${active ? "active" : ""}`,
      id: `tasks-period-${kind}`,
      "aria-pressed": String(active),
      onclick: () => (location.hash = hashFor({ ...filters, ...p })),
    });
  };
  const summary = h("p", { class: "muted small", id: "tasks-summary" });

  render(
    root,
    h(
      "header",
      { class: "page-header" },
      h(
        "div",
        {},
        h("h2", {}, "My tasks"),
        h(
          "p",
          { class: "muted small", id: "tasks-who" },
          w ? `${session.displayName} · ${w.staff_number} · medical assistant · ` : "",
          `the visits the office assigned you to prepare. Times are in the office's time zone (${tz}).`,
        ),
      ),
      button("Refresh", { class: "btn", id: "btn-tasks-refresh", onclick: () => void load() }),
    ),
    h(
      "div",
      { class: "chips", role: "group", "aria-label": "Period" },
      quick("Today", "today"),
      quick("Next 7 days", "7d"),
      quick("Next 14 days", "14d"),
      quick("Past week", "past"),
    ),
    h(
      "form",
      {
        class: "agenda-filters",
        id: "tasks-filters",
        onsubmit: (e) => {
          e.preventDefault();
          apply();
        },
      },
      field("From", from),
      field("To", to),
      field("Status", status),
    ),
    summary,
  );
  const list = h("div", { id: "tasks-list" }, spinner());
  root.append(list);
  await load();

  async function load() {
    render(list, spinner());
    const q = new URLSearchParams({ from: filters.from, to: filters.to });
    if (filters.status) q.set("status", filters.status);
    let page;
    try {
      page = await api.get(`/staff/me/tasks?${q}`);
    } catch (e) {
      render(list, callout("error", "Could not load your tasks", h("span", { id: "tasks-error" }, describeError(e))));
      return;
    }
    const counts = taskCounts(page.items);
    summary.textContent =
      `${page.total} task${page.total === 1 ? "" : "s"} from ${page.date_from} to ${page.date_to}` +
      (page.total ? ` · ${counts.new} new · ${counts.taken} taken · ${counts.completed} completed` : "") +
      (counts.reported ? ` · ${counts.reported} reported` : "");
    render(list);
    if (!page.items.length) {
      list.append(h("p", { class: "empty", id: "tasks-empty" }, "No tasks in this period."));
      return;
    }
    const ctx = { duties: page.duties, reasons: page.report_reasons, reload: load };
    for (const group of groupTasksByDay(page.items)) {
      list.append(
        h("h3", { class: "agenda-day" }, group.day, h("span", { class: "muted small" }, ` · ${group.items.length}`)),
        h(
          "ul",
          { class: "rows" },
          group.items.map((t) => taskRow(t, ctx)),
        ),
      );
    }
  }
}

/** One task with its actions; replaces itself with the server's answer after an action. */
function taskRow(t, ctx) {
  const a = t.appointment;
  const [label, kind] = TASK_STATUS[t.status] || [t.status, ""];
  const line = taskLine(t);
  const check = a.status === "scheduled" ? checkInLine({ ...a, timezone: a.timezone }) : { text: "" };
  const age = ageOn(a.patient.date_of_birth);
  const msg = h("div", { class: "check-live", id: `task-msg-${t.id}` });
  const panel = h("div", { class: "task-panel", id: `task-panel-${t.id}`, hidden: true });
  const actions = h("div", { class: "actions task-actions" });
  const row = h(
    "li",
    { class: `row task ${t.status}`, id: `task-${t.id}`, dataset: { id: String(t.id), state: line.state } },
    h(
      "div",
      { class: "row-main" },
      h("strong", { class: "mono" }, taskTime(t)),
      h("strong", {}, `${a.patient.first_name} ${a.patient.last_name}`),
      h(
        "span",
        { class: "muted small" },
        ` ${a.patient.mrn}${age !== null ? ` · ${age} y` : ""} · with ${a.provider_name}`,
      ),
      badge(label, kind),
      a.status !== "scheduled" ? badge(`visit ${a.status.replace("_", " ")}`, "muted") : null,
      a.checked_in_at ? badge("✓ patient checked in", "ok") : null,
    ),
    h("div", { class: "muted small" }, h("span", { class: "mono" }, a.number), a.reason ? ` · ${a.reason}` : ""),
    t.instructions
      ? h("div", { class: "small task-instructions" }, h("strong", {}, "From the office: "), t.instructions)
      : null,
    h("div", { class: `small task-line ${line.state}`, id: `task-line-${t.id}` }, line.text),
    check.text && t.status !== "canceled" ? h("div", { class: "muted small" }, check.text) : null,
    t.vitals?.text ? h("div", { class: "small task-vitals" }, h("strong", {}, "Vital signs: "), t.vitals.text) : null,
    t.status === "completed" && t.completion_note
      ? h("div", { class: "small" }, h("strong", {}, "Your note: "), t.completion_note)
      : null,
    actions,
    msg,
    panel,
  );

  const busy = (on) => {
    for (const b of actions.querySelectorAll("button")) b.disabled = on;
  };
  const post = async (path, body, done) => {
    msg.replaceChildren();
    busy(true);
    try {
      await api.post(`/staff/me/tasks/${t.id}/${path}`, body);
      toast(done);
      await ctx.reload(); // the counts, the grouping and the status filter stay right
    } catch (e) {
      msg.append(callout("error", "That did not work", h("span", {}, describeError(e))));
      busy(false);
    }
  };

  if (t.can_accept)
    actions.append(
      button("Take task", {
        class: "btn small-btn primary",
        id: `btn-task-accept-${t.id}`,
        title: "Tell the office you are on it",
        onclick: () => void post("accept", {}, `${a.number}: task taken`),
      }),
    );
  if (t.status === "accepted")
    actions.append(
      button("Review & complete…", {
        class: `btn small-btn ${t.can_complete ? "primary" : ""}`,
        id: `btn-task-complete-${t.id}`,
        disabled: !t.can_complete,
        title: t.can_complete
          ? "Vital signs, the medication and allergy lists, the check-in answers — then complete the task"
          : line.text,
        onclick: () => openPanel(panel, taskReviewPanel(t, { onCompleted: () => void ctx.reload() })),
      }),
    );
  if (t.can_report)
    actions.append(
      button("Can't do it", {
        class: "btn small-btn danger",
        id: `btn-task-report-${t.id}`,
        title: "Tell the office you cannot do this task — it assigns someone else",
        onclick: () => openPanel(panel, reportForm(t, ctx, post)),
      }),
    );
  if (t.has_check_in_answers && (t.status === "accepted" || t.status === "completed"))
    actions.append(
      button("Check-in answers", {
        class: "btn small-btn",
        id: `btn-task-answers-${t.id}`,
        title: "What the patient answered on the check-in page — review it with them",
        onclick: () => void showAnswers(t, panel),
      }),
    );
  if (!actions.childElementCount) actions.hidden = true;
  return row;
}

function openPanel(panel, content) {
  if (!panel.hidden && panel.dataset.kind === content.dataset.kind) {
    panel.hidden = true;
    return;
  }
  panel.dataset.kind = content.dataset.kind;
  render(panel, content);
  panel.hidden = false;
  panel.querySelector("input, select, textarea")?.focus();
}

/** "I can't do it": why (+ a note) — goes straight to the office. */
function reportForm(t, ctx, post) {
  const reason = select([["", "Choose…", { disabled: true }], ...ctx.reasons.map((r) => [r.key, r.label])], {
    id: `task-${t.id}-reason`,
    value: "",
  });
  const note = h("textarea", {
    class: "input",
    id: `task-${t.id}-report-note`,
    rows: 2,
    maxlength: 1000,
    placeholder: "What the office should know (required for “Other”)",
  });
  const problem = h("p", { class: "hint", id: `task-${t.id}-report-problem` });
  return h(
    "form",
    {
      class: "form task-form",
      dataset: { kind: "report" },
      onsubmit: (e) => {
        e.preventDefault();
        const why = reportProblem(reason.value, note.value);
        problem.textContent = why || "";
        if (why) return;
        if (!confirm(`Tell the office you cannot prepare ${t.appointment.number}? It will assign someone else.`))
          return;
        void post(
          "report",
          { reason: reason.value, note: note.value.trim() || null },
          `${t.appointment.number}: the office was told — it will reassign the task`,
        );
      },
    },
    h("strong", {}, "Why can't you do this task?"),
    field("Reason", reason),
    field("Note", note, { optional: true }),
    problem,
    h(
      "div",
      { class: "actions" },
      button("Send to the office", { class: "btn danger", type: "submit", id: `btn-task-${t.id}-send-report` }),
    ),
  );
}

async function showAnswers(t, panel) {
  const content = h("div", { dataset: { kind: "answers" } }, spinner());
  openPanel(panel, content);
  if (panel.hidden) return;
  let out;
  try {
    out = await api.get(`/staff/me/tasks/${t.id}/check-in`);
  } catch (e) {
    render(content, callout("error", "Could not load the answers", h("span", {}, describeError(e))));
    return;
  }
  render(
    content,
    h(
      "dl",
      { class: "record" },
      out.questions
        .filter((q) => q.id in out.answers)
        .map((q) => h("div", { class: "wide" }, h("dt", {}, q.label), h("dd", {}, answerText(q, out.answers[q.id])))),
    ),
    h(
      "p",
      { class: "muted small" },
      `Signed by ${out.signer_name || "the patient"} on ${officeDateTime(out.signed_at || out.completed_at, t.appointment.timezone)}`,
    ),
  );
}
