import { api } from "../api.js";
import { immunizationProblems } from "../format.js";
import {
  badge,
  button,
  callout,
  describeError,
  field,
  formatDate,
  formValues,
  h,
  input,
  orNull,
  render,
  select,
  toast,
} from "../ui.js";

/**
 * The vaccines in the patient's record (GET/POST/PATCH /patients/{id}/immunizations —
 * services/account/immunizations.py): doses given here (with the member of staff) or reported by another
 * clinic / the patient's card. The provider reads them on the chart; the immunization record and the annual
 * health examination report list them. A wrong entry is never deleted: it is marked "entered in error".
 */
export function immunizationsSection(p, { onChange } = {}) {
  const section = h("section", { class: "card", id: "patient-immunizations" });
  void reload();
  return section;

  async function reload() {
    let rows, staff;
    try {
      [rows, staff] = await Promise.all([
        api.get(`/patients/${p.id}/immunizations`),
        api.get("/workers?limit=200").catch(() => ({ items: [] })),
      ]);
    } catch (e) {
      render(section, h("h3", {}, "Immunizations"), callout("error", "Could not load", h("span", {}, describeError(e))));
      return;
    }
    const active = rows.filter((r) => r.status === "active");
    render(
      section,
      h("h3", {}, "Immunizations", h("span", { class: "muted small" }, ` · ${active.length}`)),
      rows.length
        ? h(
            "ul",
            { class: "rows", id: "patient-immunization-list" },
            rows.map((r) => row(r)),
          )
        : h("p", { class: "empty", id: "patient-immunizations-empty" }, "No immunizations on record."),
      addForm(staff.items || []),
    );
  }

  function row(r) {
    const wrong = r.status === "entered_in_error";
    return h(
      "li",
      { class: `row immunization ${wrong ? "entered-in-error" : ""}`, dataset: { id: String(r.id) } },
      h(
        "div",
        { class: "row-main" },
        h("strong", {}, r.vaccine),
        r.dose_label ? h("span", { class: "muted small" }, ` · dose ${r.dose_label}`) : null,
        r.source === "office" ? badge("given here", "ok") : badge("reported", "muted"),
        wrong ? badge("entered in error", "bad") : null,
      ),
      h(
        "div",
        { class: "small" },
        [
          formatDate(r.date_given),
          [r.manufacturer, r.lot_number && `lot ${r.lot_number}`].filter(Boolean).join(" · "),
          [r.site, r.route].filter(Boolean).join(" · "),
          r.administered_by && `given by ${r.administered_by}`,
          r.cvx_code && `CVX ${r.cvx_code}`,
        ]
          .filter(Boolean)
          .join(" · "),
      ),
      r.note ? h("div", { class: "muted small" }, r.note) : null,
      h(
        "div",
        { class: "actions" },
        h("span", { class: "spacer" }),
        button(wrong ? "Restore" : "Entered in error", {
          class: `btn small-btn ${wrong ? "" : "danger"}`,
          id: `btn-immunization-error-${r.id}`,
          title: wrong
            ? "Put it back on the record"
            : "Keep it in the history but leave it off the record and every report",
          onclick: async () => {
            if (!wrong && !confirm(`Mark ${r.vaccine} (${formatDate(r.date_given)}) as entered in error?`)) return;
            try {
              await api.patch(`/patients/${p.id}/immunizations/${r.id}`, {
                status: wrong ? "active" : "entered_in_error",
              });
              toast(wrong ? "Entry restored" : "Marked as entered in error");
              await reload();
              onChange?.();
            } catch (e) {
              toast(describeError(e), { error: true });
            }
          },
        }),
      ),
    );
  }

  function addForm(staff) {
    const msg = h("div", { class: "check-live", id: "immunization-msg" });
    const givenHere = staff.filter((w) => w.role !== "receptionist" && w.role !== "administrator");
    const worker = select([["", "— another clinic / not known —"], ...givenHere.map((w) => [String(w.id), `${w.first_name} ${w.last_name}`])], {
      name: "worker_id",
      id: "immunization-worker",
    });
    const form = h(
      "form",
      { class: "form compact inline-form", id: "immunization-form", novalidate: true },
      h("h4", {}, "Enter a dose"),
      msg,
      h(
        "div",
        { class: "field-row" },
        field(
          "Vaccine *",
          input({ name: "vaccine", maxlength: 200, placeholder: "e.g. Influenza, inactivated", id: "immunization-vaccine" }),
        ),
        field("Date given *", input({ name: "date_given", type: "date", id: "immunization-date" })),
      ),
      h(
        "div",
        { class: "field-row three" },
        field("Dose no.", input({ name: "dose_number", type: "number", min: 1, max: 20, id: "immunization-dose" }), {
          optional: true,
        }),
        field("of (series)", input({ name: "series_doses", type: "number", min: 1, max: 20, id: "immunization-series" }), {
          optional: true,
        }),
        field("CVX code", input({ name: "cvx_code", maxlength: 8, id: "immunization-cvx" }), { optional: true }),
      ),
      h(
        "div",
        { class: "field-row three" },
        field("Manufacturer", input({ name: "manufacturer", maxlength: 120, id: "immunization-maker" }), { optional: true }),
        field("Lot number", input({ name: "lot_number", maxlength: 60, id: "immunization-lot" }), { optional: true }),
        field("Site", input({ name: "site", maxlength: 60, placeholder: "left deltoid", id: "immunization-site" }), {
          optional: true,
        }),
      ),
      h(
        "div",
        { class: "field-row three" },
        field(
          "Route",
          select(
            [
              ["", "—"],
              ["IM", "IM (intramuscular)"],
              ["SC", "SC (subcutaneous)"],
              ["ID", "ID (intradermal)"],
              ["oral", "Oral"],
              ["intranasal", "Intranasal"],
              ["other", "Other"],
            ],
            { name: "route", id: "immunization-route" },
          ),
        ),
        field("Given here by", worker, { optional: true }),
        field("…or by (another clinic)", input({ name: "administered_by", maxlength: 200, id: "immunization-by" }), {
          optional: true,
        }),
      ),
      field("Note", input({ name: "note", maxlength: 1000, id: "immunization-note" }), { optional: true }),
      h(
        "div",
        { class: "actions" },
        h("span", { class: "spacer" }),
        button("Add to the record", { type: "submit", class: "btn primary", id: "btn-immunization-add" }),
      ),
    );
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      msg.replaceChildren();
      const v = formValues(form);
      const problems = immunizationProblems(v);
      if (problems.length)
        return msg.append(
          callout(
            "error",
            "Please check",
            h(
              "ul",
              {},
              problems.map((x) => h("li", {}, x)),
            ),
          ),
        );
      try {
        await api.post(`/patients/${p.id}/immunizations`, {
          vaccine: v.vaccine,
          date_given: v.date_given,
          dose_number: v.dose_number ? Number(v.dose_number) : null,
          series_doses: v.series_doses ? Number(v.series_doses) : null,
          cvx_code: orNull(v.cvx_code),
          manufacturer: orNull(v.manufacturer),
          lot_number: orNull(v.lot_number),
          site: orNull(v.site),
          route: orNull(v.route),
          worker_id: v.worker_id ? Number(v.worker_id) : null,
          administered_by: orNull(v.administered_by),
          source: v.worker_id ? "office" : "external",
          note: orNull(v.note),
        });
        toast(`${v.vaccine} added`);
        await reload();
        onChange?.();
      } catch (err) {
        msg.append(callout("error", "Could not add", h("span", {}, describeError(err))));
      }
    });
    return form;
  }
}
