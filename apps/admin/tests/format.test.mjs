// Pure helpers of the administration dashboard: `node --test apps/admin/tests` or `python dev.py test`.
import assert from "node:assert/strict";
import { test } from "node:test";

import { addDays, ageOn, describeSentTo, formatNumber, hashFor, isoDate, roleLabel, splitLocal } from "../js/format.js";

test("dates", () => {
  assert.equal(isoDate(new Date(2027, 5, 7, 23, 30)), "2027-06-07");
  assert.equal(addDays("2027-06-30", 1), "2027-07-01");
  assert.equal(ageOn("1985-03-14", new Date(2027, 2, 13)), 41);
  assert.equal(ageOn("1985-03-14", new Date(2027, 2, 14)), 42);
  assert.equal(ageOn("nope"), null);
});

test("office-time labels and hashes", () => {
  assert.deepEqual(splitLocal("Monday, June 7, 2027 · 09:30"), { day: "Monday, June 7, 2027", time: "09:30" });
  assert.equal(hashFor("appointments", { from: "2027-06-07", to: "", q: "Pat" }), "#/appointments?from=2027-06-07&q=Pat");
  assert.equal(hashFor("patients", {}), "#/patients");
});

test("labels", () => {
  assert.equal(describeSentTo(["email:p•••@example.com", "sms:+•••0301"]), "e-mail p•••@example.com, text +•••0301");
  assert.equal(formatNumber("P", "p42"), "P-000042");
  assert.equal(formatNumber("APT", "APT-000007"), "APT-000007");
  assert.equal(roleLabel("physician_assistant"), "Physician assistant");
});
