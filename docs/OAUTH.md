# Sign in with Google / Facebook / Apple (OAuth 2.0)

Users can create an account and sign in with their **Google** (Gmail), **Facebook** or
**Apple** account instead of typing a password — and password accounts can **link** a provider
as an additional way in. Standard OAuth 2.0 authorization-code flow (OpenID Connect for Google
and Apple), implemented in `services/account/oauth.py` with the standard library (plus the
`cryptography` package already used for the key wrapping, for Apple's JWTs); no provider SDKs.

## What the provider does — and what it cannot do

This app is zero-knowledge: the server never sees the password, because the password does
two jobs on the device — it proves who you are (the auth key) **and** it derives the key that
wraps your data key (`PLATFORM_DESIGN.md` §3.2). Google or Facebook can take over the first job
only. They cannot derive an encryption key for you, and we will not keep one for you.

So a sign-in-with-provider account has a **passphrase**:

| Situation | What the user sees after the provider screen |
|---|---|
| First time (new account) | *Create your passphrase* — typed twice. The device generates the data key and wraps it with the passphrase, which also becomes the account's password (e-mail + passphrase works too). Nothing is stored until this step is done; closing the dialog revokes the half-done session. |
| Existing account, new device | *Unlock your data* — the passphrase (= the account password) unwraps the data key. A wrong one fails on the device; nothing is sent. |
| Already signed in on this device | nothing — the provider is only needed at sign-in |

The passphrase is the price of "nobody but you can read your data". The design doc
(`PLATFORM_DESIGN.md` §3.2) originally deferred provider sign-in for exactly this reason; the
screens say plainly what the passphrase is for.

## The flow

```
app ──navigation──▶ GET /api/v1/auth/oauth/{google|facebook}/start?return_to=<app url>
                    302 → provider's consent screen   (state: encrypted blob — nonce, PKCE verifier,
                                                         return_to, optional "link to account")
provider ──────────▶ GET /api/v1/auth/oauth/{provider}/callback?code&state
                    server ↔ provider: code → tokens (client secret + PKCE) → profile
                    identity linked before → that account
                    same e-mail, provider says VERIFIED → link to that account (audited)
                    same e-mail, NOT verified → refused ("sign in with your password, then link")
                    nobody → new account (no password, no data key yet)
                    302 → <app url>#oauth=<ticket>&provider=<name>      (fragment: never logged)
app ──fetch────────▶ POST /api/v1/auth/oauth/complete {ticket, device}
                    200 Session  ·  or 202 two-factor challenge (the provider was the first factor)
app                 passphrase step (create / unlock) → signed in
```

* **state** and the fake's codes are AES-GCM-sealed under `TOKEN_SECRET` (`oauth.seal`) with a
  10-minute expiry — nothing is stored server-side, any replica can handle the callback.
* The **ticket** is a signed token (`typ: "oauth"`, 90 s); `auth.decode_token` refuses it as a
  session. It travels in the URL fragment, so it never reaches server logs or `Referer` headers.
* **Google**: `accounts.google.com/o/oauth2/v2/auth` (`openid email profile`, PKCE S256,
  `prompt=select_account`) → `oauth2.googleapis.com/token` → `openidconnect.googleapis.com/v1/userinfo`
  (`sub`, `email`, `email_verified`). **Facebook**: `www.facebook.com/v19.0/dialog/oauth`
  (`email,public_profile`) → `graph.facebook.com/v19.0/oauth/access_token` → `/v19.0/me?fields=id,name,email`.
  Facebook only shares addresses it has confirmed; a profile without an e-mail is refused.
* **Apple** (`AppleProvider`): `appleid.apple.com/auth/authorize` (`name email`,
  `response_mode=form_post` — Apple **POSTs** the result to the callback, so the callback accepts
  POST too) → `appleid.apple.com/auth/token` with a **client secret we mint**: an ES256 JWT signed
  with the Sign in with Apple key (`iss` = Team ID, `sub` = Services ID, 10 minutes, fresh per
  call, `apple_client_secret`) → the profile comes from the signed **`id_token`**, verified
  against Apple's published keys (`/auth/keys`, RS256, cached an hour; issuer, audience and
  expiry checked — `verify_apple_id_token`). Apple shares verified addresses only, including
  its *Hide my email* relay addresses (`@privaterelay.appleid.com`, which still reach the user).
  The name Apple sends on the first sign-in is not stored (we keep no names).
* **Account takeover guard**: an existing account is only joined to a provider identity when
  the provider reports the e-mail as *verified*; otherwise the user signs in with the password
  and links from the account screen. A provider identity belongs to one account.
* **Two-factor authentication** applies unchanged: with a second factor on, `complete` answers
  `202` and the usual code step follows ([TWO_FACTOR.md](TWO_FACTOR.md)).
* `return_to` is restricted to the app: `WEB_APP_URL`, `DEV_WEB_URL`, the API's own origin,
  `CORS_ORIGINS` — and `localhost` outside prod. Anything else falls back to the app.

### Account screen → *Sign-in methods*

| Action | API |
|---|---|
| Link Google / Facebook | `POST /me/oauth/{provider}/link {return_to}` → `{url}`; the browser goes there and returns to `return_to#oauth_linked=<provider>` |
| Unlink | `DELETE /me/oauth/{provider}` — refused (`409`) when it is the only way in and no passphrase exists |
| Choose the passphrase (new provider account) | `POST /me/passphrase {auth_key, wrapped_data_key, kdf_params}` — once; `409` afterwards |
| Status | `GET /me` → `has_password`, `identities: [{provider, email, linked_at, last_login_at}]` |
| Which providers exist here | `GET /auth/oauth/providers` → `[{name, label, available, fake}]` |

The ads admin console and the production API-docs gate keep e-mail + password (+ second
factor) sign-in only — administrators always have a password, and those screens have no
encrypted data to unlock.

Admin panel: *Signs in with: password, Google* (and *no passphrase yet* for a half-set-up
account) — `AdminAccountDetail.has_password`, `oauth_providers`.

Audit trail: `oauth.register`, `oauth.linked` (`how`: `new_account` · `verified_email` ·
`account_screen`), `oauth.unlinked`, `passphrase.set`.

## Configuration

| Variable | Meaning |
|---|---|
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | [Google Cloud console](https://console.cloud.google.com/apis/credentials) → *OAuth client ID* (Web application). Authorised redirect URI: `<API origin>/api/v1/auth/oauth/google/callback` |
| `FACEBOOK_APP_ID` / `FACEBOOK_APP_SECRET` | [Meta for Developers](https://developers.facebook.com/apps/) → *Facebook Login* (Web). Valid OAuth Redirect URI: `<API origin>/api/v1/auth/oauth/facebook/callback`; the app needs the `email` permission |
| `APPLE_CLIENT_ID` / `APPLE_TEAM_ID` / `APPLE_KEY_ID` / `APPLE_PRIVATE_KEY` (or `APPLE_PRIVATE_KEY_FILE`) | [Apple Developer](https://developer.apple.com/account/resources/) → an App ID with *Sign in with Apple*, a **Services ID** (the client id) with the API's domain and `<API origin>/api/v1/auth/oauth/apple/callback` as Return URL, and a *Sign in with Apple* **key** (.p8 — its contents as PEM text with `\n` line breaks, or a file path) with its Key ID; plus your Team ID. Apple accepts **https** return URLs on a verified domain only — no `localhost`: test locally with the fake |
| `OAUTH_FAKE_PROVIDERS` | Built-in fake providers for those without credentials — `true` in dev/test, off in qa/staging unless set, never in prod |
| `PUBLIC_API_URL` | The API's public origin when a proxy rewrites `Host` (used for the redirect URI); otherwise taken from the request |
| `WEB_APP_URL` | Where the browser returns (the web app); default `DEV_WEB_URL`, else `/` (the app served by the API) |

`<API origin>` is the origin the browser reaches the API through: with `python dev.py run`
that is `http://localhost:4200` (the Angular dev server proxies `/api`) — register that URI;
with `dev.py serve` / Docker it is `http://localhost:8000`; in Azure the API's hostname.
A provider is offered the moment both credentials are set; the sign-in form's buttons come from
`GET /auth/oauth/providers`.

## The built-in fake providers

Without credentials (dev/test) **"Google (fake)"**, **"Facebook (fake)"** and **"Apple (fake)"**
stand in: the consent screen is a page served by this API
(`/api/v1/auth/oauth/fake/{provider}/authorize`, any e-mail address continues as that person;
a checkbox makes Google report the address as *unverified*, another gives Apple's *Hide my
email* relay address; the Apple fake posts the result back like Apple does; *Cancel* returns
`access_denied`) and the code/token/profile exchange happens
in-process through the very adapter code that talks to the real provider — so the flow you test
locally is the production code minus the network. Nothing leaves the machine. Like the Stripe,
PayPal, e-mail and SMS fakes, it is never active in prod.

## Where it lives

* `services/account/oauth.py` — providers (strategy: `GoogleProvider`, `FacebookProvider`),
  sealed state, the callback rules, linking, the passphrase, the fake consent screen.
* `services/account/httpclient.py` — the one HTTP seam (shared with the Twilio adapter).
* `apps/web/src/app/core/account/account.service.ts` — `oauthStartUrl`, `consumeOAuthReturn`,
  `completeOAuth`, `createPassphrase` / `unlockWithPassphrase`, `linkProvider` / `unlinkProvider`;
  `account-dialog.ts` — the buttons, the passphrase step, *Sign-in methods*; `app.ts` —
  `finishOAuthReturn()` on start-up.
* `oauth_identities` table (Alembic `0009_oauth_identities`): provider, the provider's user id,
  account, the e-mail the provider reported (display only). No names, pictures or tokens are kept.

## Tests

| Where | What |
|---|---|
| `services/account/tests/test_oauth.py` | Apple: the fake round trip (form post, relay address, cancel, link, join by verified e-mail), the ES256 client-secret JWT (verified with the public key), `id_token` verification (wrong audience / issuer / expiry / key / signature), the adapter with a stub transport and key caching; catalogue (fake / configured / absent), the full round trip through the fake Google and Facebook, new account → passphrase → both ways in, cancel and errors, tampered state / forged code / stale ticket, linking by verified e-mail (and refusal when unverified), link/unlink from the account screen (incl. the "only way in" guard), the second factor on top, admin view and purge, `return_to` restrictions, the Google and Facebook adapters against stub transports, sealed-state expiry |
| `e2e/test_oauth.py` | real browser: new account with Google (fake) → passphrase → signed in; new device → unlock (wrong passphrase refused); e-mail + passphrase sign-in; cancelling at the provider; a password account linking Facebook, signing in with it, unlinking; a new account with Apple (form post, *Hide my email*) |
