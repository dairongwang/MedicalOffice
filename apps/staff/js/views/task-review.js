import { api } from "../api.js";
import { decisionOptions, flagBadge, reviewProgress, toNumber, vitalsProblems } from "../assistant-tasks.js";
import { answerText, officeDateTime } from "../staff-appointments.js";
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

/**
 * "Review & complete" — the medical assistant's form for one visit (GET/PUT /staff/me/tasks/{id}/review,
 * POST …/complete):
 *
 *   1. Patient roomed (tick)
 *   2. Vital signs: blood pressure (systolic / diastolic), pulse, temperature (°F / °C), weight (lb / kg)
 *   3. Medications  ┐ prepopulated from the chart and the patient's check-in answers; every item is
 *   4. Allergies    ┘ cleared (confirmed / remove) with a note of its own; items can be added
 *   5. Check-in answers: shown, reviewed (tick), note (required when the patient did not check in)
 *   6. Provider told (tick)
 *
 * What is still missing is listed live (the same rules as the server); "Save progress" keeps a
 * draft, "Mark completed" sends everything and completes the task.
 */
export function taskReviewPanel(t, { onCompleted }) {
  const root = h("div", { class: "task-review", id: `task-review-${t.id}`, dataset: { kind: "complete" } }, spinner());
  void load();
  return root;

  async function load() {
    render(root, spinner("Preparing the review…"));
    try {
      show(await api.get(`/staff/me/tasks/${t.id}/review`));
    } catch (e) {
      render(root, callout("error", "Could not load the review", h("span", {}, describeError(e))));
    }
  }

  function show(review) {
    const state = stateOf(review);
    const problemsBox = h("ul", { class: "review-problems", id: `task-${t.id}-problems` });
    const serverMsg = h("div", { class: "check-live", id: `task-${t.id}-review-msg` });
    const note = h("textarea", {
      class: "input",
      id: `task-${t.id}-note`,
      rows: 2,
      maxlength: 2000,
      placeholder: "Optional — anything the provider should know",
    });
    const saveBtn = button("Save progress", { class: "btn", id: `btn-task-${t.id}-save` });
    const doneBtn = button("Mark completed", { class: "btn primary", id: `btn-task-${t.id}-done`, type: "submit" });

    const refresh = () => {
      const { problems } = reviewProgress(state);
      render(
        problemsBox,
        problems.length
          ? problems.map((p) => h("li", {}, p))
          : h("li", { class: "ready" }, "Everything is done — the task can be completed."),
      );
      problemsBox.classList.toggle("all-done", !problems.length);
      doneBtn.disabled = !review.editable || problems.length > 0;
      saveBtn.disabled = !review.editable;
    };

    const send = async (complete) => {
      serverMsg.replaceChildren();
      saveBtn.disabled = doneBtn.disabled = true;
      try {
        if (complete) {
          await api.post(`/staff/me/tasks/${t.id}/complete`, {
            review: payload(state),
            note: note.value.trim() || null,
          });
          toast(`${t.appointment.number}: task completed — the patient's chart is updated`);
          onCompleted();
          return;
        }
        show(await api.put(`/staff/me/tasks/${t.id}/review`, payload(state)));
        toast("Saved");
      } catch (e) {
        serverMsg.append(callout("error", complete ? "Not completed" : "Not saved", h("span", {}, describeError(e))));
        refresh();
      }
    };
    saveBtn.addEventListener("click", () => void send(false));

    const tick = (key, label, id) => {
      const box = h("input", { type: "checkbox", id, checked: !!state[key] });
      box.addEventListener("change", () => {
        state[key] = box.checked;
        refresh();
      });
      return h("label", { class: "checkbox", for: id }, box, h("span", {}, label));
    };

    render(
      root,
      h(
        "form",
        {
          class: "form task-form review-form",
          onsubmit: (e) => {
            e.preventDefault();
            void send(true);
          },
        },
        !review.editable
          ? callout(
              "info",
              "Not yet",
              ` This review can be filled in from ${officeDateTime(review.complete_opens_at, t.appointment.timezone)}.`,
            )
          : null,
        section("1", "Patient roomed", tick("room_patient", review.duties[0].label, `task-${t.id}-room`)),
        section("2", "Vital signs", vitalsFields(t, state, refresh)),
        section("3", "Medications", itemList(t, state, "medication", refresh, review.patient_said_no_medications)),
        section("4", "Allergies", itemList(t, state, "allergy", refresh, review.patient_said_no_allergies)),
        section("5", "Check-in answers", checkInPart(t, review, state, refresh)),
        section("6", "Provider", tick("provider_ready", review.duties[5].label, `task-${t.id}-provider`)),
        field("Note for the provider / the office", note, { optional: true }),
        h("strong", { class: "small" }, "Still to do"),
        problemsBox,
        h("div", { class: "actions" }, saveBtn, h("span", { class: "spacer" }), doneBtn),
        serverMsg,
      ),
    );
    refresh();
  }
}

/** Hints that need the assistant's attention (the others just say where the item comes from). */
const ATTENTION = new Set(["changed", "new", "not_mentioned", "said_none"]);

function section(no, title, ...body) {
  return h(
    "fieldset",
    { class: "review-section" },
    h("legend", {}, h("span", { class: "step-no" }, no), ` ${title}`),
    ...body,
  );
}

/** The review as the form keeps it (every change goes here; the problems are computed from it). */
function stateOf(review) {
  const item = (i) => ({ ...i, fromChart: !!i.health_item_id });
  const v = review.vitals || {};
  return {
    room_patient: review.room_patient,
    provider_ready: review.provider_ready,
    check_in_reviewed: review.check_in_reviewed,
    check_in_note: review.check_in_note || "",
    no_medications: review.no_medications,
    no_allergies: review.no_allergies,
    has_check_in: !!review.check_in,
    vitals: {
      bp_systolic: v.bp_systolic ?? null,
      bp_diastolic: v.bp_diastolic ?? null,
      pulse: v.pulse ?? null,
      temperature: v.temperature ?? null,
      temperature_unit: v.temperature_unit || "F",
      weight: v.weight ?? null,
      weight_unit: v.weight_unit || "lb",
      note: v.note || "",
    },
    medications: review.medications.map(item),
    allergies: review.allergies.map(item),
  };
}

/** What PUT /review (and the completion) gets: everything the form holds. */
export function payload(state) {
  const v = state.vitals;
  const num = (n) => (n === null || Number.isNaN(n) ? null : n);
  const vitals = {
    bp_systolic: num(v.bp_systolic),
    bp_diastolic: num(v.bp_diastolic),
    pulse: num(v.pulse),
    temperature: num(v.temperature),
    temperature_unit: v.temperature_unit,
    weight: num(v.weight),
    weight_unit: v.weight_unit,
    note: v.note.trim() || null,
  };
  const items = [...state.medications, ...state.allergies]
    .filter((i) => i.id || (i.name || "").trim())
    .map((i) => ({
      ...(i.id ? { id: i.id } : { kind: i.kind }),
      name: (i.name || "").trim() || undefined,
      detail: (i.detail || "").trim() || null,
      decision: i.decision || "pending",
      note: (i.note || "").trim() || null,
    }));
  return {
    room_patient: !!state.room_patient,
    vitals,
    items,
    no_medications: !!state.no_medications,
    no_allergies: !!state.no_allergies,
    check_in_reviewed: !!state.check_in_reviewed,
    check_in_note: state.check_in_note.trim() || null,
    provider_ready: !!state.provider_ready,
  };
}

function vitalsFields(t, state, refresh) {
  const v = state.vitals;
  const id = (k) => `task-${t.id}-${k}`;
  const numberInput = (key, attrs) => {
    const el = input({
      type: "number",
      inputmode: "decimal",
      id: id(key),
      value: v[key] ?? "",
      class: "input vital",
      ...attrs,
    });
    el.addEventListener("input", () => {
      v[key] = toNumber(el.value);
      refresh();
      render(
        errors,
        vitalsProblems(v).map((p) => h("div", {}, p)),
      );
    });
    return el;
  };
  const unit = (key, options) => {
    const el = select(options, { id: id(key), value: v[key], class: "input unit" });
    el.addEventListener("change", () => {
      v[key] = el.value;
      refresh();
      render(
        errors,
        vitalsProblems(v).map((p) => h("div", {}, p)),
      );
    });
    return el;
  };
  const note = input({
    id: id("vitals-note"),
    value: v.note,
    maxlength: 500,
    placeholder: "e.g. taken seated, left arm",
  });
  note.addEventListener("input", () => (v.note = note.value));
  const errors = h(
    "div",
    { class: "error-text small", id: id("vitals-errors") },
    vitalsProblems(v).map((p) => h("div", {}, p)),
  );
  return [
    h(
      "div",
      { class: "vitals-grid" },
      h(
        "div",
        { class: "field" },
        h("label", { for: id("bp_systolic") }, "Blood pressure (mmHg)"),
        h(
          "div",
          { class: "bp" },
          numberInput("bp_systolic", { min: 50, max: 260, step: 1, placeholder: "120", "aria-label": "Systolic" }),
          h("span", { class: "muted" }, "/"),
          numberInput("bp_diastolic", { min: 30, max: 160, step: 1, placeholder: "80", "aria-label": "Diastolic" }),
        ),
      ),
      field("Pulse (beats/min)", numberInput("pulse", { min: 20, max: 250, step: 1, placeholder: "72" })),
      h(
        "div",
        { class: "field" },
        h("label", { for: id("temperature") }, "Temperature"),
        h(
          "div",
          { class: "with-unit" },
          numberInput("temperature", { step: 0.1, placeholder: "98.6" }),
          unit("temperature_unit", [
            ["F", "°F"],
            ["C", "°C"],
          ]),
        ),
      ),
      h(
        "div",
        { class: "field" },
        h("label", { for: id("weight") }, "Weight"),
        h(
          "div",
          { class: "with-unit" },
          numberInput("weight", { step: 0.1, placeholder: "154" }),
          unit("weight_unit", [
            ["lb", "lb"],
            ["kg", "kg"],
          ]),
        ),
      ),
    ),
    field("Note on the measurements", note, { optional: true }),
    errors,
  ];
}

/** The medication or allergy list: each item with its hint, the decision and a note; "+ Add". */
function itemList(t, state, kind, refresh, patientSaidNone) {
  const listKey = kind === "medication" ? "medications" : "allergies";
  const noneKey = kind === "medication" ? "no_medications" : "no_allergies";
  const noneLabel = kind === "medication" ? "The patient takes no medications" : "No known allergies";
  const ul = h("ul", { class: "review-items", id: `task-${t.id}-${listKey}` });
  let seq = 0;
  const draw = () =>
    render(
      ul,
      state[listKey].map((item) => itemRow(item)),
    );

  const itemRow = (item) => {
    const uid = item.id ? `i${item.id}` : `new-${kind}-${(item.tmp ??= ++seq)}`;
    const base = `task-${t.id}-${uid}`;
    const [flagLabel, flagKind] = flagBadge(item);
    const name = input({
      id: `${base}-name`,
      value: item.name || "",
      maxlength: 200,
      placeholder: kind === "medication" ? "Medication" : "Substance",
      class: "input item-name",
      "aria-label": "Name",
    });
    const detail = input({
      id: `${base}-detail`,
      value: item.detail || "",
      maxlength: 300,
      placeholder: kind === "medication" ? "Dose and how often" : "Reaction / severity",
      class: "input item-detail",
      "aria-label": "Detail",
    });
    const note = input({
      id: `${base}-note`,
      value: item.note || "",
      maxlength: 1000,
      placeholder: item.fromChart ? "Note (required when removing)" : "Note",
      class: "input item-note",
      "aria-label": "Note",
    });
    const li = h("li", {
      class: "review-item",
      id: base,
      dataset: { decision: item.decision || "open", flag: item.flag || "" },
    });
    name.addEventListener("input", () => {
      item.name = name.value;
      refresh();
    });
    detail.addEventListener("input", () => (item.detail = detail.value));
    note.addEventListener("input", () => {
      item.note = note.value;
      refresh();
    });
    const radios = decisionOptions(kind).map(([value, label]) => {
      const r = h("input", {
        type: "radio",
        name: `${base}-decision`,
        value,
        id: `${base}-${value}`,
        checked: item.decision === value,
      });
      r.addEventListener("change", () => {
        item.decision = value;
        li.dataset.decision = value;
        refresh();
      });
      return h("label", { class: "checkbox inline", for: r.id }, r, h("span", {}, label));
    });
    return append(
      li,
      h(
        "div",
        { class: "item-head" },
        name,
        detail,
        badge(flagLabel, flagKind),
        !item.id
          ? button("✕", {
              class: "btn small-btn",
              title: "Take this new item off the list",
              onclick: () => {
                state[listKey] = state[listKey].filter((x) => x !== item);
                draw();
                refresh();
              },
            })
          : null,
      ),
      item.flag_text
        ? h("p", { class: `small item-hint ${ATTENTION.has(item.flag) ? "attention" : "muted"}` }, item.flag_text)
        : null,
      h("div", { class: "item-decision" }, ...radios),
      note,
    );
  };

  const noneBox = h("input", { type: "checkbox", id: `task-${t.id}-${noneKey}`, checked: !!state[noneKey] });
  noneBox.addEventListener("change", () => {
    state[noneKey] = noneBox.checked;
    refresh();
  });
  draw();
  return [
    patientSaidNone ? h("p", { class: "small item-hint attention" }, `At check-in the patient wrote “none”.`) : null,
    ul,
    h(
      "div",
      { class: "actions" },
      button(kind === "medication" ? "+ Add medication" : "+ Add allergy", {
        class: "btn small-btn",
        id: `btn-task-${t.id}-add-${kind}`,
        onclick: () => {
          state[listKey].push({ kind, name: "", detail: "", decision: null, note: "", origin: "assistant" });
          draw();
          refresh();
          ul.lastElementChild?.querySelector(".item-name")?.focus();
        },
      }),
      h("label", { class: "checkbox", for: noneBox.id }, noneBox, h("span", {}, noneLabel)),
    ),
  ];
}

function checkInPart(t, review, state, refresh) {
  const reviewed = h("input", {
    type: "checkbox",
    id: `task-${t.id}-check-in-reviewed`,
    checked: !!state.check_in_reviewed,
  });
  reviewed.addEventListener("change", () => {
    state.check_in_reviewed = reviewed.checked;
    refresh();
  });
  const note = h("textarea", {
    class: "input",
    id: `task-${t.id}-check-in-note`,
    rows: 2,
    maxlength: 1000,
    placeholder: review.check_in
      ? "Optional — what came up when going through the answers"
      : "Required — the patient did not check in: how were the questions covered?",
  });
  note.value = state.check_in_note;
  note.addEventListener("input", () => {
    state.check_in_note = note.value;
    refresh();
  });
  const c = review.check_in;
  return [
    c
      ? h(
          "dl",
          { class: "record check-in-answers" },
          c.questions
            .filter((q) => q.id in c.answers)
            .map((q) => h("div", { class: "wide" }, h("dt", {}, q.label), h("dd", {}, answerText(q, c.answers[q.id])))),
        )
      : h(
          "p",
          { class: "small item-hint attention" },
          "The patient has not checked in for this visit — no answers to show.",
        ),
    h("label", { class: "checkbox", for: reviewed.id }, reviewed, h("span", {}, review.duties[4].label)),
    field("Note", note, { optional: !!c }),
  ];
}
