import { api } from "../api.js";
import { HEALTH_SOURCES } from "../format.js";
import {
  badge,
  button,
  callout,
  describeError,
  field,
  formatDateTime,
  h,
  input,
  render,
  select,
  toast,
} from "../ui.js";

/** The chart's medication and allergy lists: [kind, list title, name placeholder, detail placeholder]. */
export const MEDS_AND_ALLERGIES = {
  id: "patient-health",
  title: "Medications & allergies",
  hint:
    "Reviewed with the patient by the medical assistant at each visit (together with the check-in answers) — " +
    "the review keeps these lists current.",
  lists: [
    ["medication", "Medications", "e.g. Lisinopril", "Dose and how often"],
    ["allergy", "Allergies", "e.g. Penicillin", "Reaction / severity"],
  ],
};

/** The medical history: conditions, surgeries, family and social history. */
export const MEDICAL_HISTORY = {
  id: "patient-history",
  title: "Medical history",
  hint: "Conditions, surgeries, family and social history — the provider reads them in the patient's chart.",
  lists: [
    ["condition", "Conditions", "e.g. Hypertension", "Since when / how it is managed"],
    ["surgery", "Surgeries & procedures", "e.g. Appendectomy", "Year / details"],
    ["family_history", "Family history", "e.g. Heart attack — father", "Age / details"],
    ["social_history", "Social history", "e.g. Never smoked", "Details"],
  ],
};

const EMPTY = {
  medication: "No current medications on record.",
  allergy: "No known allergies on record.",
};

/**
 * Lists of the patient's chart (GET/POST/PATCH /patients/{id}/health-items) — the medication and
 * allergy lists (what a medical assistant's review of a visit starts from, together with what the
 * patient writes at check-in; the review keeps them current: confirmed entries re-dated, removed ones
 * "no longer current" with the note why, new ones added) or the medical history. The office adds an
 * entry here and marks one no longer current (or current again).
 */
export function healthSection(p, config = MEDS_AND_ALLERGIES) {
  const section = h("section", { class: "card", id: config.id });
  draw([...(p.medications || []), ...(p.allergies || []), ...(p.medical_history || [])]);
  return section;

  async function reload() {
    try {
      draw(await api.get(`/patients/${p.id}/health-items`));
    } catch (e) {
      toast(describeError(e), { error: true });
    }
  }

  function draw(rows) {
    render(
      section,
      h("h3", {}, config.title),
      h("p", { class: "muted small" }, config.hint),
      h(
        "div",
        { class: "two-col health-lists" },
        config.lists.map(([kind, title]) =>
          list(
            title,
            kind,
            rows.filter((r) => r.kind === kind),
          ),
        ),
      ),
      addForm(),
    );
  }

  function list(title, kind, rows) {
    const active = rows.filter((r) => r.status === "active");
    const inactive = rows.filter((r) => r.status !== "active");
    return h(
      "div",
      { class: "health-list", id: `patient-${kind}-list` },
      h("h4", {}, title, h("span", { class: "muted small" }, ` · ${active.length}`)),
      active.length
        ? h(
            "ul",
            { class: "rows" },
            active.map((r) => entry(r)),
          )
        : h("p", { class: "empty" }, EMPTY[kind] || "Nothing on record."),
      inactive.length
        ? h(
            "details",
            { class: "health-inactive" },
            h("summary", { class: "small" }, `No longer current (${inactive.length})`),
            h(
              "ul",
              { class: "rows" },
              inactive.map((r) => entry(r)),
            ),
          )
        : null,
    );
  }

  function entry(r) {
    const reviewed = r.last_reviewed_at
      ? `reviewed ${formatDateTime(r.last_reviewed_at)}${r.last_reviewed_by ? ` by ${r.last_reviewed_by}` : ""}`
      : "not reviewed yet";
    const toggle =
      r.status === "active"
        ? button("No longer current", {
            class: "btn small-btn",
            id: `btn-health-inactive-${r.id}`,
            onclick: async () => {
              const note = prompt(`Why is ${r.name} no longer current?`, "");
              if (note === null) return;
              await change(r, { status: "inactive", note: note.trim() || null }, `${r.name}: no longer current`);
            },
          })
        : button("Current again", {
            class: "btn small-btn",
            id: `btn-health-active-${r.id}`,
            onclick: () => change(r, { status: "active" }, `${r.name} is current again`),
          });
    return h(
      "li",
      { class: `row health-item ${r.status}`, dataset: { id: String(r.id) } },
      h(
        "div",
        { class: "row-main" },
        h("strong", {}, r.name),
        r.detail ? h("span", {}, ` · ${r.detail}`) : null,
        r.status !== "active" ? badge("no longer current", "muted") : null,
      ),
      h("div", { class: "muted small" }, `${HEALTH_SOURCES[r.source] || r.source} · ${reviewed}`),
      r.note ? h("div", { class: "small" }, `Note: ${r.note}`) : null,
      h("div", { class: "row-meta" }, toggle),
    );
  }

  async function change(r, body, done) {
    try {
      await api.patch(`/patients/${p.id}/health-items/${r.id}`, body);
      toast(done);
      await reload();
    } catch (e) {
      toast(describeError(e), { error: true });
    }
  }

  function addForm() {
    const fid = (name) => `${config.id}-${name}`;
    const kind = select(
      config.lists.map(([k, title]) => [k, title]),
      { id: fid("kind") },
    );
    const name = input({ id: fid("name"), maxlength: 200, placeholder: config.lists[0][2] });
    const detail = input({ id: fid("detail"), maxlength: 300, placeholder: config.lists[0][3] });
    kind.addEventListener("change", () => {
      const spec = config.lists.find(([k]) => k === kind.value);
      name.placeholder = spec[2];
      detail.placeholder = spec[3];
    });
    const note = input({ id: fid("note"), maxlength: 1000, placeholder: "Optional" });
    const msg = h("div", { class: "check-live" });
    return h(
      "form",
      {
        class: "inline-form health-add",
        id: fid("add-form"),
        onsubmit: async (e) => {
          e.preventDefault();
          msg.replaceChildren();
          if (!name.value.trim()) {
            msg.append(callout("error", "Name missing", " What should the entry say?"));
            return;
          }
          try {
            await api.post(`/patients/${p.id}/health-items`, {
              kind: kind.value,
              name: name.value.trim(),
              detail: detail.value.trim() || null,
              note: note.value.trim() || null,
            });
            toast(`${name.value.trim()} added`);
            await reload();
          } catch (err) {
            msg.append(callout("error", "Could not add", h("span", {}, describeError(err))));
          }
        },
      },
      h("h4", {}, "Add an entry"),
      h(
        "div",
        { class: "health-add-fields" },
        field("List", kind),
        field("Name", name),
        field("Detail", detail, { optional: true }),
        field("Note", note, { optional: true }),
      ),
      h("div", { class: "actions" }, button("Add", { class: "btn primary", type: "submit", id: fid("add") })),
      msg,
    );
  }
}
