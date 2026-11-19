import { api } from "../api.js";
import { ageOn, hashFor } from "../format.js";
import {
  badge,
  button,
  callout,
  describeError,
  formatDate,
  formatDateTime,
  h,
  input,
  record,
  render,
  spinner,
  toast,
} from "../ui.js";
import { appointmentRow } from "./appointments.js";
import { referralsSection, resultsSection } from "./clinical-records.js";
import { MEDICAL_HISTORY, MEDS_AND_ALLERGIES, healthSection } from "./health-items.js";
import { patientEditForm } from "./patient-edit.js";
import { paymentHistorySection } from "./payment-history.js";
import { paymentMethodsSection } from "./payment-methods.js";
import { immunizationsSection } from "./immunizations.js";
import { patientPharmacySection, prescriptionsSection } from "./prescriptions.js";
import { patientReportsSection } from "./reports.js";

/**
 * Administrators: the office's patient roster — search (name, record number, e-mail, phone),
 * page through it, open one record with everything the patient registered and their
 * appointment history (past and upcoming, every status) and the payment methods on the
 * patient's account (views/payment-methods.js).
 * One record has four sub-pages: the record itself (with the insurance and the appointment history),
 * the clinical record (medications, allergies, medical history — views/health-items.js — prescriptions
 * (views/prescriptions.js), lab and imaging results and referral notes — views/clinical-records.js), the payment methods on the patient's account, and the payment history.
 * The "Immunizations & reports" sub-page: the vaccines on record (views/immunizations.js) and the reports the
 * office prepares on request for a doctor to sign (views/reports.js).
 * Route: #/patients  ·  #/patients?q=&offset=  ·  #/patients/<id>  ·  #/patients/<id>/clinical  ·  #/patients/<id>/payment-methods
 *        ·  #/patients/<id>/reports
 *        ·  #/patients/<id>/payments
 */
export function patientsView(params) {
  const root = h("section", { class: "page wide" });
  if (params.id) void renderDetail(root, Number(params.id), null, params.sub || "record");
  else void renderList(root, params.q || "", Number(params.offset) || 0);
  return root;
}

const PAGE = 50;

async function renderList(root, q, offset) {
  const search = input({
    type: "search",
    placeholder: "Search name, P-000042, e-mail or phone",
    value: q,
    id: "patients-search",
  });
  search.addEventListener("change", () => (location.hash = hashFor("patients", { q: search.value.trim() })));
  render(
    root,
    h(
      "header",
      { class: "page-header" },
      h(
        "div",
        {},
        h("h2", {}, "Patients"),
        h(
          "p",
          { class: "muted small" },
          "Everyone registered with the office (through the Patients tab of the main app), sorted by name.",
        ),
      ),
    ),
    h("div", { class: "toolbar" }, search),
  );
  const list = h("div", { id: "patients-list" }, spinner());
  root.append(list);
  let page;
  try {
    page = await api.get(`/patients?q=${encodeURIComponent(q)}&offset=${offset}&limit=${PAGE}`);
  } catch (e) {
    render(list, callout("error", "Could not load the patients", h("span", {}, describeError(e))));
    return;
  }
  render(
    list,
    h(
      "p",
      { class: "muted small", id: "patients-count" },
      `${page.total} patient${page.total === 1 ? "" : "s"}${q ? ` matching “${q}”` : ""}` +
        (page.total > PAGE ? ` · showing ${offset + 1}–${Math.min(offset + PAGE, page.total)}` : ""),
    ),
  );
  if (!page.items.length) {
    list.append(h("p", { class: "empty" }, q ? "No patient matches." : "No patients registered yet."));
    return;
  }
  list.append(
    h(
      "ul",
      { class: "rows" },
      page.items.map((p) =>
        h(
          "li",
          {},
          h(
            "a",
            { class: "row patient", href: `#/patients/${p.id}`, dataset: { id: String(p.id) } },
            h(
              "div",
              { class: "row-main" },
              h("strong", {}, `${p.last_name}, ${p.first_name}`),
              h("span", { class: "muted small" }, ` born ${formatDate(p.date_of_birth)} (${ageOn(p.date_of_birth)})`),
            ),
            h("div", { class: "row-meta" }, h("span", { class: "mono small" }, p.mrn)),
            h("div", { class: "muted small" }, `Registered ${formatDate(p.created_at)}`),
          ),
        ),
      ),
    ),
  );
  if (page.total > PAGE) {
    list.append(
      h(
        "div",
        { class: "actions pager" },
        button("← Previous", {
          class: "btn",
          disabled: offset === 0,
          onclick: () => (location.hash = hashFor("patients", { q, offset: Math.max(0, offset - PAGE) || "" })),
        }),
        h("span", { class: "spacer" }),
        button("Next →", {
          class: "btn",
          disabled: offset + PAGE >= page.total,
          onclick: () => (location.hash = hashFor("patients", { q, offset: offset + PAGE })),
        }),
      ),
    );
  }
}

const SUBPAGES = [
  ["record", "Record", "The record, insurance and appointments"],
  [
    "clinical",
    "Clinical",
    "Medications, allergies, medical history, prescriptions, lab and imaging results, referral notes",
  ],
  [
    "reports",
    "Immunizations & reports",
    "The vaccines on record; the immunization record and annual health examination reports (signed by a doctor)",
  ],
  ["payment-methods", "Payment methods", "The cards, wallets and bank accounts on the patient's account"],
  ["payments", "Payment history", "Every hold, charge, refund and validation across all payment methods"],
];

function subnav(p, active) {
  return h(
    "nav",
    { class: "subnav", id: "patient-subnav", "aria-label": "Sections of this record" },
    SUBPAGES.map(([key, label, title]) =>
      h(
        "a",
        {
          class: `subnav-link ${key === active ? "active" : ""}`,
          id: `patient-sub-${key}`,
          href: `#/patients/${p.id}${key === "record" ? "" : "/" + key}`,
          title,
          "aria-current": key === active ? "page" : null,
        },
        label,
      ),
    ),
  );
}

async function renderDetail(root, id, loaded = null, sub = "record") {
  render(root, spinner());
  let p = loaded;
  try {
    if (!p) p = await api.get(`/patients/${id}`);
  } catch (e) {
    render(
      root,
      callout("error", "Could not load the patient", h("span", {}, describeError(e))),
      button("← Back", { onclick: () => history.back() }),
    );
    return;
  }
  const address = [p.address_line, [p.postal_code, p.city].filter(Boolean).join(" "), p.state, p.country]
    .filter(Boolean)
    .join(", ");
  render(
    root,
    h(
      "header",
      { class: "page-header" },
      h(
        "div",
        {},
        h("h2", { id: "patient-name" }, `${p.first_name} ${p.last_name}`),
        h(
          "p",
          { class: "muted small" },
          h("span", { class: "mono", id: "patient-mrn" }, p.mrn),
          ` · born ${formatDate(p.date_of_birth)} (${ageOn(p.date_of_birth)})`,
          p.sex ? ` · ${p.sex}` : "",
        ),
      ),
      h(
        "div",
        { class: "actions" },
        button("← All patients", { class: "btn", onclick: () => (location.hash = "#/patients") }),
        button("✎ Edit", {
          class: "btn primary",
          id: "btn-patient-edit",
          title: "Correct the record: name, date of birth, contact details, insurance, card photos",
          onclick: () => {
            const form = patientEditForm(p, {
              onSaved: (record) => void renderDetail(root, id, record, sub),
              onCancel: () => void renderDetail(root, id, p, sub),
            });
            render(root, form);
            form.scrollIntoView({ block: "start" });
          },
        }),
        button("Agenda", {
          class: "btn",
          id: "btn-patient-agenda",
          title: "This patient's upcoming appointments on the agenda",
          onclick: () => (location.hash = hashFor("appointments", { patient: String(p.id) })),
        }),
      ),
    ),
    subnav(p, sub),
  );
  if (sub === "payment-methods") {
    root.append(paymentMethodsSection(p));
    return;
  }
  if (sub === "payments") {
    root.append(paymentHistorySection(p));
    return;
  }
  if (sub === "reports") {
    // The reports' drafts follow the record: redraw them when an immunization is added / corrected.
    let reports = patientReportsSection(p);
    const redraw = () => {
      const fresh = patientReportsSection(p);
      reports.replaceWith(fresh);
      reports = fresh;
    };
    root.append(immunizationsSection(p, { onChange: redraw }), reports);
    return;
  }
  if (sub === "clinical") {
    root.append(
      healthSection(p, MEDS_AND_ALLERGIES),
      healthSection(p, MEDICAL_HISTORY),
      patientPharmacySection(p),
      prescriptionsSection(p),
      resultsSection(p),
      referralsSection(p),
    );
    return;
  }
  root.append(
    h(
      "section",
      { class: "card" },
      h("h3", {}, "Record"),
      record([
        ["E-mail", p.email],
        ["Phone", p.phone],
        ["Address", address, true],
        ["Emergency contact", [p.emergency_contact_name, p.emergency_contact_phone].filter(Boolean).join(" · ")],
        ["Reason for first visit", p.reason_for_visit, true],
        ["Registered", formatDateTime(p.created_at)],
        ["Last updated", formatDateTime(p.updated_at)],
      ]),
      h(
        "p",
        { class: "muted small" },
        "Patients can update their own details from the registration page (after a code to the contact details on file, or signed in). Everything else — including the date of birth — is corrected here with Edit.",
      ),
    ),
    insuranceSection(p),
  );
  const history = h("section", { class: "card", id: "patient-appointments" }, h("h3", {}, "Appointments"), spinner());
  root.append(history);
  try {
    const page = await api.get(`/appointments?patient_id=${p.id}&limit=500`);
    const items = [...page.items].sort((a, b) => b.start_at.localeCompare(a.start_at)); // newest first
    const upcoming = items.filter((a) => a.status === "scheduled" && new Date(a.end_at) >= new Date()).length;
    render(
      history,
      h("h3", {}, "Appointments ", h("span", { class: "muted small" }, `· ${items.length} (${upcoming} upcoming)`)),
      items.length
        ? h(
            "ul",
            { class: "rows" },
            items.map((a) => appointmentRow(a, { showDay: true })),
          )
        : h("p", { class: "empty" }, "No appointments yet."),
    );
  } catch (e) {
    render(history, h("h3", {}, "Appointments"), callout("error", "Could not load", h("span", {}, describeError(e))));
  }
}

const RELATIONSHIP = {
  self: "the patient",
  spouse: "spouse / partner",
  parent: "parent / guardian",
  child: "child",
  other: "someone else",
};

/**
 * What the patient entered in the Insurance section of the registration page, and the photos
 * of the card (front / back) they uploaded or took with the camera. The photos need the bearer
 * token, so they are fetched as blobs (api.blobUrl) rather than linked in an <img src>.
 */
function insuranceSection(p) {
  const cards = p.insurance_cards || [];
  const hasDetails = p.insurance_provider || p.insurance_plan_name || p.insurance_member_id || p.insurance_group_number;
  const section = h("section", { class: "card", id: "patient-insurance" }, h("h3", {}, "Insurance"));
  if (!hasDetails && !cards.length) {
    section.append(
      h(
        "p",
        { class: "empty" },
        "No insurance details given at registration (self-pay, or the patient brings the card to the front desk).",
      ),
      verificationBlock(p),
    );
    return section;
  }
  const holder =
    p.insurance_policyholder_relationship && p.insurance_policyholder_relationship !== "self"
      ? [
          p.insurance_policyholder_name,
          RELATIONSHIP[p.insurance_policyholder_relationship] || p.insurance_policyholder_relationship,
        ]
          .filter(Boolean)
          .join(" — ")
      : p.insurance_policyholder_relationship === "self"
        ? "The patient"
        : null;
  section.append(
    record([
      ["Insurance company", p.insurance_provider],
      ["Plan", p.insurance_plan_name],
      ["Member ID", p.insurance_member_id ? h("span", { class: "mono" }, p.insurance_member_id) : null],
      ["Group number", p.insurance_group_number ? h("span", { class: "mono" }, p.insurance_group_number) : null],
      ["Policyholder", holder],
    ]),
  );
  section.append(verificationBlock(p));
  if (!cards.length) {
    section.append(h("p", { class: "muted small" }, "No photo of the insurance card was uploaded."));
    return section;
  }
  const gallery = h("div", { class: "insurance-cards", id: "patient-insurance-cards" });
  for (const card of cards) {
    const figure = h(
      "figure",
      { class: "insurance-card", dataset: { side: card.side } },
      spinner("Loading photo…"),
      h(
        "figcaption",
        { class: "muted small" },
        `${card.side === "front" ? "Front" : "Back"} · ${card.content_type.replace("image/", "").toUpperCase()} · ` +
          `${Math.max(1, Math.round(card.size_bytes / 1024))} KB · uploaded ${formatDateTime(card.uploaded_at)}`,
      ),
    );
    gallery.append(figure);
    void api
      .blobUrl(card.url)
      .then((url) => {
        const img = h("img", { src: url, alt: `${card.side} of the insurance card` });
        const link = h(
          "a",
          { href: url, target: "_blank", rel: "noopener", title: "Open full size in a new tab" },
          img,
        );
        figure.replaceChild(link, figure.firstChild);
      })
      .catch((e) => {
        figure.replaceChild(
          callout("error", "Could not load the photo", h("span", {}, describeError(e))),
          figure.firstChild,
        );
      });
  }
  section.append(
    gallery,
    h("p", { class: "muted small" }, "Click a photo to open it full size. Photos are only visible to administrators."),
  );
  return section;
}

// ---------------------------------------------------------------------------- coverage verification

const STATUS_LABEL = {
  active: "Coverage active",
  inactive: "Coverage not active",
  pending: "Coverage starts later",
  not_found: "Member not found",
  incomplete: "Not enough details to check",
  unavailable: "Could not be checked",
};
const DEDUCTIBLE_LABEL = { met: "met", partially_met: "partially met", not_met: "not met", none: "none" };

function money(cents, currency = "USD") {
  if (cents === null || cents === undefined) return "—";
  try {
    return new Intl.NumberFormat(undefined, { style: "currency", currency }).format(cents / 100);
  } catch {
    return `${(cents / 100).toFixed(2)} ${currency}`;
  }
}

function requirement(flag, notes) {
  if (flag === null || flag === undefined) return "Unknown";
  return h("span", {}, h("strong", {}, flag ? "Required" : "Not required"), notes ? ` — ${notes}` : "");
}

/**
 * The newest eligibility & benefits check (coverage, effective dates, copay, deductible,
 * coinsurance, referral / prior authorization) with "Verify now" (POST …/insurance/verify —
 * the built-in fake outside production) and the history of earlier checks.
 */
function verificationBlock(p) {
  const block = h("div", { class: "verification-block", id: "patient-verification" });
  const renderCheck = (v) => {
    const head = h(
      "div",
      { class: "card-head" },
      h("strong", {}, "Coverage check"),
      h(
        "div",
        { class: "actions" },
        button("Verify now", {
          class: "btn",
          id: "btn-verify-insurance",
          title: "Ask the insurer (or the built-in fake) about coverage and benefits now",
          onclick: async (e) => {
            const btn = e.currentTarget;
            btn.disabled = true;
            btn.textContent = "Checking…";
            try {
              const fresh = await api.post(`/patients/${p.id}/insurance/verify`);
              toast(`Coverage check: ${STATUS_LABEL[fresh.status] || fresh.status}`);
              renderCheck(fresh);
            } catch (err) {
              toast(describeError(err), { error: true });
              btn.disabled = false;
              btn.textContent = "Verify now";
            }
          },
        }),
        button("History", {
          class: "btn",
          id: "btn-verification-history",
          title: "Every check so far",
          onclick: () => void showHistory(p, block),
        }),
      ),
    );
    if (!v) {
      render(
        block,
        head,
        h("p", { class: "muted small", id: "verification-none" }, "Coverage has not been checked yet."),
      );
      return;
    }
    const details =
      v.status === "active" || v.status === "pending" || v.status === "inactive"
        ? record([
            [
              "Plan",
              [v.payer_name, v.plan_name].filter(Boolean).join(" · ") + (v.plan_type ? ` (${v.plan_type})` : ""),
            ],
            [
              "Effective dates",
              `${v.coverage_start ? formatDate(v.coverage_start) : "…"} – ${v.coverage_end ? formatDate(v.coverage_end) : "open-ended"}`,
            ],
            ...(v.status === "active"
              ? [
                  [
                    "Copay",
                    `${money(v.copay_cents, v.currency)} office visit` +
                      (v.specialist_copay_cents !== null
                        ? ` · ${money(v.specialist_copay_cents, v.currency)} specialist`
                        : ""),
                  ],
                  [
                    "Deductible",
                    v.deductible_cents === null
                      ? "—"
                      : v.deductible_status === "none"
                        ? "none"
                        : `${money(v.deductible_met_cents, v.currency)} of ${money(v.deductible_cents, v.currency)} met · ` +
                          `${money(v.deductible_remaining_cents, v.currency)} remaining (${DEDUCTIBLE_LABEL[v.deductible_status] || v.deductible_status})`,
                  ],
                  [
                    "Coinsurance",
                    v.coinsurance_percent === null
                      ? "—"
                      : `${v.coinsurance_percent}% patient share after the deductible`,
                  ],
                  [
                    "Out-of-pocket max",
                    v.out_of_pocket_max_cents === null
                      ? null
                      : `${money(v.out_of_pocket_met_cents, v.currency)} of ${money(v.out_of_pocket_max_cents, v.currency)} reached`,
                  ],
                  ["Referral", requirement(v.referral_required, v.referral_notes), true],
                  ["Prior authorization", requirement(v.prior_auth_required, v.prior_auth_notes), true],
                ]
              : []),
          ])
        : null;
    render(
      block,
      head,
      h(
        "p",
        { class: "verification-status", dataset: { status: v.status } },
        badge(
          STATUS_LABEL[v.status] || v.status,
          v.status === "active"
            ? "ok"
            : v.status === "pending"
              ? "warn"
              : v.status === "inactive" || v.status === "not_found"
                ? "bad"
                : "",
        ),
        " ",
        h("span", { id: "verification-message" }, v.message),
      ),
      details,
      h(
        "p",
        { class: "muted small" },
        `Checked ${formatDateTime(v.checked_at)} · ${v.triggered_by === "registration" ? "at registration" : v.triggered_by === "staff" ? "by the office" : v.triggered_by}` +
          ` · ${v.provider === "fake" ? "built-in fake (nothing left this machine)" : v.provider}` +
          (v.reference ? ` · ref ${v.reference}` : ""),
      ),
    );
  };
  renderCheck(p.insurance_verification);
  return block;
}

async function showHistory(p, block) {
  let rows;
  try {
    rows = await api.get(`/patients/${p.id}/insurance/verifications`);
  } catch (e) {
    toast(describeError(e), { error: true });
    return;
  }
  const old = block.querySelector("#verification-history");
  if (old) old.remove();
  block.append(
    h(
      "div",
      { id: "verification-history" },
      h("h4", {}, `Checks so far (${rows.length})`),
      rows.length
        ? h(
            "ul",
            { class: "rows" },
            rows.map((v) =>
              h(
                "li",
                { class: "row" },
                h(
                  "div",
                  { class: "row-main" },
                  h("strong", {}, STATUS_LABEL[v.status] || v.status),
                  " ",
                  h("span", { class: "muted small" }, v.message),
                ),
                h(
                  "div",
                  { class: "muted small" },
                  `${formatDateTime(v.checked_at)} · ${v.triggered_by} · ${v.provider}`,
                ),
              ),
            ),
          )
        : h("p", { class: "empty" }, "No checks yet."),
    ),
  );
}
