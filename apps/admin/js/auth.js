import { api, ApiError } from "./api.js";
import { deriveAuthKey } from "./crypto.js";

/** Office administrator sign-in (administrator account of the Medic Office API, role = admin). */
export const session = {
  me: null,
  get signedIn() {
    return !!api.token && !!this.me;
  },
  get isAdmin() {
    return this.me?.role === "admin";
  },

  async restore() {
    if (!api.token) return null;
    try {
      this.me = await api.get("/me");
    } catch (e) {
      if (!(e instanceof ApiError) || e.status !== 0) api.setToken(null);
      this.me = null;
    }
    return this.me;
  },

  /** Password step. Returns { ok: true } or { twoFactor: challenge } when a code is needed. */
  async login(email, password) {
    const auth_key = await deriveAuthKey(password, email);
    const res = await api.post(
      "/auth/login",
      { email, auth_key, device: { platform: "admin-dashboard" } },
      { auth: false },
    );
    if (res?.two_factor_required) return { twoFactor: res };
    return this.finish(res);
  },

  async loginCode(challenge, code) {
    const res = await api.post(
      "/auth/login/2fa",
      { challenge, code, device: { platform: "admin-dashboard" } },
      { auth: false },
    );
    return this.finish(res);
  },

  async finish(sessionOut) {
    api.setToken(sessionOut.token);
    this.me = await api.get("/me");
    if (this.me.role !== "admin") {
      await this.logout();
      throw new ApiError(
        "This account is not an administrator. The office administration dashboard needs an administrator account.",
        403,
      );
    }
    return { ok: true };
  },

  async logout() {
    try {
      if (api.token) await api.post("/auth/logout");
    } catch {
      /* already gone */
    }
    api.setToken(null);
    this.me = null;
  },
};
