import { api } from "../api.js";
import {
  append,
  badge,
  button,
  callout,
  describeError,
  field,
  h,
  input,
  render,
  select,
  spinner,
  toast,
} from "../ui.js";
import {
  ASSISTANT_FILTERS,
  STATUS_LABELS,
  addDays,
  answerText,
  assistantBadge,
  assistantSummary,
  reviewDecision,
  checkInSummary,
  officeDateTime,
  staffNotifiedText,
  describeSentTo,
  hashFor as hashOf,
  isoDate,
  roleLabel,
  splitLocal,
} from "../format.js";

/**
 * Administrators: the agenda — every appointment patients booked on the main app's Patients
 * tab, grouped by day, filterable by period, provider, status, check-in and patient. An
 * administrator can **check the patient in** (any time while the visit is scheduled — patients
 * check themselves in from the app only within the 24 hours before) or undo it, mark a visit
 * completed / no-show, cancel it (the slot frees up), re-schedule a canceled one, leave a note,
 * and copy / re-send the patient's link. **Medical assistants**: the office assigns an assistant who
 * is available for the visit (the list says who is and why the others are not), reassigns when the
 * assistant reports they cannot do it — a banner counts those — or takes the assistant off.
 * Route: #/appointments  ·  #/appointments?from=&to=&provider=&status=&checkin=yes|no&assistant=&q=&patient=<id>
 */
export function appointmentsView(params) {
  const root = h("section", { class: "page wide" });
  void renderAgenda(root, params);
  return root;
}

export function statusBadgeOf(status) {
  const [label, kind] = STATUS_LABELS[status] || [status, ""];
  return badge(label, kind);
}

const hashFor = (filters) => hashOf("appointments", filters);

async function renderAgenda(root, params) {
  let nextStart = null; // the earliest upcoming appointment on the loaded agenda (for the reminder simulation)
  const today = isoDate(new Date());
  const filters = {
    from: params.from || today,
    to: params.to || addDays(params.from || today, 6),
    provider: params.provider || "",
    status: params.status || "",
    checkin: params.checkin === "yes" || params.checkin === "no" ? params.checkin : "",
    assistant: ASSISTANT_FILTERS.some(([v]) => v && v === params.assistant) ? params.assistant : "",
    q: params.q || "",
    patient: params.patient || "",
  };

  let providers = [];
  let officeZone = "";
  try {
    const list = await api.get("/appointments/providers", { auth: false });
    providers = list.providers;
    officeZone = list.timezone || "";
  } catch {
    /* the filter simply stays empty */
  }

  const fromInput = input({ type: "date", value: filters.from, id: "agenda-from" });
  const toInput = input({ type: "date", value: filters.to, id: "agenda-to" });
  const providerSelect = select(
    [
      ["", "Every provider"],
      ...providers.map((p) => [String(p.id), `${p.first_name} ${p.last_name} (${roleLabel(p.role)})`]),
    ],
    { id: "agenda-provider", value: filters.provider },
  );
  const statusSelect = select(
    [
      ["", "Every status"],
      ["scheduled", "Scheduled"],
      ["completed", "Completed"],
      ["no_show", "No-show"],
      ["canceled", "Canceled"],
    ],
    { id: "agenda-status", value: filters.status },
  );
  const checkinSelect = select(
    [
      ["", "Any"],
      ["yes", "Checked in"],
      ["no", "Not checked in"],
    ],
    { id: "agenda-checkin", value: filters.checkin },
  );
  const assistantSelect = select(ASSISTANT_FILTERS, { id: "agenda-assistant", value: filters.assistant });
  const search = input({
    type: "search",
    placeholder: "Patient name, P-000042 or APT-000007",
    value: filters.q,
    id: "agenda-search",
  });
  const apply = () =>
    (location.hash = hashFor({
      from: fromInput.value,
      to: toInput.value,
      provider: providerSelect.value,
      status: statusSelect.value,
      checkin: checkinSelect.value,
      assistant: assistantSelect.value,
      q: search.value.trim(),
      patient: filters.patient,
    }));
  for (const el of [fromInput, toInput, providerSelect, statusSelect, checkinSelect, assistantSelect, search])
    el.addEventListener("change", apply);

  const quick = (label, from, to, id) =>
    button(label, {
      class: `btn chip ${filters.from === from && filters.to === to ? "active" : ""}`,
      id,
      onclick: () => (location.hash = hashFor({ ...filters, from, to })),
    });

  render(
    root,
    h(
      "header",
      { class: "page-header" },
      h(
        "div",
        {},
        h("h2", {}, "Appointments"),
        h(
          "p",
          { class: "muted small" },
          `What patients booked on the Patients tab of the main app. Times are in the office's time zone${officeZone ? ` (${officeZone})` : ""}.`,
        ),
      ),
    ),
    filters.patient
      ? callout(
          "info",
          `Showing one patient's appointments (record ${formatPatientNumber(filters.patient)})`,
          " ",
          h("a", { href: `#/patients/${filters.patient}`, id: "agenda-patient-link" }, "Open the patient record"),
          " · ",
          h("a", { href: hashFor({ ...filters, patient: "" }), id: "agenda-everyone" }, "Everyone"),
        )
      : null,
    h(
      "div",
      { class: "chips", role: "group", "aria-label": "Period" },
      quick("Today", today, today, "agenda-today"),
      quick("Next 7 days", today, addDays(today, 6), "agenda-week"),
      quick("Next 30 days", today, addDays(today, 29), "agenda-month"),
    ),
    reassignBanner(today),
    remindersControl(
      () => nextStart,
      () => loadList(),
    ),
    h(
      "form",
      {
        class: "agenda-filters",
        onsubmit: (e) => {
          e.preventDefault();
          apply();
        },
      },
      field("From", fromInput),
      field("To", toInput),
      field("Provider", providerSelect),
      field("Status", statusSelect),
      field("Check-in", checkinSelect),
      field("Assistant", assistantSelect),
      field("Search", search),
    ),
  );
  const list = h("div", { id: "agenda-list" }, spinner());
  root.append(list);
  await loadList();

  async function loadList() {
    render(list, spinner());
    const query = new URLSearchParams({ from: filters.from, to: filters.to, limit: "500" });
    if (filters.patient) query.set("patient_id", filters.patient);
    if (filters.provider) query.set("provider_id", filters.provider);
    if (filters.status) query.set("status", filters.status);
    if (filters.checkin) query.set("checked_in", filters.checkin === "yes" ? "true" : "false");
    if (filters.assistant) query.set("assistant", filters.assistant);
    if (filters.q) query.set("q", filters.q);
    let page;
    try {
      page = await api.get(`/appointments?${query}`);
    } catch (e) {
      render(list, callout("error", "Could not load the agenda", h("span", {}, describeError(e))));
      return;
    }
    const upcoming = page.items.filter((a) => a.status === "scheduled" && new Date(a.start_at) > new Date());
    nextStart = upcoming.length ? upcoming[0].start_at : null;
    render(list, h("p", { class: "muted small" }, `${page.total} appointment${page.total === 1 ? "" : "s"}`));
    if (!page.items.length) {
      list.append(h("p", { class: "empty" }, "No appointments in this period."));
      return;
    }
    const byDay = new Map();
    for (const a of page.items) {
      const { day } = splitLocal(a.start_local);
      if (!byDay.has(day)) byDay.set(day, []);
      byDay.get(day).push(a);
    }
    for (const [day, items] of byDay) {
      list.append(
        h("h3", { class: "agenda-day" }, day, h("span", { class: "muted small" }, ` · ${items.length}`)),
        h(
          "ul",
          { class: "rows" },
          items.map((a) => appointmentRow(a)),
        ),
      );
    }
  }
}

/** One agenda row with its actions. ``showDay``: print the full date (lists not grouped by day). */
export function appointmentRow(a, options = {}) {
  const { time } = splitLocal(a.start_local);
  const when = options.showDay ? a.start_local : time;
  const msg = h("div", { class: "check-live" });
  const row = h("li", { class: `row appointment ${a.status}`, dataset: { id: String(a.id) } });
  const actions = h("div", { class: "row-meta" });

  const update = async (body, done) => {
    msg.replaceChildren();
    for (const b of actions.querySelectorAll("button")) b.disabled = true;
    try {
      const updated = await api.patch(`/appointments/${a.id}`, body);
      toast(done);
      row.replaceWith(appointmentRow(updated, options));
    } catch (e) {
      msg.append(callout("error", "Could not update", h("span", {}, describeError(e))));
      for (const b of actions.querySelectorAll("button")) b.disabled = false;
    }
  };

  /** The desk starts the check-in: the link goes to the patient — or the server says why it could not. */
  const startCheckIn = async () => {
    msg.replaceChildren();
    for (const b of actions.querySelectorAll("button")) b.disabled = true;
    try {
      const updated = await api.patch(`/appointments/${a.id}`, { checked_in: true });
      const c = updated.check_in;
      if (c?.status === "failed") toast(`Check-in failed: ${c.failure_reason}`, { error: true });
      else
        toast(
          `Check-in link sent to ${a.patient.first_name} ${a.patient.last_name} by ` +
            `${(c?.link_channels || []).map((x) => (x === "sms" ? "text" : "e-mail")).join(" and ")} — valid 60 minutes`,
        );
      row.replaceWith(appointmentRow(updated, options));
    } catch (e) {
      msg.append(callout("error", "Could not start the check-in", h("span", {}, describeError(e))));
      for (const b of actions.querySelectorAll("button")) b.disabled = false;
    }
  };

  if (a.status === "scheduled") {
    // Check-in: the patient signs the check-in page (questionnaire + the estimate not covered by
    // insurance), then the estimate is pre-authorized. The desk starts it any time by sending the
    // page's link to the patient — to be completed within 60 minutes — or calls it off / undoes it.
    const { state } = checkInSummary(a);
    const sendLink = (label, id) =>
      button(label, {
        class: `btn small-btn ${state === "link-sent" ? "" : "primary"}`,
        id,
        title:
          "Send the check-in page to the patient's e-mail / phone: questions about the visit and the estimated cost not covered by insurance, both signed — within 60 minutes",
        onclick: () => startCheckIn(),
      });
    if (!a.checked_in_at) {
      if (state === "link-sent") {
        actions.append(
          sendLink("Resend check-in link", `btn-agenda-resend-checkin-${a.id}`),
          button("Cancel check-in", {
            class: "btn small-btn",
            id: `btn-agenda-cancel-checkin-${a.id}`,
            title: "Call the pending check-in off — the link stops working",
            onclick: () => {
              if (confirm(`Call off the check-in link sent for ${a.number}?`))
                void update({ checked_in: false }, `Check-in of ${a.number} called off`);
            },
          }),
        );
      } else {
        actions.append(
          sendLink(
            state === "failed" ? "Send check-in link again" : "Check in (send link)",
            `btn-agenda-checkin-${a.id}`,
          ),
        );
      }
    } else
      actions.append(
        button("Undo check-in", {
          class: "btn small-btn",
          id: `btn-agenda-undo-checkin-${a.id}`,
          title: "Take the check-in back (wrong patient, the patient left, …) — its pre-authorization is released",
          onclick: () => {
            if (confirm(`Undo the check-in of ${a.patient.first_name} ${a.patient.last_name} for ${a.number}?`))
              void update({ checked_in: false }, `Check-in of ${a.number} undone`);
          },
        }),
      );
    if (!a.confirmed_at)
      actions.append(
        button("Mark confirmed", {
          class: "btn small-btn",
          title: "The patient confirmed by phone / at the desk",
          onclick: () => update({ confirmed: true }, `${a.number} marked confirmed`),
        }),
      );
    actions.append(
      button("Completed", {
        class: "btn small-btn",
        title: "The visit took place: the copay / hold is charged and the visit summary goes to the doctor to sign",
        onclick: () =>
          update({ status: "completed" }, `${a.number} marked completed — the visit summary is waiting for the doctor`),
      }),
      button("No-show", {
        class: "btn small-btn",
        onclick: () => update({ status: "no_show" }, `${a.number} marked as no-show`),
      }),
      button("Cancel", {
        class: "btn small-btn danger",
        onclick: () => {
          if (confirm(`Cancel ${a.number} (${a.patient.first_name} ${a.patient.last_name}, ${a.start_local})?`))
            void update({ status: "canceled" }, `${a.number} canceled — the slot is free again`);
        },
      }),
    );
  } else {
    actions.append(
      button("Re-schedule", {
        class: "btn small-btn",
        title: "Put it back on the agenda (the slot must still be free)",
        onclick: () => update({ status: "scheduled" }, `${a.number} is scheduled again`),
      }),
    );
  }
  const noteBtn = button(a.notes ? "Edit note" : "Add note", {
    class: "btn small-btn",
    onclick: () => {
      const notes = prompt(`Note for ${a.number} (staff only):`, a.notes || "");
      if (notes !== null) void update({ notes: notes.trim() }, "Note saved");
    },
  });
  actions.append(noteBtn);
  const assistantPanel = h("div", { class: "assistant-panel", id: `agenda-assistant-panel-${a.id}`, hidden: true });
  const ma = a.assistant;
  if (a.status === "scheduled" && ma?.status !== "completed") {
    const reported = ma?.status === "reported";
    actions.append(
      button(!ma ? "Assign assistant" : reported ? "Reassign assistant" : "Change assistant", {
        class: `btn small-btn ${reported || !ma ? "primary" : ""}`,
        id: `btn-agenda-assign-${a.id}`,
        title: "Choose a medical assistant who is available for this visit",
        onclick: () =>
          void toggleAssignPanel(a, assistantPanel, (updated, done) => {
            toast(done);
            row.replaceWith(appointmentRow(updated, options));
          }),
      }),
    );
    if (ma && !reported)
      actions.append(
        button("Unassign", {
          class: "btn small-btn",
          id: `btn-agenda-unassign-${a.id}`,
          title: "Take the medical assistant off this visit (they are told)",
          onclick: async () => {
            if (!confirm(`Take ${ma.name} off ${a.number}?`)) return;
            msg.replaceChildren();
            try {
              const updated = await api.delete(`/appointments/${a.id}/assistant`);
              toast(`${ma.name} is no longer on ${a.number}`);
              row.replaceWith(appointmentRow(updated, options));
            } catch (e) {
              msg.append(callout("error", "Could not unassign", h("span", {}, describeError(e))));
            }
          },
        }),
      );
  }
  if (ma)
    actions.append(
      button("Task history", {
        class: "btn small-btn",
        id: `btn-agenda-tasks-${a.id}`,
        title: "Every medical-assistant task of this visit: assigned, taken, completed, reported, withdrawn",
        onclick: () => void toggleTaskHistory(a, assistantPanel),
      }),
    );
  const vs = a.visit_summary;
  if (vs) {
    actions.append(
      button(vs.status === "signed" ? "Print summary" : "Summary draft (PDF)", {
        class: "btn small-btn",
        id: `btn-agenda-summary-pdf-${a.id}`,
        title:
          vs.status === "signed"
            ? "The visit summary the doctor signed — print it for the patient (recorded)"
            : "The draft waiting for the doctor's signature (prints as DRAFT)",
        onclick: async () => {
          const tab = window.open("", "_blank"); // now, in the click — a later window.open would be blocked
          try {
            const url = await api.blobUrl(`/api/v1/appointments/${a.id}/visit-summary/pdf`);
            if (tab) tab.location.href = url;
            else location.href = url;
            setTimeout(() => URL.revokeObjectURL(url), 60_000);
          } catch (e) {
            tab?.close();
            toast(describeError(e), { error: true });
          }
        },
      }),
    );
    if (vs.status === "signed")
      actions.append(
        button("Send summary", {
          class: "btn small-btn",
          id: `btn-agenda-summary-send-${a.id}`,
          title: "E-mail the signed visit summary (PDF) to the patient again and text them that it is ready",
          onclick: async () => {
            msg.replaceChildren();
            try {
              const out = await api.post(`/appointments/${a.id}/visit-summary/send`);
              toast(
                out.sent_channels.length
                  ? `Visit summary sent by ${out.sent_channels.map((x) => (x === "sms" ? "text" : "e-mail")).join(" and ")}`
                  : `Not sent: ${out.delivery_note || "no e-mail address or phone number on the record"} — print it instead`,
                { error: !out.sent_channels.length },
              );
              row.replaceWith(appointmentRow(await api.get(`/appointments/${a.id}`), options));
            } catch (e) {
              msg.append(callout("error", "Could not send the summary", h("span", {}, describeError(e))));
            }
          },
        }),
      );
  }
  const record = h("div", { class: "checkin-record", id: `agenda-checkin-record-${a.id}`, hidden: true });
  if (a.check_in || a.checked_in_at) {
    actions.append(
      button("Check-in record", {
        class: "btn small-btn",
        id: `btn-agenda-checkin-record-${a.id}`,
        title: "The answers, both signatures, the estimate and the pre-authorization — or why the check-in failed",
        onclick: () => void toggleCheckInRecord(a, record),
      }),
    );
  }
  if (a.link) {
    actions.append(
      button("Copy link", {
        class: "btn small-btn",
        title: "Copy the patient's appointment link (review · add to calendar · cancel) to hand it over by hand",
        onclick: async () => {
          try {
            await navigator.clipboard.writeText(a.link.url);
            toast("Link copied");
          } catch {
            prompt("The patient's appointment link:", a.link.url);
          }
        },
      }),
      button("Resend link", {
        class: "btn small-btn",
        id: `btn-agenda-resend-${a.id}`,
        title: "Send the link again by e-mail / text message to what is on the patient's record",
        onclick: async () => {
          msg.replaceChildren();
          for (const b of actions.querySelectorAll("button")) b.disabled = true;
          try {
            const updated = await api.post(`/appointments/${a.id}/send-link`);
            const sent = updated.link?.channels?.length
              ? `Link sent by ${updated.link.channels.join(" and ")}` +
                (updated.link.fake ? " (built-in fake — see the test inbox)" : "")
              : "The link could not be sent — copy it and hand it over instead";
            toast(sent, { error: !updated.link?.channels?.length });
            row.replaceWith(appointmentRow(updated, options));
          } catch (e) {
            msg.append(callout("error", "Could not send", h("span", {}, describeError(e))));
            for (const b of actions.querySelectorAll("button")) b.disabled = false;
          }
        },
      }),
    );
  }

  append(
    row,
    h(
      "div",
      { class: "row-main" },
      h("strong", { class: "mono" }, when),
      h(
        "a",
        { href: `#/patients/${a.patient.id}`, class: "agenda-patient" },
        h("strong", {}, `${a.patient.first_name} ${a.patient.last_name}`),
      ),
      h(
        "span",
        { class: "muted small" },
        ` ${a.patient.mrn} · with ${a.provider.first_name} ${a.provider.last_name} (${roleLabel(a.provider.role)})`,
      ),
      statusBadgeOf(a.status),
      !a.checked_in_at && checkInSummary(a).state === "failed"
        ? h(
            "span",
            { class: "badge bad agenda-checkin-failed", id: `agenda-checkin-failed-${a.id}` },
            "check-in failed",
          )
        : null,
      !a.checked_in_at && checkInSummary(a).state === "link-sent"
        ? h("span", { class: "badge warn", id: `agenda-checkin-pending-${a.id}` }, "check-in link sent")
        : null,
      a.checked_in_at
        ? h(
            "span",
            { class: "badge ok agenda-checked-in", id: `agenda-checked-in-${a.id}`, title: checkInSummary(a).text },
            "✓ checked in",
          )
        : null,
      a.status === "scheduled" && a.confirmed_at && !a.checked_in_at ? badge("✓ confirmed", "ok") : null,
      a.assistant
        ? h(
            "span",
            {
              class: `badge ${assistantSummary(a).kind} agenda-assistant-badge`,
              id: `agenda-assistant-badge-${a.id}`,
              title: assistantSummary(a).text,
            },
            assistantBadge(a),
          )
        : null,
      a.visit_summary
        ? h(
            "span",
            {
              class: `badge ${a.visit_summary.status === "signed" ? "ok" : "warn"} agenda-summary-badge`,
              id: `agenda-summary-badge-${a.id}`,
              title: summaryText(a),
            },
            a.visit_summary.status === "signed" ? "summary signed" : "summary to sign",
          )
        : null,
      a.payment === "self_pay"
        ? h(
            "span",
            {
              class: "badge warn",
              title: `Self-pay: the coverage check at booking said "${a.coverage_status || "none"}" and the patient agreed to pay for the visit`,
            },
            "self-pay",
          )
        : null,
    ),
    h(
      "div",
      { class: "muted small" },
      h("span", { class: "mono" }, a.number),
      a.reason ? ` · ${a.reason}` : "",
      a.notes ? h("span", { class: "agenda-note" }, ` · Note: ${a.notes}`) : "",
    ),
    checkInStatus(a),
    assistantStatus(a),
    linkStatus(a),
    reminderStatus(a),
    a.visit_summary
      ? h(
          "div",
          { class: "small agenda-summary", id: `agenda-summary-${a.id}`, dataset: { state: a.visit_summary.status } },
          summaryText(a),
        )
      : null,
    actions,
    msg,
    record,
    assistantPanel,
  );
  return row;
}

/** "Jamie Helper took the task on …" / "… cannot do it (Sick) — assign someone else". */
function assistantStatus(a) {
  const { state, text } = assistantSummary(a);
  if (!text) return null;
  return h(
    "div",
    { class: `small agenda-assistant ${state}`, id: `agenda-assistant-${a.id}`, dataset: { state } },
    text,
    a.assistant?.instructions ? h("span", { class: "muted" }, ` · Instructions: ${a.assistant.instructions}`) : null,
  );
}

/** Warn when assistants reported tasks they cannot do: those visits need someone else. */
function reassignBanner(today) {
  const box = h("div", { id: "agenda-reassign-banner" });
  void (async () => {
    let board;
    try {
      board = await api.get("/assignments?status=needs_reassignment&limit=1");
    } catch {
      return; // the agenda still works without the banner
    }
    if (!board.needs_reassignment) return;
    const n = board.needs_reassignment;
    box.append(
      callout(
        "warn",
        `${n} visit${n === 1 ? " needs" : "s need"} a new medical assistant`,
        " The assistant reported they cannot do the task. ",
        h(
          "a",
          { href: hashFor({ from: today, to: addDays(today, 89), assistant: "reported" }), id: "agenda-reassign-link" },
          "Show them",
        ),
      ),
    );
  })();
  return box;
}

/**
 * The assign / reassign panel: every medical assistant with their availability for this visit —
 * in that day, within their hours, not with another patient then — unavailable ones disabled with
 * the reason; optional instructions; the server checks the same rules.
 */
async function toggleAssignPanel(a, panel, onAssigned) {
  if (!panel.hidden && panel.dataset.kind === "assign") {
    panel.hidden = true;
    return;
  }
  panel.dataset.kind = "assign";
  panel.hidden = false;
  render(panel, spinner("Checking who is available…"));
  let opts;
  try {
    opts = await api.get(`/appointments/${a.id}/assistants`);
  } catch (e) {
    render(panel, callout("error", "Could not load the medical assistants", h("span", {}, describeError(e))));
    return;
  }
  if (!opts.candidates.length) {
    render(
      panel,
      callout(
        "info",
        "No medical assistants on the roster",
        " Add staff with the role “Medical assistant” on the staff dashboard (Roster).",
      ),
    );
    return;
  }
  const name = `assistant-${a.id}`;
  const radios = [];
  const items = opts.candidates.map((c) => {
    const radio = h("input", {
      type: "radio",
      name,
      value: String(c.worker_id),
      id: `${name}-${c.worker_id}`,
      disabled: !c.available,
    });
    radios.push(radio);
    return h(
      "li",
      { class: `candidate ${c.available ? "available" : "unavailable"}`, dataset: { worker: String(c.worker_id) } },
      h(
        "label",
        { class: "checkbox", for: radio.id },
        radio,
        h(
          "span",
          {},
          h("strong", {}, `${c.first_name} ${c.last_name}`),
          h("span", { class: "muted small" }, ` ${c.staff_number}${c.hours ? ` · ${c.hours}` : ""}`),
          c.current ? badge("on this visit", "ok") : null,
          c.available ? badge("available", "ok") : h("span", { class: "small candidate-reason" }, ` — ${c.reason}`),
        ),
      ),
    );
  });
  const firstFree = radios.find((r) => !r.disabled);
  if (firstFree) firstFree.checked = true;
  const instructions = input({
    id: `${name}-instructions`,
    maxlength: 1000,
    placeholder: "Optional, e.g. “Room 2 — the patient uses a wheelchair”",
    value: a.assistant?.status !== "reported" ? a.assistant?.instructions || "" : "",
  });
  const problem = h("div", { class: "check-live" });
  const submit = button(a.assistant ? "Assign instead" : "Assign", {
    class: "btn primary",
    type: "submit",
    id: `btn-${name}-submit`,
    disabled: !firstFree,
  });
  render(
    panel,
    h(
      "form",
      {
        class: "form assign-form",
        onsubmit: async (e) => {
          e.preventDefault();
          const chosen = radios.find((r) => r.checked);
          if (!chosen) return;
          problem.replaceChildren();
          submit.disabled = true;
          try {
            const updated = await api.put(`/appointments/${a.id}/assistant`, {
              worker_id: Number(chosen.value),
              instructions: instructions.value.trim() || null,
            });
            onAssigned(
              updated,
              `${updated.assistant?.name || "The assistant"} assigned to ${a.number} — they were told`,
            );
          } catch (err) {
            problem.append(callout("error", "Could not assign", h("span", {}, describeError(err))));
            submit.disabled = false;
          }
        },
      },
      h("strong", {}, `Medical assistant for ${a.number} · ${opts.start_local} (${opts.timezone})`),
      a.assistant?.status === "reported"
        ? h("p", { class: "small agenda-assistant reported" }, assistantSummary(a).text)
        : null,
      h("ul", { class: "checklist candidates", id: `${name}-list` }, items),
      firstFree
        ? null
        : h(
            "p",
            { class: "hint" },
            "Nobody is available for this visit — change an assistant's week on the staff dashboard.",
          ),
      field("Instructions for the assistant", instructions, { optional: true }),
      h("div", { class: "actions" }, submit),
      problem,
    ),
  );
}

const TASK_STATUS = {
  assigned: ["Assigned", "warn"],
  accepted: ["Taken", "ok"],
  completed: ["Completed", "ok"],
  reported: ["Reported: can't do it", "bad"],
  canceled: ["Withdrawn", "muted"],
};

/** Every medical-assistant task of the visit, newest first. */
async function toggleTaskHistory(a, panel) {
  if (!panel.hidden && panel.dataset.kind === "history") {
    panel.hidden = true;
    return;
  }
  panel.dataset.kind = "history";
  panel.hidden = false;
  render(panel, spinner());
  let rows;
  try {
    rows = await api.get(`/appointments/${a.id}/assignments`);
  } catch (e) {
    render(panel, callout("error", "Could not load the task history", h("span", {}, describeError(e))));
    return;
  }
  const when = (iso) => (iso ? officeDateTime(iso, a.timezone) : "");
  render(
    panel,
    h("strong", {}, `Medical-assistant tasks of ${a.number}`),
    h(
      "ul",
      { class: "rows task-history" },
      rows.map((t) => {
        const [label, kind] = TASK_STATUS[t.status] || [t.status, ""];
        const steps = [
          `assigned ${when(t.assigned_at)}${t.assigned_by ? ` by ${t.assigned_by}` : ""}`,
          t.notified_to?.length
            ? `told by ${describeSentTo(t.notified_to)}`
            : t.notify_note
              ? `not told — ${t.notify_note}`
              : "",
          t.accepted_at ? `taken ${when(t.accepted_at)}` : "",
          t.completed_at ? `completed ${when(t.completed_at)}` : "",
          t.reported_at
            ? `reported ${when(t.reported_at)}: ${t.report_reason_label || t.report_reason}${t.report_note ? ` — “${t.report_note}”` : ""}`
            : "",
          t.resolved_at ? `settled ${when(t.resolved_at)}${t.resolved_by ? ` by ${t.resolved_by}` : ""}` : "",
          t.canceled_at ? `withdrawn ${when(t.canceled_at)}${t.cancel_reason ? ` (${t.cancel_reason})` : ""}` : "",
        ].filter(Boolean);
        return h(
          "li",
          { class: "row", dataset: { status: t.status, id: String(t.id) } },
          h(
            "div",
            { class: "row-main" },
            h("strong", {}, t.name),
            h("span", { class: "muted small" }, ` ${t.staff_number}`),
            badge(label, kind),
          ),
          h("div", { class: "muted small" }, steps.join(" · ")),
          t.instructions ? h("div", { class: "small" }, `Instructions: ${t.instructions}`) : null,
          t.vitals?.text
            ? h("div", { class: "small" }, `Vital signs: ${t.vitals.text}${t.vitals.note ? ` (${t.vitals.note})` : ""}`)
            : null,
          t.review_items?.length ? reviewList(t.review_items) : null,
          t.check_in_note ? h("div", { class: "small" }, `Check-in answers: ${t.check_in_note}`) : null,
          t.completion_note ? h("div", { class: "small" }, `Note: ${t.completion_note}`) : null,
        );
      }),
    ),
  );
}

/** "Checked in in the app (patient account) on …" / "Not checked in — … from …" — where check-in stands. */
function checkInStatus(a) {
  const { state, text } = checkInSummary(a);
  if (state === "none") return null;
  const notified = a.checked_in_at ? staffNotifiedText(a.check_in, a.timezone) : "";
  return h(
    "div",
    { class: "agenda-checkin-wrap" },
    h("div", { class: `muted small agenda-checkin ${state}`, id: `agenda-checkin-${a.id}`, dataset: { state } }, text),
    notified
      ? h(
          "div",
          {
            class: `muted small agenda-staff-notified ${a.check_in?.staff_notified_at ? "ok" : "bad"}`,
            id: `agenda-staff-notified-${a.id}`,
          },
          notified,
        )
      : null,
  );
}

const CHECK_IN_STATUS = {
  completed: ["Completed", "ok"],
  pending: ["Waiting for the patient", "warn"],
  failed: ["Failed", "bad"],
  canceled: ["Canceled", "muted"],
};

/** The check-in record: every attempt, newest first — answers, signatures, estimate, payment, why it failed. */
async function toggleCheckInRecord(a, panel) {
  if (!panel.hidden) {
    panel.hidden = true;
    return;
  }
  panel.hidden = false;
  render(panel, spinner());
  let rows;
  try {
    rows = await api.get(`/appointments/${a.id}/check-ins`);
  } catch (e) {
    render(panel, callout("error", "Could not load the check-in record", h("span", {}, describeError(e))));
    return;
  }
  if (!rows.length) {
    render(panel, h("p", { class: "empty" }, "No check-in yet."));
    return;
  }
  render(
    panel,
    rows.map((c, i) => checkInAttempt(c, i === 0, a.timezone)),
  );
}

function checkInAttempt(c, latest, tz) {
  const when = (iso) => officeDateTime(iso, tz);
  const [label, kind] = CHECK_IN_STATUS[c.status] || [c.status, ""];
  const money = (cents) => `$${(cents / 100).toFixed(2)}`;
  const signature = (title, name, at, png, ref) =>
    name
      ? h(
          "figure",
          { class: "checkin-signature" },
          png ? h("img", { src: png, alt: `${title}: signature of ${name}` }) : null,
          h("figcaption", { class: "muted small" }, `${title} — ${name}, ${when(at)}${ref ? ` · ${ref}` : ""}`),
        )
      : null;
  const answered = Object.keys(c.answers || {}).length;
  return h(
    "section",
    { class: `checkin-attempt ${latest ? "latest" : ""}`, dataset: { status: c.status, id: String(c.id) } },
    h(
      "div",
      { class: "row-main" },
      h("strong", {}, `Check-in #${c.id}`),
      badge(label, kind),
      h(
        "span",
        { class: "muted small" },
        ` started ${when(c.started_at)} · ${c.via === "staff" ? `link sent by ${c.started_by || "the office"}` : "by the patient in the app"}` +
          (c.link_sent_to?.length ? ` · to ${c.link_sent_to.join(", ")}` : "") +
          (c.opened_at ? ` · opened ${when(c.opened_at)}` : " · not opened"),
      ),
    ),
    c.failure_reason
      ? h("p", { class: c.status === "failed" ? "checkin-failure" : "muted small" }, c.failure_reason)
      : null,
    answered
      ? h(
          "dl",
          { class: "record checkin-answers" },
          c.questions
            .filter((q) => q.id in c.answers)
            .map((q) => h("div", { class: "wide" }, h("dt", {}, q.label), h("dd", {}, answerText(q, c.answers[q.id])))),
        )
      : null,
    c.estimate
      ? h(
          "div",
          { class: "checkin-estimate" },
          h("strong", {}, `Estimated cost not covered by insurance: ${money(c.estimate.total_cents)}`),
          h(
            "span",
            { class: "muted small" },
            ` · ${c.estimate.lines.map((l) => `${l.label} ${money(l.amount_cents)}`).join(" · ")}`,
          ),
        )
      : null,
    c.payment
      ? h(
          "p",
          { class: "small" },
          `Pre-authorization: ${money(c.payment.amount_cents)} on ${c.payment.method.display} — ${c.payment.status}` +
            (c.payment.reference ? ` · ref ${c.payment.reference}` : ""),
        )
      : null,
    staffNotifiedText(c, tz) ? h("p", { class: "small checkin-staff-notified" }, staffNotifiedText(c, tz)) : null,
    h(
      "div",
      { class: "checkin-signatures" },
      signature(
        "Questionnaire",
        c.answers_signer_name,
        c.answers_signed_at,
        c.answers_signature_png,
        c.answers_esign_reference,
      ),
      signature(
        "Financial responsibility",
        c.financial_signer_name,
        c.financial_signed_at,
        c.financial_signature_png,
        c.financial_esign_reference,
      ),
    ),
  );
}

/** "Reminder sent by e-mail …, text … on … · confirmed via link on …" — the day-before reminder and the answer. */
function reminderStatus(a) {
  if (a.status !== "scheduled" && !a.reminder_sent_at) return null;
  const parts = [];
  if (a.reminder_sent_at) {
    parts.push(
      a.reminder_channels.length
        ? `Reminder sent by ${a.reminder_channels.map((c) => (c === "sms" ? "text" : "e-mail")).join(" and ")} on ${officeDateTime(a.reminder_sent_at, a.timezone)}`
        : `Reminder could not be sent${a.reminder_note ? ` — ${a.reminder_note}` : ""}`,
    );
  } else {
    parts.push("Reminder: due the day before");
  }
  parts.push(
    a.confirmed_at
      ? `confirmed via ${a.confirmed_via || "?"} on ${officeDateTime(a.confirmed_at, a.timezone)}`
      : "not confirmed yet",
  );
  return h("div", { class: "muted small agenda-reminder" }, parts.join(" · "));
}

/**
 * "Send due reminders now": runs the job (it also runs by itself every minute). Outside
 * production an "as of" choice pretends it is already tomorrow, so the reminder for an
 * appointment booked today can be tried without waiting — it lands in the test inbox.
 */
function remindersControl(getNextStart, onSent) {
  const asOf = select(
    [
      ["", "as of now"],
      ["next", "as of 12 h before the next appointment (simulate)"],
      ["1", "as of +1 day (simulate)"],
      ["2", "as of +2 days (simulate)"],
    ],
    { id: "reminders-as-of", value: "" },
  );
  const msg = h("span", { class: "muted small", id: "reminders-result" });
  const run = button("Send due reminders now", {
    class: "btn small-btn",
    id: "btn-reminders-run",
    title: "Reminders go out by e-mail / text a day before each appointment; the job runs every minute — or now.",
    onclick: async () => {
      run.disabled = true;
      msg.textContent = "Running…";
      try {
        let moment = null;
        if (asOf.value === "next") {
          const next = getNextStart();
          if (!next) throw new Error("No upcoming appointment on this agenda to simulate for.");
          moment = new Date(new Date(next).getTime() - 12 * 3_600_000);
        } else if (asOf.value) moment = new Date(Date.now() + Number(asOf.value) * 86_400_000);
        const query = moment ? `?as_of=${encodeURIComponent(moment.toISOString())}` : "";
        const out = await api.post(`/appointments/reminders/run${query}`);
        msg.textContent =
          `${out.due} due, ${out.sent} delivered` +
          (out.numbers.length ? ` (${out.numbers.join(", ")})` : "") +
          (out.simulated ? " — simulated run; messages are in the test inbox" : "");
        toast(out.sent ? `Reminders sent: ${out.sent}` : "No reminder was due");
        if (out.sent) await onSent(); // the rows now show "Reminder sent …"
      } catch (e) {
        msg.textContent = describeError(e);
        toast(describeError(e), { error: true });
      } finally {
        run.disabled = false;
      }
    },
  });
  return h(
    "div",
    { class: "reminders-bar", id: "reminders-bar" },
    h("span", { class: "small" }, h("strong", {}, "Reminders"), " the day before, by e-mail / text:"),
    run,
    asOf,
    msg,
  );
}

/** "Link sent by e-mail (p•••@example.com) on … · opened 2× (last …)" — the delivery record. */
function linkStatus(a) {
  const link = a.link;
  if (!link) return null;
  const parts = [];
  if (link.channels.length) {
    const to = describeSentTo(link.sent_to);
    parts.push(`Link sent by ${to}${link.sent_at ? ` on ${officeDateTime(link.sent_at, a.timezone)}` : ""}`);
    if (link.fake) parts.push("built-in fake delivery (test inbox)");
  } else {
    parts.push("Link not sent" + (link.note ? ` — ${link.note}` : ""));
  }
  parts.push(
    link.opens ? `opened ${link.opens}× (last ${officeDateTime(link.last_opened_at, a.timezone)})` : "not opened yet",
  );
  return h("div", { class: "muted small agenda-link", dataset: { opens: String(link.opens) } }, parts.join(" · "));
}

/** "Visit summary VS-000001 v1 signed by Alex Example on … · sent … · opened by the patient" / the draft. */
function summaryText(a) {
  const vs = a.visit_summary;
  if (!vs) return "";
  const head = `Visit summary ${vs.number}${vs.version > 1 ? ` v${vs.version}` : ""}`;
  if (vs.status !== "signed")
    return `${head}: draft — waiting for ${a.provider.role === "doctor" ? "Dr. " : ""}${a.provider.first_name} ${a.provider.last_name}'s signature`;
  return [
    `${head} signed by ${vs.signer_name || "the provider"} on ${officeDateTime(vs.signed_at, a.timezone)}`,
    vs.sent_at ? `sent to the patient ${officeDateTime(vs.sent_at, a.timezone)}` : "not sent by e-mail / text",
    vs.patient_viewed_at ? "opened by the patient" : null,
  ]
    .filter(Boolean)
    .join(" · ");
}

function formatPatientNumber(id) {
  return `P-${String(id).padStart(6, "0")}`;
}

/** The medications and allergies the assistant reviewed: each with the decision and its note. */
function reviewList(items) {
  const row = (i) => {
    const [label, kind] = reviewDecision(i);
    return h(
      "li",
      { dataset: { decision: i.decision || "open" } },
      h("strong", {}, i.name),
      i.detail ? ` · ${i.detail}` : "",
      " ",
      badge(label, kind),
      i.flag_text ? h("span", { class: "muted" }, ` — ${i.flag_text}`) : null,
      i.note ? h("div", { class: "muted" }, `Note: ${i.note}`) : null,
    );
  };
  return h(
    "div",
    { class: "small review-list" },
    ["medication", "allergy"].map((kind) => {
      const mine = items.filter((i) => i.kind === kind);
      return mine.length
        ? h(
            "div",
            {},
            h("span", {}, kind === "medication" ? "Medications reviewed:" : "Allergies reviewed:"),
            h("ul", {}, mine.map(row)),
          )
        : null;
    }),
  );
}
