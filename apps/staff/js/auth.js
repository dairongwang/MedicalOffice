import { api, ApiError } from "./api.js";
import { deriveAuthKey, newAccountKeys } from "./crypto.js";

/**
 * Sign-in to the staff dashboard with an account of the Medic Office API:
 *
 *  - a **manager** (role admin) — the Applications, Onboarding and Roster tabs;
 *  - a **member of staff** (role staff, linked to their staff record — staff_accounts.py) — their
 *    own appointments ("My appointments") and week ("My availability");
 *  - a **medical assistant** (a member of staff whose record has the role medical_assistant) — the
 *    tasks the office assigned them ("My tasks": take · complete · report they cannot do it).
 *
 * Patient accounts and plain app accounts are turned away. `staff` is the signed-in worker's
 * GET /staff/me (null for a manager who is not on the roster).
 */
export const session = {
  me: null,
  staff: null,
  get signedIn() {
    return !!api.token && !!this.me;
  },
  get isAdmin() {
    return this.me?.role === "admin";
  },
  /** Signed in and linked to an active staff record (their appointments, their week). */
  get isStaff() {
    return this.signedIn && !!this.staff;
  },
  /** A member of staff whose roster record is a medical assistant ("My tasks"). */
  get isAssistant() {
    return this.isStaff && this.staff?.worker?.role === "medical_assistant";
  },
  /** Where this session starts (a tab id): managers on Applications, medical assistants on My
   *  tasks, other members of staff on My appointments, everybody else on Apply. */
  get home() {
    if (this.isAdmin) return "applications";
    if (this.isAssistant) return "my-tasks";
    if (this.isStaff) return "my-appointments";
    return "apply";
  },
  /** "Dr. Alex Example" / the e-mail. */
  get displayName() {
    const w = this.staff?.worker;
    if (!w) return this.me?.email || "";
    return `${w.role === "doctor" ? "Dr. " : ""}${w.first_name} ${w.last_name}`;
  },

  async restore() {
    if (!api.token) return null;
    try {
      this.me = await api.get("/me");
      await this.loadStaff();
      if (!this.allowed()) await this.logout();
    } catch (e) {
      if (!(e instanceof ApiError) || e.status !== 0) api.setToken(null);
      this.me = null;
      this.staff = null;
    }
    return this.me;
  },

  /** The linked staff record, if any (403 for accounts that are not linked / inactive). */
  async loadStaff() {
    this.staff = null;
    if (this.me?.role !== "staff" && this.me?.role !== "admin") return;
    try {
      this.staff = await api.get("/staff/me");
    } catch (e) {
      if (!(e instanceof ApiError) || e.status !== 403) throw e;
      this.staffProblem = e.message;
    }
  },

  allowed() {
    return this.isAdmin || !!this.staff;
  },

  /** Password step. Returns { ok: true } or { twoFactor: challenge } when a code is needed. */
  async login(email, password) {
    const auth_key = await deriveAuthKey(password, email);
    const res = await api.post(
      "/auth/login",
      { email, auth_key, device: { platform: "staff-dashboard" } },
      { auth: false },
    );
    if (res?.two_factor_required) return { twoFactor: res };
    return this.finish(res);
  },

  async loginCode(challenge, code) {
    const res = await api.post(
      "/auth/login/2fa",
      { challenge, code, device: { platform: "staff-dashboard" } },
      { auth: false },
    );
    return this.finish(res);
  },

  async finish(sessionOut) {
    api.setToken(sessionOut.token);
    this.me = await api.get("/me");
    this.staffProblem = null;
    await this.loadStaff();
    if (!this.allowed()) {
      const problem =
        this.me.role === "staff" && this.staffProblem
          ? this.staffProblem
          : "This account cannot use the staff dashboard. Managers sign in with their administrator account; " +
            "members of staff create a staff account with their staff number and work e-mail.";
      await this.logout();
      throw new ApiError(problem, 403);
    }
    return { ok: true };
  },

  /** Create a staff account, step 1: a code goes to the work e-mail on the staff record. */
  async startAccount(staffNumber, email) {
    return api.post("/staff/account/verify", { staff_number: staffNumber, email }, { auth: false });
  },

  /** Step 2: the code + the new password (only its derived keys are sent) → signed in. */
  async createAccount(enrollment, code, email, password) {
    const keys = await newAccountKeys(password, email);
    const out = await api.post(
      "/staff/account",
      { enrollment, code, ...keys, device: { platform: "staff-dashboard" } },
      { auth: false },
    );
    return this.finish(out);
  },

  async logout() {
    try {
      if (api.token) await api.post("/auth/logout");
    } catch {
      /* already gone */
    }
    api.setToken(null);
    this.me = null;
    this.staff = null;
  },
};
