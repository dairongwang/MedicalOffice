/**
 * Thin fetch wrapper for the Medic Office API. The API origin comes from (first wins):
 * `?api=` in the URL (saved), localStorage, `window.ADMIN_CONFIG.apiBase` (written by serve.py /
 * the container entrypoint), or the page's own origin.
 */

const TOKEN_KEY = "admin.token";
const API_KEY = "admin.apiBase";

export class ApiError extends Error {
  constructor(message, status, body = null) {
    super(message);
    this.status = status;
    this.body = body;
  }
  get offline() {
    return this.status === 0;
  }
}

function resolveBase() {
  const fromUrl = new URLSearchParams(location.search).get("api");
  if (fromUrl !== null) {
    if (fromUrl) localStorage.setItem(API_KEY, fromUrl.replace(/\/$/, ""));
    else localStorage.removeItem(API_KEY);
  }
  const stored = localStorage.getItem(API_KEY);
  const configured = window.ADMIN_CONFIG?.apiBase;
  return (stored || configured || "").replace(/\/$/, "");
}

export const api = {
  base: resolveBase() + "/api/v1",
  get token() {
    return localStorage.getItem(TOKEN_KEY);
  },
  setToken(token) {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  },

  async request(method, path, body, { auth = true } = {}) {
    const headers = { Accept: "application/json" };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    if (auth && this.token) headers.Authorization = `Bearer ${this.token}`;
    let res;
    try {
      res = await fetch(this.base + path, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
        cache: "no-store",
      });
    } catch {
      throw new ApiError("The office's server can't be reached. Check your connection.", 0);
    }
    if (res.status === 204) return null;
    let data = null;
    try {
      data = await res.json();
    } catch {
      /* no body */
    }
    if (!res.ok) {
      if (res.status === 401 && auth && this.token) {
        this.setToken(null);
        document.dispatchEvent(new CustomEvent("admin:signed-out"));
      }
      throw new ApiError(describe(data, res.status), res.status, data);
    }
    return data;
  },
  get(path, opts) {
    return this.request("GET", path, undefined, opts);
  },
  post(path, body = {}, opts) {
    return this.request("POST", path, body, opts);
  },
  put(path, body, opts) {
    return this.request("PUT", path, body, opts);
  },
  patch(path, body, opts) {
    return this.request("PATCH", path, body, opts);
  },
  delete(path, opts) {
    return this.request("DELETE", path, undefined, opts);
  },

  /** The API origin (without /api/v1) — to turn the API's own relative links (/api/v1/…) into URLs. */
  get origin() {
    return this.base.replace(/\/api\/v1$/, "");
  },

  /**
   * A binary resource that needs the bearer token (an `<img src>` cannot send one): fetched as
   * a Blob and handed back as an object URL. Caller revokes it (URL.revokeObjectURL) when done.
   */
  async blobUrl(url) {
    const headers = {};
    if (this.token) headers.Authorization = `Bearer ${this.token}`;
    let res;
    try {
      res = await fetch(url.startsWith("/") ? this.origin + url : url, { headers, cache: "no-store" });
    } catch {
      throw new ApiError("The office's server can't be reached. Check your connection.", 0);
    }
    if (!res.ok) {
      let data = null;
      try {
        data = await res.json();
      } catch {
        /* no body */
      }
      throw new ApiError(describe(data, res.status), res.status, data);
    }
    return URL.createObjectURL(await res.blob());
  },
};

function describe(data, status) {
  const detail = data?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail))
    return detail.map((d) => `${(d.loc || []).slice(1).join(".")}: ${d.msg}`.replace(/^: /, "")).join("; ");
  if (status === 429) return "Too many requests — please wait a few minutes.";
  if (status >= 500) return "The server ran into a problem. Please try again later.";
  return `Request failed (${status})`;
}
