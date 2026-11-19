// The pure rules of the application form, the staff-account sign-up and the week editor
// (js/validation.js): `node --test apps/staff/tests` or `python dev.py test`.
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  ageCheck,
  ageOn,
  formatNumber,
  isEmail,
  isPhone,
  licenseCheck,
  normalizeTime,
  parseDate,
  passwordProblem,
  standardWeek,
  validateWeek,
} from "../js/validation.js";

const TODAY = new Date(2027, 5, 7); // June 7, 2027

test("the age check: 18 or over, a plausible date", () => {
  assert.equal(ageOn("2009-06-07", TODAY), 18);
  assert.equal(ageOn("2009-06-08", TODAY), 17);
  assert.equal(ageCheck("1990-01-01", TODAY).status, "passed");
  const young = ageCheck("2011-01-01", TODAY);
  assert.equal(young.status, "failed");
  assert.match(young.note, /minimum hiring age is 18/);
  assert.equal(ageCheck("2030-01-01", TODAY).status, "invalid");
  assert.equal(ageCheck("1900-01-01", TODAY).status, "invalid");
  assert.equal(ageCheck("not a date", TODAY).status, "invalid");
});

test("the licence check mirrors the server", () => {
  assert.equal(licenseCheck("receptionist", "", "", "", TODAY).status, "not_applicable");
  assert.equal(licenseCheck("nurse", "", "", "", TODAY).status, "failed");
  assert.equal(licenseCheck("nurse", "AB1", "", "", TODAY).status, "failed");
  assert.equal(licenseCheck("nurse", "DUMMY-RN-0001", "", "2027-01-01", TODAY).status, "failed"); // expired
  const soon = licenseCheck("doctor", "DUMMY-MD-0001", "Example Board", "2027-07-01", TODAY);
  assert.equal(soon.status, "in_progress");
  assert.match(soon.note, /expires soon/);
  const fine = licenseCheck("doctor", "DUMMY-MD-0001", "Example Board", "2029-01-01", TODAY);
  assert.match(fine.note, /Example Board/);
});

test("contact details", () => {
  assert.ok(isEmail("dummy-nurse@example.com"));
  assert.ok(!isEmail("dummy-nurse@"));
  assert.ok(isPhone(""));
  assert.ok(!isPhone("", { required: true }));
  assert.ok(isPhone("+1 555 010 0002"));
  assert.ok(!isPhone("12345"));
});

test("staff and application numbers in any spelling", () => {
  assert.equal(formatNumber("W", "3"), "W-000003");
  assert.equal(formatNumber("W", "w-42"), "W-000042");
  assert.equal(formatNumber("W", "W-000001"), "W-000001");
  assert.equal(formatNumber("A", "a7"), "A-000007");
  assert.equal(formatNumber("W", "nonsense"), "NONSENSE");
});

test("the new staff account's password", () => {
  assert.match(passwordProblem("short", "short", "dummy@example.com"), /at least 10/);
  assert.match(passwordProblem("dummy@example.com", "dummy@example.com", "dummy@example.com"), /e-mail/);
  assert.match(passwordProblem("Dummy-pass-1234", "Dummy-pass-1235", "d@example.com"), /not the same/);
  assert.equal(passwordProblem("Dummy-pass-1234", "Dummy-pass-1234", "d@example.com"), null);
});

test("the week: times, order, hours", () => {
  assert.equal(normalizeTime("9:00"), "09:00");
  assert.equal(normalizeTime("09:00:00"), "09:00");
  assert.equal(normalizeTime("25:00"), null);
  assert.ok(parseDate("2027-02-28"));
  assert.equal(parseDate("2027-02-30"), null);
  const week = standardWeek();
  assert.deepEqual(
    week.map((d) => d.available),
    [true, true, true, true, true, false, false],
  );
  assert.equal(validateWeek(week).hours, 40);
  const bad = validateWeek([{ weekday: 0, available: true, start: "17:00", end: "09:00" }]);
  assert.equal(bad.ok, false);
  assert.match(bad.errors[0], /Monday: the end time must be after the start time/);
});
