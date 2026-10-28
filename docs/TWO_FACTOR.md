# Two-factor authentication

Optional per account. Users choose any combination of three second factors:

| Method | What the user needs | Provider on the server | Without a provider |
|---|---|---|---|
| **Authenticator app** (TOTP) | Google Authenticator, Microsoft Authenticator, Authy, 1Password, Bitwarden, … — works offline | none (RFC 6238, `totp.py`) | always available |
| **Text message** (SMS) | a mobile phone number | Twilio REST API (`messaging.py`, no SDK) | built-in **fake** outside prod; not offered in prod |
| **E-mail** | the account's address | any SMTP server (`smtplib`: own server, SendGrid, Mailgun, SES, Microsoft 365 …) | built-in **fake** outside prod; not offered in prod |

…plus ten one-time **recovery codes**, issued when the first method is turned on. Off by
default; the user turns methods on from the account screen. Administrators can reset the second
factor of a locked-out user.

## Why, in a zero-knowledge design

The server never sees the password or the data key — but the password alone unlocks *two*
things at sign-in: the session **and** the `wrapped_data_key` (`PLATFORM_DESIGN.md` §3.2).
Somebody who learns the password (phishing, reuse, a leak elsewhere) could fetch that wrapped
key and unwrap it offline. With a second factor a sign-in never gets that far without the
user's authenticator, phone or inbox: the password step hands out **nothing** — not a session,
not a device registration, not the wrapped key — until the code is right.

The authenticator app is the strongest choice (nothing travels over the phone network or
e-mail, works offline); text messages and e-mail are offered because not everybody has or wants
an authenticator app. The screen says so.

## Sign-in

```
                         ┌── 200 Session (token, wrapped_data_key …)            no second factor
POST /auth/login ───────┤
  email + auth_key       └── 202 { challenge, methods, default_method,          second factor on
                                   destinations, sent, send_error }
                                       │   default = authenticator app > text message > e-mail;
                                       │   if it is text/e-mail, the code is sent right away (sent)
POST /auth/login/2fa/send ◀────────────┤   optional: { challenge, method: sms|email } — switch / resend
POST /auth/login/2fa ◀─────────────────┘   { challenge, code }  — a code of ANY enabled method,
  → 200 Session — exactly what the password-only login returns        or a recovery code
```

* "Sign in with Google / Facebook" ([OAUTH.md](OAUTH.md)) counts as the first factor exactly
  like the password: `POST /auth/oauth/complete` answers the same `202` challenge.
* The **challenge** is a signed, stateless token (`auth.issue_challenge`, `typ: "2fa"`, five
  minutes) that proves *which* account just proved its password. It is never accepted as a
  session token (`auth.decode_token` refuses anything with a `typ`).
* The code step shows where the code went (masked: `+•••••••0100`, `a•••@example.com`) and
  buttons for the other ways in: *Use authenticator app*, *Text a code to …*, *E-mail a code to
  …*, *Send a new code*, *Use a recovery code*. If the text/e-mail could not be sent
  (`send_error`: provider down, send limit), the user can still pick another method.
* **Authenticator codes** are RFC 6238 TOTP with the defaults every app uses (SHA-1, 6 digits,
  30-second steps, 160-bit secret) — standard library only, pinned by the RFC test vectors.
  One step of clock drift either way is accepted; a code that signed somebody in is refused for
  the rest of its window (`accounts.totp_last_step`).
* **Sent codes** (text/e-mail) are six random digits, valid `TWO_FACTOR_CODE_TTL_SECONDS` (10
  min), one outstanding per account and purpose (a new one replaces the old), at most 5 wrong
  tries each, deleted when used. The server keeps an HMAC (keyed with `TOKEN_SECRET`) only —
  a plain hash of six digits would be reversed in a millisecond.
* **Recovery codes**: ten codes like `k7pmx-3wqen`, shown once, each works once, stored as salted
  SHA-256 hashes. Case, spaces and dashes do not matter when typing them.

### Limits

| What | Limit | Answer |
|---|---|---|
| Wrong codes (any method) and wrong password re-checks | `TWO_FACTOR_MAX_FAILURES` (5) per account per `TWO_FACTOR_FAILURE_WINDOW_SECONDS` (300) | `429` + `Retry-After` |
| Codes sent by text/e-mail | `TWO_FACTOR_MAX_SENDS` (10) per account per `TWO_FACTOR_SEND_WINDOW_SECONDS` (900) — texts cost money and must not become a spam cannon ("SMS pumping") | `429` |
| The same code again to the same place | once per `TWO_FACTOR_RESEND_SECONDS` (30) | `429` "A code was just sent …" |
| Wrong tries per sent code | 5, then the code is void | `401` |

All counters are keyed by the account (an attacker cannot dodge them by changing address; a
user is never locked out by somebody else's failures against another account). They live in
memory, per process: with *N* API replicas the effective limits are *N*×.

## Turning methods on (account screen → *Two-factor authentication*)

Each method has a row with **Set up** / **Remove**. Setting one up is two steps, so a typo in a
phone number or a mis-scanned QR code never locks anybody out:

| Step | API | What happens |
|---|---|---|
| 1. Password (+ number) | `POST /me/2fa/setup` `{auth_key, method: totp\|sms\|email, phone?}` | Re-proves the password (a leaked session token alone cannot lock the owner out). **totp:** a fresh secret, returned as an `otpauth://` URI — rendered as a **QR code on the device** (`qr.ts`) — and as a key to type or copy. **sms:** the number (international format; spaces, dashes, brackets, `00` prefix are fine) gets a code. **email:** the account address gets a code |
| 2. Code | `POST /me/2fa/enable` `{code, method}` | The right code proves the app / phone / inbox is really the user's; only now is the method on. The **first** method also returns the **recovery codes** (shown once, *Copy* / *Download*); further methods keep the existing ones |

The phone number is stored only from step 2 on, and only while text-message codes are on.

### Changing a method (new phone, another e-mail address, a new authenticator app)

Each enabled method has a **Change** button. The rules answer "is this really the owner?"
three times over:

| Who / what must be proven | How |
|---|---|
| The signed-in owner knows the password | `auth_key` re-check (the password is derived on the device, never sent) |
| … and holds a *current* second factor | a code from any enabled method (text / e-mail via *Text me a code* / *E-mail me a code*, the authenticator app) — or a recovery code when the phone is gone |
| … and owns the new destination | a code sent to the **new** number / address, or a code from the **new** authenticator app |

API: the same two calls as a set-up — `POST /me/2fa/setup {auth_key, method, code, phone?|email?}`
(`403` without the current code; `422` for the same number/address again) sends the
confirmation to the new destination / returns the new secret, and `POST /me/2fa/enable
{code, method}` swaps it over (`replacing: true` in the set-up answer). Until then the old
number / address / app keeps working. `POST /me/2fa/resend` sends the pending confirmation
again. After the swap the **previous destination is told** (the old number by text, the old
codes address and always the account e-mail) — the one signal an attacker who somehow passed
every check cannot suppress; audit entry `2fa.method_changed`. Recovery codes are unchanged.

The sign-in e-mail address itself cannot be changed here: it is also the salt of the
key derivation (`crypto.ts`), so e-mail codes can go to a *separate* address
(`accounts.otp_email`) while the account keeps its identity.

### Removing, turning off, new recovery codes

| Action | API | Needs |
|---|---|---|
| Remove one method | `POST /me/2fa/disable` `{auth_key, code, method}` | password **and** a current code of any method, or a recovery code. Removing the last method turns everything off |
| Turn off completely | `POST /me/2fa/disable` `{auth_key, code}` | the same; recovery codes are deleted too |
| New recovery codes | `POST /me/2fa/recovery-codes` `{auth_key, code}` | the same; the previous set stops working |
| A code for these | `POST /me/2fa/send` `{method: sms\|email}` | signed in; *Text me a code* / *E-mail me a code* buttons on those forms |
| Status | `GET /me/2fa` → `{enabled, methods, phone, email, recovery_codes_left, available, fake_channels, …}` | also embedded in `GET /me` as `two_factor` |

Both factors are required to weaken the account: a stolen session token is not enough, and
neither is the password alone. A user who lost the phone signs in with another method or a
recovery code, removes the old method and sets it up again.

Status codes: a wrong code at sign-in is `401` like a wrong password; on the signed-in
endpoints a wrong code or password is **`403`** — every client drops its session on a `401`,
and a typo must never sign anybody out.

### Lost everything (support)

Admin panel → account → **Reset 2FA** (`POST /admin/accounts/{id}/2fa/reset`): removes every
method (and the phone number), signs the account out everywhere, writes `2fa.reset` to the
audit log. The password alone signs in again and the user re-enrols. The panel shows the
methods and the **masked** phone number (`+•••••••0100`) so support can confirm "the number
ending in 0100" without seeing it. This is exactly what an attacker would ask support for —
**verify the person's identity first**; the button's tooltip and the confirmation say so.

## Providers and the built-in fake

`messaging.py` has one small strategy per channel. Each uses the real provider when it is
configured, otherwise the **built-in fake** stands in — like the Stripe/PayPal fakes — in
dev/test by default (`MESSAGING_FAKE_PROVIDERS`; qa/staging only when set explicitly), **never in
prod**. In prod a channel
without a provider is not offered (`available` lists only the authenticator app), and the
startup log says what each channel uses:
`Two-factor codes: e-mail=smtp · text message=twilio` (or `fake` / `off`).

**The fake** keeps the messages the app would have sent in `DATA_DIR/message_outbox.json` and
shows them three ways — nothing leaves the machine:

* **Test inbox page** `http://<api>/api/v1/messages/fake/outbox` — refreshes itself, laid out for
  phones too, so a tester signing in on a phone on the same network reads the code on that
  phone. The app links to it wherever a code was "sent" (*Built-in fake — nothing was really
  sent. Read it in the test inbox ↗*). JSON for tests: `…/outbox.json?to=…&channel=…`.
* **API log**: `[fake sms] SMS to +15555550100 - 123456 is your Personal Assistant code …`
* **`python dev.py outbox`** (`--clear`, or a number for how many) — reads the file directly, on
  Windows, macOS and Linux, with or without the API running.

### Configuration

| Variable | Default | Meaning |
|---|---|---|
| `MESSAGING_FAKE_PROVIDERS` | `true` in dev/test, `false` in qa/staging (opt in), always `false` in prod | Built-in fake for channels without a provider. Its test inbox shows codes to whoever opens it — keep it off on anything reachable from outside |
| `SMTP_HOST` / `SMTP_PORT` | — / `587` | Mail server; e-mail codes are on once `SMTP_HOST` is set |
| `SMTP_USERNAME` / `SMTP_PASSWORD` | — | Login (omit for an open relay on a private network) |
| `SMTP_SECURITY` | `starttls` | `starttls` (587) · `ssl` (465) · `none` (local relay only) |
| `EMAIL_FROM` | `Personal Assistant <no-reply@localhost>` | Sender; use an address your domain's SPF/DKIM cover |
| `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` | — | Twilio credentials; text codes are on once these and a sender are set |
| `TWILIO_FROM` *or* `TWILIO_MESSAGING_SERVICE_SID` | — | Sender number (E.164) or Messaging Service (preferred for multi-country sending) |
| `SMS_WEBOTP_HOST` | — | Host of the web app (e.g. `app.example.com`). Adds the line `@app.example.com #123456` to texts so Android/Chrome offers the code automatically — on that site only. Configured, never taken from the request (a phishing site could otherwise bind codes to itself) |
| `TWO_FACTOR_ISSUER` | `Personal Assistant` | Name in authenticator apps and in the messages |
| `TWO_FACTOR_CHALLENGE_TTL_SECONDS` | `300` | How long the code step may take |
| `TWO_FACTOR_CODE_TTL_SECONDS` | `600` | Validity of a texted/e-mailed code |
| `TWO_FACTOR_RESEND_SECONDS` | `30` | Minimum gap before the same code target gets another one |
| `TWO_FACTOR_MAX_SENDS` / `TWO_FACTOR_SEND_WINDOW_SECONDS` | `10` / `900` | Texts/e-mails per account per window |
| `TWO_FACTOR_MAX_FAILURES` / `TWO_FACTOR_FAILURE_WINDOW_SECONDS` | `5` / `300` | Wrong codes / password re-checks per account per window |

Another SMS provider (Vonage, MessageBird, AWS SNS …) is one more `Channel` subclass with a
`configured()` and a `_deliver()`; nothing else changes.

## On every device

The app is one PWA for phones, tablets, macOS, Linux and Windows; the two-factor screens use
standard web APIs only and adapt where the platform offers more:

| | iPhone / iPad | Android phone / tablet | Windows · macOS · Linux |
|---|---|---|---|
| Code fields | numeric keypad; **code from Messages offered above the keyboard** (`autocomplete="one-time-code"`) — also Safari on macOS | numeric keypad; **code read from the incoming text** (WebOTP, with `SMS_WEBOTP_HOST`) — the sign-in completes by itself | typed or pasted |
| Authenticator set-up | QR code (for a second device) + **Open in authenticator app** (`otpauth://` link) + *Copy key* | same | QR code to scan with the phone + *Copy key* |
| Layout | dialog as a bottom sheet, full-width thumb-sized buttons | same | centred dialog |
| Recovery codes file | *Download* (CRLF line endings: opens cleanly in Notepad and everywhere else) | | |

The server side is standard library (`smtplib`, `urllib`, `ssl`, `hmac`) and file writes are
atomic with `os.replace` — the same on Windows, macOS, Linux and in the containers.
`e2e/test_two_factor_devices.py` runs the set-up and sign-in flows with the profiles of an
iPhone, an Android phone, an iPad, an Android tablet and Windows/macOS/Linux desktops (screen,
touch, pixel ratio, user agent) and checks that nothing overflows the screen.

## Other clients

* **Ads admin console** (`apps/ads-admin`): code step after the password, with where the code
  went, *Text/E-mail a code* buttons and the test-inbox link.
* **API docs gate** (`/api/docs` in prod, `docs_gate.py`): same.

## Data

On `accounts` (Alembic `0007_two_factor`, `0008_two_factor_channels`): `totp_secret` (base32;
set = authenticator on), `totp_enabled_at`, `totp_pending_secret`, `totp_last_step`,
`email_otp_enabled_at`, `sms_otp_enabled_at`, `phone_number` (E.164, only while text codes are
on), `otp_email` (a separate address for the codes, `0010_otp_email`; null = the account
e-mail), `recovery_codes` (JSON list of salted hashes). Table `one_time_codes`: the outstanding
texted/e-mailed codes (keyed hash, purpose, channel, destination, attempts, expiry — minutes
long, expired rows pruned at startup). `tests/test_privacy.py` allow-lists the table and keeps
the phone number in that one column.

The phone number is the one piece of personal data beyond the e-mail address. It is opt-in,
used for nothing but these texts, removed with the method, and cleared by an account purge
together with everything else of the second factor. Text messages and e-mails pass through
the configured provider (a processor under GDPR — list it in the privacy policy).

Audit trail (`audit_log`): `2fa.enable` (with the method), `2fa.method_changed`, `2fa.method_removed`, `2fa.disable`,
`2fa.recovery_codes`, `2fa.recovery_used` (codes left), `2fa.reset` (by an administrator).

## Testing it locally

Seeded accounts (`python dev.py run` / `seed`), all with the password **`Twofa-pass-123`** and
the recovery codes `twofa-00001` … `twofa-00010` (each once; re-seeding restores them):

| Account | Second factor |
|---|---|
| `twofa@example.com` | authenticator app — add the key `OBSXE43PNZQWYLLBONZWS43UMFXHIMTG` to any app |
| `twofa-sms@example.com` | text message to `+15555550100` (fictional) → test inbox |
| `twofa-email@example.com` | e-mail → test inbox |

Every other fixture is reset to *no* second factor on each seed. From Python, the code an
authenticator shows right now: `totp.code_at("OBSXE43PNZQWYLLBONZWS43UMFXHIMTG")`.

| Where | What |
|---|---|
| `services/account/tests/test_two_factor.py` | RFC 6238/4226 vectors, drift window, replay guard, recovery-code hashing; authenticator enrolment; the two-step login (challenge is not a session, nothing handed out before the code, stale/foreign challenges); per-account rate limit; recovery codes; audit; purge; administrator reset; administrator sign-in incl. docs gate; OpenAPI |
| `services/account/tests/test_two_factor_channels.py` | phone normalisation/masking; SMS and e-mail enrolment and sign-in through the fake; switching channels, newest code wins, single use; authenticator preferred; removing one method / the last one; codes for sensitive changes; expiry and per-code attempts; send limits and resend cooldown; provider failure reported, not fatal; prod without providers offers only the app; **SMTP adapter against a stub `smtplib`**, **Twilio adapter against a stub transport** (URL, auth, form, messaging service, WebOTP line, refusal); the test inbox pages; support view with masked phone + reset; purge |
| `services/account/tests/test_two_factor_change.py` · `e2e/test_two_factor_change.py` | changing the phone number / codes address / authenticator app: refused without password + current code, proof of the new destination, the old one notified, sign-in codes go to the new place only, a recovery code may authorise it, resend of the pending confirmation; in a real browser from the account screen |
| `services/account/tests/test_seed.py` · `test_privacy.py` | the three 2FA fixtures sign in; the privacy allow-list and the single phone column |
| `apps/web/src/app/core/account/qr.spec.ts` · `platform.spec.ts` | QR code well-formed; WebOTP used where present, typing elsewhere; only server-built `otpauth://` links |
| `e2e/test_two_factor.py` | authenticator app end to end, ads admin console with an authenticator, support reset |
| `e2e/test_two_factor_devices.py` | set-up + sign-in on iPhone (SMS), Android phone (SMS), iPad (e-mail), Android tablet (e-mail), Windows (app), macOS (e-mail), Linux (SMS) profiles, no horizontal overflow; switching text → e-mail → recovery code on a phone, removing a method with an e-mailed code; the test inbox on a phone; an administrator with e-mail codes on the ads admin console (resend cooldown, wrong then right code) |
