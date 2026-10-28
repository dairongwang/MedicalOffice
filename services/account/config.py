"""Runtime configuration of the Account & Sync Service (environment variables, sane defaults).

Standard library only, identical on Windows/macOS/Linux/containers. A ``.env`` file next to this
module is loaded if present (KEY=VALUE lines; real environment variables take precedence).

What this service is — and is not:
  * It holds **account + billing rows** and a **transient, end-to-end-encrypted sync relay**.
  * It never holds plaintext user content. There are no task/event tables here by design
    (see tests/test_privacy.py, which fails the build if one is added).
"""

from __future__ import annotations

import os
import secrets
from decimal import Decimal
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SERVICE_DIR.parent.parent


def parse_dotenv_line(raw: str) -> tuple[str, str] | None:
    """``KEY=value`` → (KEY, value). Accepts ``export KEY=…``, quoted values (kept verbatim) and
    trailing ``# comments`` after unquoted values; returns None for blank/comment lines."""
    line = raw.strip().lstrip("\ufeff")  # BOM from some Windows editors
    if not line or line.startswith("#") or "=" not in line:
        return None
    key, value = line.split("=", 1)
    key = key.strip()
    if key.startswith("export "):
        key = key[7:].strip()
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    else:
        value = value.split(" #", 1)[0].split("\t#", 1)[0].strip()
    return (key, value) if key else None


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        parsed = parse_dotenv_line(raw)
        if parsed:
            os.environ.setdefault(parsed[0], parsed[1])


_load_dotenv(SERVICE_DIR / ".env")


def _bool(value: str | None, default: bool) -> bool:
    if value is None or not value.strip():
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


_ENV_ALIASES = {
    "production": "prod",
    "prd": "prod",
    "live": "prod",
    "development": "dev",
    "local": "dev",
    "testing": "test",
    "uat": "qa",
    "stage": "staging",
    "preprod": "staging",
}


def normalize_env(raw: str | None) -> str:
    """APP_ENV → one of dev | test | qa | staging | prod. Case-insensitive, tolerant of
    surrounding quotes/whitespace/inline comments and of common aliases (``Production`` →
    ``prod``), so that "prod" never silently turns into "not prod"."""
    value = (raw or "").split("#", 1)[0].strip().strip("\"'").strip().lower()
    return _ENV_ALIASES.get(value, value) or "dev"


SERVICE_VERSION = "1.0.0"
ENV = normalize_env(os.environ.get("APP_ENV"))  # dev | test | qa | staging | prod
IS_PROD = ENV == "prod"

# ---- Database -----------------------------------------------------------------------
# PostgreSQL in every real environment (Azure Database for PostgreSQL in the cloud):
#   DATABASE_URL=postgresql+psycopg://user:password@host:5432/assistant
# SQLite is accepted for quick local hacking and for the unit tests only.
DATA_DIR = Path(os.environ.get("DATA_DIR", SERVICE_DIR / "data")).resolve()
DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{(DATA_DIR / 'account.db').as_posix()}")
IS_POSTGRES = DATABASE_URL.startswith("postgresql")
# Run Alembic migrations at startup for PostgreSQL too (SQLite always does). Fine for a single
# replica; multi-replica deployments migrate in the pipeline instead (see Dockerfile CMD).
AUTO_MIGRATE = _bool(os.environ.get("AUTO_MIGRATE"), False)

# ---- Auth ---------------------------------------------------------------------------
# Secret used to sign access tokens. MUST be set in staging/prod (a random one is generated for
# dev so that restarting the dev server simply signs everybody out).
TOKEN_SECRET = os.environ.get("TOKEN_SECRET") or secrets.token_urlsafe(32)
TOKEN_TTL_SECONDS = int(os.environ.get("TOKEN_TTL_SECONDS", str(30 * 24 * 3600)))
if ENV in ("staging", "prod") and not os.environ.get("TOKEN_SECRET"):
    raise RuntimeError(
        f"APP_ENV={ENV} requires TOKEN_SECRET (it signs every session token). Set it in the environment or in "
        f'{SERVICE_DIR / ".env"}, e.g.:  python -c "import secrets; print(secrets.token_urlsafe(48))"  '
        "(python dev.py run/serve generate one for you)."
    )

# ---- Two-factor authentication (twofactor.py) ---------------------------------------
# Optional per account: a TOTP authenticator app as the second factor of a sign-in. The issuer
# is the name the authenticator shows next to the account. A sign-in that passed the password
# gets a short-lived challenge to answer with a code; after this many wrong codes within the
# window the account's second factor is locked for the rest of the window (6-digit codes must
# not be guessable by looping).
TWO_FACTOR_ISSUER = os.environ.get("TWO_FACTOR_ISSUER", "Personal Assistant").strip() or "Personal Assistant"
TWO_FACTOR_CHALLENGE_TTL_SECONDS = int(os.environ.get("TWO_FACTOR_CHALLENGE_TTL_SECONDS", "300"))
TWO_FACTOR_MAX_FAILURES = int(os.environ.get("TWO_FACTOR_MAX_FAILURES", "5"))
TWO_FACTOR_FAILURE_WINDOW_SECONDS = int(os.environ.get("TWO_FACTOR_FAILURE_WINDOW_SECONDS", "300"))
# Codes sent by e-mail / text message (the other two second factors besides the authenticator
# app): how long one stays valid, how soon another may be requested, and how many may be sent per
# account within the window (texts cost money and can be abused for "SMS pumping").
TWO_FACTOR_CODE_TTL_SECONDS = int(os.environ.get("TWO_FACTOR_CODE_TTL_SECONDS", "600"))
TWO_FACTOR_RESEND_SECONDS = int(os.environ.get("TWO_FACTOR_RESEND_SECONDS", "30"))
TWO_FACTOR_MAX_SENDS = int(os.environ.get("TWO_FACTOR_MAX_SENDS", "10"))
TWO_FACTOR_SEND_WINDOW_SECONDS = int(os.environ.get("TWO_FACTOR_SEND_WINDOW_SECONDS", "900"))

# ---- Outgoing messages: e-mail and SMS (messaging.py) ---------------------------------
# Used for two-factor codes. Each channel works once its provider is configured; without one
# the BUILT-IN FAKE answers (dev/test by default): messages land in a local outbox you can read at
# /api/v1/messages/fake/outbox (any browser, phone included), in the API log and with
# `python dev.py outbox` — nothing leaves the machine. In prod a channel without a provider is
# simply not offered.
# Default on in dev/test only: the test inbox shows codes to whoever opens it, so a qa/staging
# deployment reachable from outside has to opt in explicitly (MESSAGING_FAKE_PROVIDERS=true).
MESSAGING_FAKE_PROVIDERS = _bool(os.environ.get("MESSAGING_FAKE_PROVIDERS"), ENV in ("dev", "test")) and not IS_PROD
# E-mail via SMTP (any provider: your mail server, SendGrid, Mailgun, Amazon SES, Microsoft 365 …).
EMAIL_FROM = os.environ.get("EMAIL_FROM", "Personal Assistant <no-reply@localhost>").strip()
SMTP_HOST = os.environ.get("SMTP_HOST", "").strip()
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USERNAME = os.environ.get("SMTP_USERNAME", "").strip()
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SMTP_SECURITY = os.environ.get("SMTP_SECURITY", "starttls").strip().lower() or "starttls"  # starttls | ssl | none
SMTP_TIMEOUT_SECONDS = int(os.environ.get("SMTP_TIMEOUT_SECONDS", "20"))
# Text messages via Twilio's REST API (no SDK needed): account SID + auth token, and either a
# sender number (E.164) or a Messaging Service SID.
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "").strip()
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "").strip()
TWILIO_FROM = os.environ.get("TWILIO_FROM", "").strip()
TWILIO_MESSAGING_SERVICE_SID = os.environ.get("TWILIO_MESSAGING_SERVICE_SID", "").strip()
TWILIO_API_BASE = os.environ.get("TWILIO_API_BASE", "https://api.twilio.com").strip().rstrip("/")
# Host name of the web app (e.g. app.example.com). When set, texts end with "@host #123456" so
# Android/Chrome can offer the code for automatic entry (WebOTP) — and only on that site. Must be
# configured, never taken from the request (that would let a phishing site bind the code to itself).
SMS_WEBOTP_HOST = os.environ.get("SMS_WEBOTP_HOST", "").strip()

# ---- Sign in with Google / Facebook (oauth.py) ----------------------------------------
# OAuth 2.0 / OpenID Connect as an alternative to the password at sign-in. A provider is offered
# once its credentials are set; without them the BUILT-IN FAKE provider answers (dev/test by
# default, never in prod) so the whole flow can be exercised locally. The redirect URI to register
# with the provider is <API origin>/api/v1/auth/oauth/{google|facebook}/callback.
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
FACEBOOK_APP_ID = os.environ.get("FACEBOOK_APP_ID", "").strip()
FACEBOOK_APP_SECRET = os.environ.get("FACEBOOK_APP_SECRET", "").strip()
# Sign in with Apple: the Services ID (client id), the Team ID, the Key ID and the private key of
# the "Sign in with Apple" key (.p8 file) — as PEM text (with "\n" for line breaks) or a file path.
APPLE_CLIENT_ID = os.environ.get("APPLE_CLIENT_ID", "").strip()
APPLE_TEAM_ID = os.environ.get("APPLE_TEAM_ID", "").strip()
APPLE_KEY_ID = os.environ.get("APPLE_KEY_ID", "").strip()
APPLE_PRIVATE_KEY = os.environ.get("APPLE_PRIVATE_KEY", "").strip().replace("\\n", "\n")
if not APPLE_PRIVATE_KEY and os.environ.get("APPLE_PRIVATE_KEY_FILE", "").strip():
    try:
        APPLE_PRIVATE_KEY = Path(os.environ["APPLE_PRIVATE_KEY_FILE"].strip()).read_text(encoding="utf-8").strip()
    except OSError:
        APPLE_PRIVATE_KEY = ""
OAUTH_FAKE_PROVIDERS = _bool(os.environ.get("OAUTH_FAKE_PROVIDERS"), ENV in ("dev", "test")) and not IS_PROD
# The API's public origin (https://api.example.com) when it sits behind a proxy that rewrites
# Host — used to build the redirect URI. Empty = taken from the request.
PUBLIC_API_URL = os.environ.get("PUBLIC_API_URL", "").strip().rstrip("/")
# Where the browser returns after the provider (the web app). Empty = DEV_WEB_URL, else "/"
# (the app served by this process). Other origins are accepted when listed in CORS_ORIGINS.
WEB_APP_URL = os.environ.get("WEB_APP_URL", "").strip().rstrip("/")

# ---- Releases (what clients compare themselves against) -------------------------------
# Token the deploy pipeline uses to publish a release manifest after the PWA is on the CDN.
RELEASE_ADMIN_TOKEN = os.environ.get("RELEASE_ADMIN_TOKEN", "")
# Fallback manifest used until the first release is published (dev convenience).
RELEASE_FILE = Path(os.environ.get("RELEASE_FILE", PROJECT_DIR / "apps" / "web" / "release.json"))

# ---- Sync relay ---------------------------------------------------------------------
SYNC_OP_TTL_DAYS = int(os.environ.get("SYNC_OP_TTL_DAYS", "30"))  # hard TTL for relayed ciphertext
SYNC_INACTIVE_DEVICE_DAYS = int(os.environ.get("SYNC_INACTIVE_DEVICE_DAYS", "30"))
SYNC_MAX_OP_BYTES = int(os.environ.get("SYNC_MAX_OP_BYTES", str(64 * 1024)))
SYNC_MAX_SNAPSHOT_BYTES = int(os.environ.get("SYNC_MAX_SNAPSHOT_BYTES", str(16 * 1024 * 1024)))
SYNC_MAX_BATCH = int(os.environ.get("SYNC_MAX_BATCH", "500"))

# ---- Billing ------------------------------------------------------------------------
# Payment methods (payments.py, strategy pattern): stripe (Stripe Checkout) · paypal (PayPal
# Subscriptions/Orders). The account screen shows a button per method; a method works once its
# credentials below are set (Stripe test keys / PAYPAL_ENV=sandbox for development — nothing is
# charged). BILLING_METHODS narrows/orders the list (comma-separated, e.g. "stripe,paypal";
# empty = every method).
BILLING_METHODS = [m.strip().lower() for m in os.environ.get("BILLING_METHODS", "").split(",") if m.strip()]
# The method pre-selected when the client does not ask for one (must be configured, otherwise
# the first configured method is used).
BILLING_PROVIDER = os.environ.get("BILLING_PROVIDER", "stripe").strip().lower() or "stripe"
# Built-in fakes of Stripe and PayPal (fakes.py — served by this API, nothing leaves the machine)
# stand in for a provider whose credentials are missing: the hosted checkout/portal pages,
# the REST calls and the signed webhooks all work, so every billing state can be exercised
# locally. On outside prod, never in prod.
BILLING_FAKE_PROVIDERS = _bool(os.environ.get("BILLING_FAKE_PROVIDERS"), True) and not IS_PROD
TRIAL_DAYS = int(os.environ.get("TRIAL_DAYS", "14"))
GRACE_DAYS = int(os.environ.get("GRACE_DAYS", "7"))
# ---- Prices (what each paid membership costs) ----------------------------------------
# Decimal amounts in BILLING_CURRENCY; the checkout pages, the plan picker and the refunds use
# them. Stripe is charged exactly these amounts (ad-hoc price data at checkout); PayPal
# subscriptions bill the Plan ids below, which must be created at PayPal with the same amounts.
BILLING_CURRENCY = os.environ.get("BILLING_CURRENCY", "USD").strip().upper() or "USD"
PRICE_PRO_MONTHLY = Decimal(os.environ.get("PRICE_PRO_MONTHLY", "1.00"))
PRICE_PRO_YEARLY = Decimal(os.environ.get("PRICE_PRO_YEARLY", "10.00"))
PRICE_LIFETIME = Decimal(os.environ.get("PRICE_LIFETIME", "20.00"))
# Refunds on cancellation: subscriptions give back the unused share of the period (days left ÷
# days in the period × amount paid); Lifetime gives back this flat amount.
REFUND_LIFETIME = Decimal(os.environ.get("REFUND_LIFETIME", "10.00"))
# KPI alerts on the administrators' payment dashboard (payment_analytics.py): warn above these,
# "fail" above twice them. Rates are fractions (0.10 = 10 %).
PAYMENT_ALERT_FAILURE_RATE = float(os.environ.get("PAYMENT_ALERT_FAILURE_RATE", "0.10"))
PAYMENT_ALERT_REFUND_RATE = float(os.environ.get("PAYMENT_ALERT_REFUND_RATE", "0.25"))
PAYMENT_ALERT_REVENUE_DROP = float(os.environ.get("PAYMENT_ALERT_REVENUE_DROP", "0.30"))
PAYMENT_ALERT_PAST_DUE = int(os.environ.get("PAYMENT_ALERT_PAST_DUE", "5"))
PAYMENT_ALERT_MIN_TRANSACTIONS = int(os.environ.get("PAYMENT_ALERT_MIN_TRANSACTIONS", "5"))
# Stripe (https://dashboard.stripe.com → Developers): secret key and the webhook endpoint's
# signing secret (endpoint URL: <api>/api/v1/billing/webhooks/stripe).
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "").strip()
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "").strip()
STRIPE_API_BASE = os.environ.get("STRIPE_API_BASE", "https://api.stripe.com").strip().rstrip("/")
# PayPal (https://developer.paypal.com → Apps & Credentials): REST app client id + secret, the id
# of the webhook you registered (URL: <api>/api/v1/billing/webhooks/paypal) and the billing Plan
# ids for the subscriptions (Lifetime is a one-off order at PRICE_LIFETIME). PAYPAL_ENV=sandbox|live.
PAYPAL_CLIENT_ID = os.environ.get("PAYPAL_CLIENT_ID", "").strip()
PAYPAL_CLIENT_SECRET = os.environ.get("PAYPAL_CLIENT_SECRET", "").strip()
PAYPAL_WEBHOOK_ID = os.environ.get("PAYPAL_WEBHOOK_ID", "").strip()
PAYPAL_PLAN_PRO_MONTHLY = os.environ.get("PAYPAL_PLAN_PRO_MONTHLY", "").strip()
PAYPAL_PLAN_PRO_YEARLY = os.environ.get("PAYPAL_PLAN_PRO_YEARLY", "").strip()
PAYPAL_ENV = os.environ.get("PAYPAL_ENV", "live" if IS_PROD else "sandbox").strip().lower()
PAYPAL_API_BASE = os.environ.get("PAYPAL_API_BASE", "").strip().rstrip("/") or (
    "https://api-m.paypal.com" if PAYPAL_ENV == "live" else "https://api-m.sandbox.paypal.com"
)

# ---- Membership ---------------------------------------------------------------------
# Membership types (membership.py, strategy pattern): anonymous (no account) · free (account,
# no subscription) · paid (Pro/Lifetime) · admin (production support). Free memberships may
# keep at most this many *active* (not done) tasks; paid and admin are unlimited.
FREE_MAX_ACTIVE_TASKS = int(os.environ.get("FREE_MAX_ACTIVE_TASKS", "3"))
# Comma-separated e-mails that get the administrator role when they register or sign in
# (bootstrap for a fresh deployment; further admins are promoted from the admin panel).
ADMIN_EMAILS = {e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()}
# Create the fake test accounts (seed.py) at startup. Ignored in prod.
SEED_FAKE_ACCOUNTS = _bool(os.environ.get("SEED_FAKE_ACCOUNTS"), False) and ENV != "prod"

# ---- Sponsored content (ads) ---------------------------------------------------------
# First-party, contextual advertising for the free memberships (docs/ADVERTISING.md). No ad
# network, no tracking: the client asks for creatives with a *coarse region* it chose itself
# (country code derived on the device, user-visible and overridable) and reports anonymous
# impression/click counts. Paid/admin memberships have the "ad_free" feature and never ask.
ADS_ENABLED = _bool(os.environ.get("ADS_ENABLED"), True)
# How long one creative stays before the client rotates to the next one (sent to the client).
ADS_ROTATION_SECONDS = int(os.environ.get("ADS_ROTATION_SECONDS", "300"))
# Creatives per /ads/serve answer (the client rotates through them before asking again).
ADS_MAX_PER_RESPONSE = int(os.environ.get("ADS_MAX_PER_RESPONSE", "3"))
# Aggregate daily counters (ad × day × region, no identifiers) are deleted after this many days.
ADS_STATS_RETENTION_DAYS = int(os.environ.get("ADS_STATS_RETENTION_DAYS", "400"))
# Hosts creative images may be loaded from (comma-separated, e.g. "cdn.example.com"). Images
# must be first-party: a sponsor-hosted image would reveal the viewer's IP address to the
# sponsor at impression time. Empty (default) = text-only creatives, images are refused.
ADS_IMAGE_HOSTS = {h.strip().lower() for h in os.environ.get("ADS_IMAGE_HOSTS", "").split(",") if h.strip()}
# Analytics (ad-admin dashboard): per-region figures below this many impressions are folded into
# "other" (statistical disclosure control — a single view from a small country on one day could
# otherwise be linked to a person by someone who knows them). 1 = no suppression.
ADS_ANALYTICS_MIN_COUNT = int(os.environ.get("ADS_ANALYTICS_MIN_COUNT", "5"))
# Optional: serve the separate ad-admin dashboard (apps/ads-admin, static files) from this process
# at /ads-admin/ — single-process/self-hosted setups. Normally it is its own deployment
# (apps/ads-admin/Dockerfile) and talks to this API across origins (CORS_ORIGINS).
ADS_ADMIN_DIST = Path(os.environ["ADS_ADMIN_DIST"]).resolve() if os.environ.get("ADS_ADMIN_DIST") else None
# Where administrators find the ads admin console (the user app's admin dialog links to it).
# Default: /ads-admin/ when this process serves it, empty (no link) otherwise.
ADS_ADMIN_URL = os.environ.get("ADS_ADMIN_URL", "/ads-admin/" if ADS_ADMIN_DIST else "").strip()

# ---- Ad sources (docs/ADVERTISING.md §10) --------------------------------------------
# Which source fills the panel: internal | sponsored | affiliate | adsense | microsoft | network.
# This is the default; administrators can switch at runtime (admin panel → Sponsored content →
# Ad source; stored in app_settings and taking precedence over this value).
ADS_SOURCE = os.environ.get("ADS_SOURCE", "sponsored").strip().lower() or "sponsored"
# Built-in fakes for the third-party providers (served by this API, nothing leaves the machine)
# whenever a provider's credentials are missing. On outside prod, off in prod.
ADS_FAKE_PROVIDERS = _bool(os.environ.get("ADS_FAKE_PROVIDERS"), ENV != "prod")
# Affiliate links: query parameter appended to every affiliate creative's URL (e.g. "tag=pa-21").
ADS_AFFILIATE_TAG = os.environ.get("ADS_AFFILIATE_TAG", "").strip()
# Google AdSense: publisher id (ca-pub-…) and ad unit/slot id.
ADS_ADSENSE_CLIENT = os.environ.get("ADS_ADSENSE_CLIENT", "").strip()
ADS_ADSENSE_SLOT = os.environ.get("ADS_ADSENSE_SLOT", "").strip()
ADS_ADSENSE_SCRIPT_URL = os.environ.get(
    "ADS_ADSENSE_SCRIPT_URL", "https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js"
).strip()
# Microsoft Advertising (pubCenter): site id, publisher id, ad unit id (from the pubCenter tag).
ADS_MICROSOFT_SITE_ID = os.environ.get("ADS_MICROSOFT_SITE_ID", "").strip()
ADS_MICROSOFT_PUBLISHER_ID = os.environ.get("ADS_MICROSOFT_PUBLISHER_ID", "").strip()
ADS_MICROSOFT_AD_UNIT_ID = os.environ.get("ADS_MICROSOFT_AD_UNIT_ID", "").strip()
ADS_MICROSOFT_SCRIPT_URL = os.environ.get(
    "ADS_MICROSOFT_SCRIPT_URL", "https://adsdk.microsoft.com/pubcenter/sdk.js"
).strip()
# Third-party network: a display name, and EITHER a JSON endpoint this server calls on behalf of
# the client (server-to-server — the visitor's IP never reaches the network; "{region}" and
# "{lang}" are substituted) OR a script tag URL embedded in the page (consent-gated, like AdSense).
ADS_NETWORK_NAME = os.environ.get("ADS_NETWORK_NAME", "").strip()
ADS_NETWORK_JSON_URL = os.environ.get("ADS_NETWORK_JSON_URL", "").strip()
ADS_NETWORK_SCRIPT_URL = os.environ.get("ADS_NETWORK_SCRIPT_URL", "").strip()
ADS_NETWORK_UNIT_ID = os.environ.get("ADS_NETWORK_UNIT_ID", "").strip()
ADS_NETWORK_PRIVACY_URL = os.environ.get("ADS_NETWORK_PRIVACY_URL", "").strip()
# Optional: name of a request header an edge proxy/CDN fills with the visitor's country
# (Cloudflare: "CF-IPCountry"). Used only when the client sends *no* region at all (never when
# the user chose "no region"), never stored. Empty (default) = never look at the IP-derived country.
ADS_REGION_HEADER = os.environ.get("ADS_REGION_HEADER", "").strip()

# ---- API docs ------------------------------------------------------------------------
# /api/docs, /api/redoc and /api/openapi.json need an administrator account whenever
# APP_ENV=prod — always, no variable can switch that off (docs_gate.py). In every other
# environment they are open; DOCS_REQUIRE_ADMIN=true enables the gate there too (e.g. to try it).
DOCS_REQUIRE_ADMIN = IS_PROD or _bool(os.environ.get("DOCS_REQUIRE_ADMIN"), False)

# ---- Web ---------------------------------------------------------------------------
# Comma-separated origins allowed to call the API (the PWA's origin when hosted separately,
# e.g. https://<name>.azurestaticapps.net). Empty = same-origin only.
CORS_ORIGINS = [o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()]
# If set to the Angular build output (apps/web/dist/web/browser), the API also serves the PWA —
# one process for LAN testing and small self-hosted setups. In Azure the CDN serves it instead.
WEB_DIST = Path(os.environ["WEB_DIST"]).resolve() if os.environ.get("WEB_DIST") else None
# Where the app lives while developing (`python dev.py run` sets this to the Angular dev server);
# requests for "/" on the API are redirected there so an old bookmark/tab still ends up in the app.
DEV_WEB_URL = os.environ.get("DEV_WEB_URL", "").strip()
