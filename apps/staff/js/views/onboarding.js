import { api } from "../api.js";
import {
  button,
  callout,
  describeError,
  field,
  formatDate,
  formatDateTime,
  h,
  input,
  render,
  spinner,
  statusBadge,
  toast,
} from "../ui.js";
import { roleLabel } from "../validation.js";
import { signaturePad } from "./signature-pad.js";

/**
 * Managers: onboarding of hired staff — one checklist per person (employment application,
 * offer letter, background screening, reference check, I-9, W-4, payroll, handbook, HIPAA,
 * OSHA, emergency contact). Route: #/onboarding  or  #/onboarding/<id>
 */
export function onboardingView(params) {
  const root = h("section", { class: "page wide" });
  if (params.id) void renderDetail(root, Number(params.id));
  else void renderList(root, params.state || "in_progress");
  return root;
}

async function renderList(root, state) {
  const chips = [
    ["in_progress", "In progress"],
    ["completed", "Completed"],
    ["all", "All"],
  ];
  render(
    root,
    h(
      "header",
      { class: "page-header" },
      h(
        "div",
        {},
        h("h2", {}, "Onboarding"),
        h(
          "p",
          { class: "muted small" },
          "Everyone hired goes through the same checklist. Tick steps off as paperwork comes in.",
        ),
      ),
    ),
    h(
      "div",
      { class: "toolbar" },
      h(
        "div",
        { class: "chips" },
        chips.map(([value, label]) =>
          button(label, {
            class: `chip ${value === state ? "active" : ""}`,
            onclick: () => (location.hash = `#/onboarding?state=${value}`),
            dataset: { filter: value },
          }),
        ),
      ),
    ),
  );
  const list = h("div", { id: "ob-list" }, spinner());
  root.append(list);
  try {
    const page = await api.get(`/onboarding?state=${state}`);
    root
      .querySelector('[data-filter="in_progress"]')
      .append(h("span", { class: "count" }, String(page.in_progress)));
    root
      .querySelector('[data-filter="completed"]')
      .append(h("span", { class: "count" }, String(page.completed)));
    root.querySelector('[data-filter="all"]').append(h("span", { class: "count" }, String(page.total)));
    list.replaceChildren();
    if (!page.items.length) {
      list.append(
        h(
          "p",
          { class: "empty" },
          state === "in_progress"
            ? "Nobody is being onboarded right now. Hire someone from the Applications tab."
            : "Nothing here yet.",
        ),
      );
      return;
    }
    list.append(
      h(
        "ul",
        { class: "rows" },
        page.items.map((o) =>
          h(
            "li",
            {},
            h(
              "a",
              { class: "row", href: `#/onboarding/${o.id}` },
              h(
                "div",
                { class: "row-main" },
                h("strong", {}, `${o.worker.first_name} ${o.worker.last_name}`),
                h(
                  "span",
                  { class: "muted small" },
                  ` ${roleLabel(o.worker.role)} · `,
                  h("span", { class: "mono" }, o.worker.staff_number),
                ),
              ),
              progress(o),
              h(
                "div",
                { class: "muted small" },
                o.completed_at
                  ? `Completed · approved ${formatDate(o.approved_at)}`
                  : o.all_steps_done
                    ? "All steps done — awaiting manager approval"
                    : o.next_step
                      ? `Next: ${o.next_step}`
                      : `Started ${formatDate(o.started_at)}`,
              ),
            ),
          ),
        ),
      ),
    );
  } catch (e) {
    render(list, callout("error", "Could not load onboarding", h("span", {}, describeError(e))));
  }
}

function progress(o) {
  return h(
    "div",
    { class: "progress-row" },
    h(
      "div",
      {
        class: "progress",
        role: "progressbar",
        "aria-valuenow": o.percent,
        "aria-valuemin": 0,
        "aria-valuemax": 100,
      },
      h("div", { class: "progress-bar", style: `width:${o.percent}%` }),
    ),
    h("span", { class: "small" }, `${o.done}/${o.total} · ${o.percent}%`),
  );
}

async function renderDetail(root, id) {
  render(root, spinner());
  let o;
  try {
    o = await api.get(`/onboarding/${id}`);
  } catch (e) {
    render(
      root,
      callout("error", "Could not load", h("span", {}, describeError(e))),
      button("← Back", { onclick: () => history.back() }),
    );
    return;
  }
  const msg = h("div", { id: "ob-msg" });
  const refresh = () => renderDetail(root, id);

  const notes = h("textarea", {
    class: "input",
    rows: 3,
    maxlength: 4000,
    placeholder: "Notes about this onboarding (start date agreed, documents outstanding, …)",
    id: "ob-notes",
  });
  notes.value = o.notes || "";
  const saveNotes = button("Save notes", {
    id: "btn-ob-notes",
    onclick: async () => {
      try {
        await api.put(`/onboarding/${id}/notes`, { notes: notes.value.trim() || null });
        toast("Notes saved");
      } catch (e) {
        render(msg, callout("error", "Could not save notes", h("span", {}, describeError(e))));
      }
    },
  });

  const remindBtn = button(o.completed_at ? "Onboarding complete" : "Send reminder", {
    class: "btn primary",
    id: "btn-ob-remind",
    disabled: !!o.completed_at,
    title: o.completed_at
      ? "All steps are done — no reminder needed"
      : "Send the onboarding link again by e-mail / text",
    onclick: async () => {
      remindBtn.disabled = true;
      try {
        const updated = await api.post(`/onboarding/${id}/invite`, { kind: "remind" });
        const inv = updated.invites[0];
        toast(
          inv.channels.length
            ? `Reminder sent (${inv.channels.join(" + ")})`
            : "Link created — could not send; copy it from the log",
        );
        sessionStorage.setItem("staff.justSentInvite", String(inv.id));
        refresh();
      } catch (e) {
        msg.replaceChildren(callout("error", "Could not send the reminder", h("span", {}, describeError(e))));
        remindBtn.disabled = false;
      }
    },
  });
  render(
    root,
    h(
      "header",
      { class: "page-header" },
      h(
        "div",
        {},
        h("a", { href: "#/onboarding", class: "back" }, "← Onboarding"),
        h("h2", {}, `${o.worker.first_name} ${o.worker.last_name}`),
        h(
          "p",
          { class: "muted small" },
          `${roleLabel(o.worker.role)} · `,
          h("span", { class: "mono" }, o.worker.staff_number),
          o.application_number ? ` · from application ${o.application_number}` : "",
          ` · started ${formatDate(o.started_at)}`,
        ),
      ),
      remindBtn,
    ),
    msg,
    o.completed_at
      ? callout(
          "ok",
          "Onboarding complete",
          h(
            "span",
            {},
            `Approved by ${o.approval_signer_name || o.approved_by} on ${formatDateTime(o.approved_at)}`,
            o.approval_esign_reference
              ? h("span", {}, " · e-signature ", h("span", { class: "mono" }, o.approval_esign_reference))
              : null,
            ".",
          ),
        )
      : o.all_steps_done
        ? callout(
            "info",
            "All steps done — awaiting your approval",
            h("span", {}, "Sign below to confirm the onboarding is finished."),
          )
        : null,
    justSentHint(o),
    approvalCard(o, id, msg, refresh),
    inviteLog(o, id, msg, refresh),
    h(
      "section",
      { class: "card" },
      h("div", { class: "card-head" }, h("h3", {}, "Checklist"), progress(o)),
      h(
        "ol",
        { class: "checklist", id: "ob-items" },
        o.items.map((item) => itemRow(id, item, msg, refresh)),
      ),
    ),
    h(
      "section",
      { class: "card" },
      h("h3", {}, "Notes"),
      notes,
      h("div", { class: "actions" }, h("span", { class: "spacer" }), saveNotes),
    ),
  );
}

function itemRow(onboardingId, item, msg, refresh) {
  const done = item.status === "done";
  const na = item.status === "not_applicable";
  const box = h("input", { type: "checkbox", id: `ob-${item.key}`, checked: done });
  const note = input({
    placeholder: "Note (date, document, who)",
    maxlength: 500,
    value: item.note || "",
    id: `ob-note-${item.key}`,
  });
  const save = async (status) => {
    msg.replaceChildren();
    try {
      await api.put(`/onboarding/${onboardingId}/items/${item.key}`, {
        status,
        note: note.value.trim() || null,
      });
      toast(
        status === "done"
          ? `${item.label}: done`
          : status === "not_applicable"
            ? `${item.label}: not applicable`
            : `${item.label}: reopened`,
      );
      refresh();
    } catch (e) {
      box.checked = done;
      render(msg, callout("error", "Could not update the step", h("span", {}, describeError(e))));
    }
  };
  box.addEventListener("change", () => save(box.checked ? "done" : "pending"));
  note.addEventListener("change", () => save(item.status));
  const naBtn = button(na ? "Applies again" : "Not applicable", {
    class: "btn ghost small-btn",
    onclick: () => save(na ? "pending" : "not_applicable"),
  });
  const sub = item.submission;
  const submissionInfo = sub
    ? h(
        "div",
        { class: "submission small" },
        h(
          "span",
          {},
          sub.signed_at
            ? `E-signed online by ${sub.signer_name} on ${formatDateTime(sub.signed_at)} · `
            : `Submitted online ${formatDateTime(sub.submitted_at)} · `,
          sub.esign_reference ? h("span", { class: "mono" }, sub.esign_reference) : null,
          sub.document_version ? h("span", { class: "muted" }, ` · document v${sub.document_version}`) : null,
        ),
        Object.keys(sub.data || {}).length
          ? h(
              "details",
              {},
              h("summary", {}, "Submitted details"),
              h(
                "dl",
                { class: "record" },
                Object.entries(sub.data).map(([k, v]) =>
                  h(
                    "div",
                    {},
                    h("dt", {}, k.replace(/_/g, " ")),
                    h("dd", {}, v === true ? "yes" : v === false ? "no" : String(v ?? "—")),
                  ),
                ),
              ),
            )
          : null,
        sub.has_signature
          ? button("View signature", {
              class: "btn ghost small-btn",
              onclick: async (e) => {
                try {
                  const res = await fetch(
                    `${api.base}/onboarding/${onboardingId}/submissions/${item.key}/signature`,
                    {
                      headers: { Authorization: `Bearer ${api.token}` },
                    },
                  );
                  if (!res.ok) throw new Error("Could not load the signature image.");
                  const url = URL.createObjectURL(await res.blob());
                  const img = h("img", {
                    src: url,
                    alt: `Signature of ${sub.signer_name}`,
                    class: "signature-img",
                  });
                  e.target.replaceWith(img);
                } catch (err) {
                  toast(describeError(err), { error: true });
                }
              },
            })
          : null,
      )
    : null;
  return h(
    "li",
    { class: `step ${item.status} ${item.who === "hire" ? "by-hire" : ""}` },
    h(
      "label",
      { class: "step-main", for: box.id },
      box,
      h("span", { class: "step-no" }, String(item.position + 1)),
      h(
        "span",
        { class: "step-label" },
        item.label,
        item.who === "hire"
          ? h("span", { class: "muted small who-tag" }, " · new hire (portal)")
          : h("span", { class: "muted small who-tag" }, " · office"),
      ),
      statusBadge(item.status),
    ),
    submissionInfo,
    h("div", { class: "step-details" }, note, naBtn),
    item.completed_by || item.completed_at
      ? h(
          "p",
          { class: "muted small step-by" },
          `${item.completed_by ? item.completed_by + " · " : ""}${formatDateTime(item.completed_at)}`,
        )
      : null,
  );
}

/** The log of onboarding links (for handing one over manually) — newest first. */
function inviteLog(o, id, msg, refresh) {
  const list = o.invites || [];
  return h(
    "section",
    { class: "card", id: "ob-invites" },
    h(
      "div",
      { class: "card-head" },
      h("h3", {}, "Onboarding links"),
      h(
        "span",
        { class: "muted small" },
        `${list.length} sent · valid ${list[0] ? "" : ""}links expire automatically`,
      ),
    ),
    h(
      "p",
      { class: "hint" },
      "Each link was sent to the new hire by e-mail / text message and is logged here so you can hand it over manually (copy). Treat links like passwords. In development the messages land in the ",
      h("a", { href: `${api.base}/messages/fake/outbox`, target: "_blank", rel: "noopener" }, "fake outbox"),
      ".",
    ),
    list.length
      ? h(
          "ul",
          { class: "invites" },
          list.map((inv) =>
            h(
              "li",
              { class: `invite ${inv.valid ? "" : "dead"}` },
              h(
                "div",
                { class: "invite-main" },
                h(
                  "strong",
                  {},
                  { hire: "Hired", start: "Started", remind: "Reminder", manual: "Manual" }[inv.kind] ||
                    inv.kind,
                ),
                h(
                  "span",
                  { class: "muted small" },
                  ` · ${formatDateTime(inv.created_at)}${inv.created_by ? " · " + inv.created_by : ""}`,
                ),
                " ",
                inv.revoked_at
                  ? statusBadge("withdrawn")
                  : inv.expired
                    ? h("span", { class: "badge bad" }, "Expired")
                    : h("span", { class: "badge ok" }, "Valid"),
              ),
              h(
                "div",
                { class: "muted small" },
                inv.channels.length
                  ? `Sent via ${inv.channels.join(" + ")} to ${inv.sent_to}`
                  : "Not delivered",
                ` · expires ${formatDateTime(inv.expires_at)}`,
                inv.opens
                  ? ` · opened ${inv.opens}× (last ${formatDateTime(inv.last_opened_at)})`
                  : " · not opened yet",
              ),
              inv.delivery_note ? h("div", { class: "hint" }, inv.delivery_note) : null,
              h(
                "div",
                { class: "invite-link" },
                h("input", {
                  class: "input mono small",
                  readonly: true,
                  value: inv.link,
                  "aria-label": "Onboarding link",
                  onclick: (e) => e.target.select(),
                }),
                button("Copy", {
                  class: "btn small-btn",
                  onclick: async () => {
                    try {
                      await navigator.clipboard.writeText(inv.link);
                      toast("Link copied");
                    } catch {
                      toast("Select the link and copy it manually", { error: true });
                    }
                  },
                }),
                inv.valid
                  ? button("Revoke", {
                      class: "btn ghost small-btn",
                      onclick: async () => {
                        if (!confirm("Revoke this link? The new hire will need a new one.")) return;
                        try {
                          await api.post(`/onboarding/${id}/invites/${inv.id}/revoke`);
                          toast("Link revoked");
                          refresh();
                        } catch (e) {
                          msg.replaceChildren(
                            callout("error", "Could not revoke", h("span", {}, describeError(e))),
                          );
                        }
                      },
                    })
                  : null,
              ),
            ),
          ),
        )
      : h("p", { class: "empty" }, "No link sent yet — use Send reminder."),
  );
}

/** Right after a link was generated: where the new hire / the manager finds it. */
function justSentHint(o) {
  const justId = sessionStorage.getItem("staff.justSentInvite");
  const inv = (o.invites || []).find((i) => String(i.id) === justId);
  if (!inv) return null;
  sessionStorage.removeItem("staff.justSentInvite");
  return callout(
    inv.fake_delivery ? "warn" : "ok",
    inv.fake_delivery ? "Link generated — where to find it (fake delivery)" : "Link sent — where to find it",
    h("span", { id: "invite-hint" }, inv.where_to_find),
    inv.fake_delivery
      ? h(
          "span",
          {},
          "Open the ",
          h(
            "a",
            { href: `${api.base}/messages/fake/outbox`, target: "_blank", rel: "noopener" },
            "test inbox",
          ),
          " or look at the API console; the link itself: ",
          h("span", { class: "mono small" }, inv.link),
        )
      : null,
  );
}

/** The manager's electronic sign-off: "onboarding is finished". */
function approvalCard(o, id, msg, refresh) {
  if (o.completed_at) {
    const viewBtn = button("View approval signature", {
      class: "btn ghost small-btn",
      id: "btn-approval-signature",
      onclick: async (e) => {
        try {
          const res = await fetch(`${api.base}/onboarding/${id}/approval/signature`, {
            headers: { Authorization: `Bearer ${api.token}` },
          });
          if (!res.ok) throw new Error("Could not load the signature image.");
          e.target.replaceWith(
            h("img", {
              src: URL.createObjectURL(await res.blob()),
              alt: "Approval signature",
              class: "signature-img",
            }),
          );
        } catch (err) {
          toast(describeError(err), { error: true });
        }
      },
    });
    return h(
      "section",
      { class: "card approval", id: "ob-approval" },
      h("h3", {}, "Approval"),
      h(
        "p",
        { class: "small" },
        `Signed by ${o.approval_signer_name} (${o.approved_by}) on ${formatDateTime(o.approved_at)}.`,
      ),
      o.approval_esign_reference
        ? h(
            "p",
            { class: "hint" },
            "E-signature reference: ",
            h("span", { class: "mono" }, o.approval_esign_reference),
          )
        : null,
      viewBtn,
      h(
        "p",
        { class: "hint" },
        "Reopening any step withdraws this approval; the onboarding then has to be signed off again.",
      ),
    );
  }
  if (!o.all_steps_done) {
    return h(
      "section",
      { class: "card approval", id: "ob-approval" },
      h("h3", {}, "Approval"),
      h(
        "p",
        { class: "small muted" },
        "Once every step is done, sign here to approve that the onboarding is finished.",
      ),
      button("Sign & approve", {
        class: "btn primary",
        id: "btn-approve",
        disabled: true,
        title: "Every step must be done first",
      }),
    );
  }
  const pad = signaturePad();
  const name = input({
    placeholder: "Your full name",
    maxlength: 200,
    autocomplete: "name",
    id: "approve-name",
  });
  const consent = h("input", { type: "checkbox", id: "approve-consent" });
  const note = input({ placeholder: "Note (optional)", maxlength: 1000, id: "approve-note" });
  const err = h("div");
  const submit = button("Sign & approve onboarding", {
    type: "submit",
    class: "btn primary",
    id: "btn-approve",
  });
  const form = h(
    "form",
    { class: "portal-form", id: "approve-form", novalidate: true },
    h(
      "p",
      { class: "small muted" },
      "I confirm that every onboarding step has been completed and verified, and that this person's onboarding is finished.",
    ),
    pad.el,
    field("Type your full name *", name),
    h(
      "label",
      { class: "checkbox consent", for: "approve-consent" },
      consent,
      h("span", {}, "I agree to sign this approval electronically. *"),
    ),
    field("Note", note, { optional: true }),
    err,
    h("div", { class: "actions" }, h("span", { class: "spacer" }), submit),
  );
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    err.replaceChildren();
    const problems = [];
    if (pad.isEmpty()) problems.push("Please draw your signature.");
    if (!name.value.trim()) problems.push("Please type your full name.");
    if (!consent.checked) problems.push("Please agree to sign electronically.");
    if (problems.length)
      return err.append(
        callout(
          "error",
          "Please check",
          h(
            "ul",
            {},
            problems.map((p) => h("li", {}, p)),
          ),
        ),
      );
    submit.disabled = true;
    try {
      await api.post(`/onboarding/${id}/approve`, {
        signature: { signer_name: name.value.trim(), image_png: pad.toDataUrl(), consent: true },
        note: note.value.trim() || null,
      });
      toast("Onboarding approved and complete");
      refresh();
    } catch (e2) {
      err.append(callout("error", "Could not approve", h("span", {}, describeError(e2))));
      submit.disabled = false;
    }
  });
  return h(
    "section",
    { class: "card approval", id: "ob-approval" },
    h("h3", {}, "Approval — sign to finish"),
    form,
  );
}
