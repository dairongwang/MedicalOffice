"""Account & Sync Service — the only server component of the Personal Assistant platform.

    /api/v1/auth/*      register · login (+ login/2fa, login/2fa/send: second factor) · logout · sign out everywhere
                        oauth/providers · oauth/{google|facebook}/start → callback · oauth/complete  (sign in with …)
    /api/v1/me          account, devices, entitlements, settings, two-factor (app · SMS · e-mail), delete
    /api/v1/messages/fake/outbox   test inbox of the built-in e-mail/SMS fake (dev/test only)
    /api/v1/membership  the membership types (anonymous · free · paid · admin) and their limits
    /api/v1/billing/*   methods · checkout · cancel (+preview) · payments · manage · webhooks/{stripe,paypal} · trial
    /api/v1/admin/*     stats · accounts · membership/role changes · audit   (administrators)
    /api/v1/sync/*      push · pull · ack · status · snapshot   (ciphertext relay)
    /api/v1/release/*   manifest · check · publish · history   (client update policy)
    /api/v1/ads/*       serve · metrics   (first-party sponsored content for free memberships, anonymous)
    /api/v1/health

It stores account + billing rows and relays end-to-end-encrypted sync operations. It holds no
user content — the product logic and data live in the PWA (apps/web). Optionally serves the
built PWA (WEB_DIST) so one process can host everything on a LAN.
"""

from __future__ import annotations

import json
import logging
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.orm import Session

import accounts
import ad_sources
import admin
import ads
import auth
import billing
import config
import docs_gate
import fakes
import membership
import messaging
import models
import oauth
import payments
import releases
import schemas
import sync
import twofactor
from database import SessionLocal, get_db, init_db

log = logging.getLogger("account")

# Windows consoles and pipes may use a legacy code page (cp1252): a print with a character the
# page lacks would raise UnicodeEncodeError and abort start-up. Degrade such characters to "?"
# instead — nothing we print is worth crashing for.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure") and (getattr(_stream, "encoding", "") or "").lower() not in ("utf-8", "utf8"):
        try:
            _stream.reconfigure(errors="replace")
        except (ValueError, AttributeError):  # pragma: no cover - exotic stream wrappers
            pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # What this process decided from its environment — the first thing to check when "prod"
    # behaviour (docs gate, no fake accounts) seems to be missing.
    print(
        f"Personal Assistant API v{config.SERVICE_VERSION} · APP_ENV={config.ENV} · "
        f"database={'postgresql' if config.IS_POSTGRES else 'sqlite'} · "
        f"api docs={'administrators only' if config.DOCS_REQUIRE_ADMIN else 'open'}",
        flush=True,
    )
    if config.SEED_FAKE_ACCOUNTS:  # dev/test convenience: the fake accounts are there after `dev.py run`
        import seed

        with SessionLocal() as db:
            seed.seed(db, log=lambda fmt, *args: print(fmt % args, flush=True))
            seed.seed_ads(db, log=lambda fmt, *args: print(fmt % args, flush=True))
    with SessionLocal() as db:  # aggregate ad counters older than the retention window
        ads.prune_stats(db)
        twofactor.prune_codes(db)  # expired two-factor codes sent by e-mail / text
    print(
        "Two-factor codes: e-mail="
        + (
            "fake (test inbox: /api/v1/messages/fake/outbox)"
            if messaging.EMAIL_CHANNEL.fake
            else "smtp"
            if messaging.EMAIL_CHANNEL.configured()
            else "off"
        )
        + " · text message="
        + ("fake" if messaging.SMS_CHANNEL.fake else "twilio" if messaging.SMS_CHANNEL.configured() else "off"),
        flush=True,
    )
    yield


app = FastAPI(
    title="Personal Assistant — Account & Sync Service",
    version=config.SERVICE_VERSION,
    description=__doc__,
    lifespan=lifespan,
    # Docs live under /api/* and are served by docs_gate (open in dev/test/qa/staging, admin-only in prod).
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

if config.CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.CORS_ORIGINS,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Min-Supported-Version"],
    )

api = APIRouter(prefix="/api/v1")


def client_headers(
    x_client_version: str | None = Header(default=None),
    x_schema_version: int | None = Header(default=None),
) -> tuple[str | None, int | None]:
    return x_client_version, x_schema_version


def _device_out(d: models.Device, current_id: str) -> schemas.DeviceOut:
    return schemas.DeviceOut(
        id=d.id,
        platform=d.platform,
        client_version=d.client_version,
        schema_version=d.schema_version,
        cursor=d.cursor,
        name_ciphertext=d.name_ciphertext,
        last_seen_at=d.last_seen_at,
        is_current=d.id == current_id,
    )


# ==================== Auth ====================


def _register_device(db: Session, account: models.Account, info: schemas.DeviceInfo) -> models.Device:
    device = models.Device(
        account_id=account.id,
        platform=info.platform,
        client_version=info.client_version,
        schema_version=info.schema_version,
        name_ciphertext=info.name_ciphertext,
    )
    db.add(device)
    db.flush()
    return device


def _session(account: models.Account, device: models.Device) -> schemas.Session:
    return schemas.Session(
        token=auth.issue_token(account.id, device.id, device.token_generation),
        account_id=account.id,
        device_id=device.id,
        email=account.email,
        wrapped_data_key=account.wrapped_data_key,
        kdf_params=json.loads(account.kdf_params or "{}"),
        vault_enabled=account.vault_enabled,
        has_password=account.has_password,
    )


@api.post("/auth/register", response_model=schemas.Session, status_code=201, tags=["auth"])
def register(body: schemas.RegisterRequest, db: Session = Depends(get_db)):
    email = body.email.lower()
    if db.execute(select(models.Account).where(models.Account.email == email)).scalar_one_or_none():
        raise HTTPException(status_code=409, detail="An account with this e-mail already exists. Sign in instead.")
    salt = auth.new_salt()
    account = models.Account(
        email=email,
        auth_salt=salt,
        auth_hash=auth.hash_auth_key(body.auth_key, salt),
        wrapped_data_key=body.wrapped_data_key,
        kdf_params=json.dumps(body.kdf_params),
    )
    auth.apply_bootstrap_role(account)
    db.add(account)
    db.flush()
    device = _register_device(db, account, body.device)
    billing.subscription_for(db, account)
    db.commit()
    return _session(account, device)


@api.post(
    "/auth/login",
    response_model=schemas.Session,
    responses={202: {"model": schemas.TwoFactorChallenge, "description": "Password accepted; second factor needed"}},
    tags=["auth"],
)
def login(body: schemas.LoginRequest, db: Session = Depends(get_db)):
    """Password step. Accounts with two-factor authentication get ``202`` + a challenge instead
    of a session — finish with ``POST /auth/login/2fa``. Nothing (not even the wrapped data
    key) is handed out before the second factor."""
    account = db.execute(select(models.Account).where(models.Account.email == body.email.lower())).scalar_one_or_none()
    if (
        not account
        or account.deleted_at
        or not auth.verify_auth_key(body.auth_key, account.auth_salt, account.auth_hash)
    ):
        raise HTTPException(status_code=401, detail="E-mail or password is incorrect.")
    auth.apply_bootstrap_role(account)
    if account.two_factor_enabled:
        challenge = twofactor.challenge_for(db, account)  # sends the code when the preferred method is sms/e-mail
        db.commit()  # the bootstrap role, if any
        return JSONResponse(status_code=202, content=challenge.model_dump(mode="json"))
    device = _register_device(db, account, body.device)
    db.commit()
    return _session(account, device)


@api.post("/auth/login/2fa/send", response_model=schemas.CodeDelivery, tags=["auth"])
def login_two_factor_send(body: schemas.TwoFactorSendLogin, db: Session = Depends(get_db)):
    """Sign-in, between the steps: send (or resend) a code by text message or e-mail — one of
    the methods the challenge lists. Limited per account (429 with Retry-After)."""
    out = twofactor.send_login_code(db, body.challenge, body.method)
    db.commit()
    return out


@api.post("/auth/login/2fa", response_model=schemas.Session, tags=["auth"])
def login_two_factor(body: schemas.TwoFactorLoginRequest, db: Session = Depends(get_db)):
    """Second step: the challenge from ``/auth/login`` plus a 6-digit code (authenticator app,
    text message or e-mail) or a recovery code → the session. Wrong codes are counted per
    account (429 after too many)."""
    account, _method = twofactor.complete_login(db, body.challenge, body.code)
    device = _register_device(db, account, body.device)
    db.commit()
    return _session(account, device)


# ---- Sign in with Google / Facebook (oauth.py) ----


@api.get("/auth/oauth/providers", response_model=schemas.OAuthProviders, tags=["auth"])
def oauth_providers():
    """Which providers this server offers (configured, or the built-in fake standing in)."""
    return oauth.catalog()


@api.get("/auth/oauth/{name}/start", include_in_schema=True, tags=["auth"], response_class=RedirectResponse)
def oauth_start(name: str, request: Request, return_to: str | None = Query(default=None, max_length=2048)):
    """Send the browser here (a navigation, not a fetch): it continues at the provider and comes
    back to ``return_to#oauth=<ticket>`` — or ``#oauth_error=<message>``."""
    return RedirectResponse(oauth.start_url(request, name, return_to), status_code=302)


@api.get("/auth/oauth/{name}/callback", include_in_schema=False)
def oauth_callback(name: str, request: Request, db: Session = Depends(get_db)):
    """The provider's redirect target (register it there as <API origin>/api/v1/auth/oauth/{name}/callback)."""
    return RedirectResponse(
        oauth.handle_callback(db, request, name, dict(request.query_params)),
        status_code=302,
        headers={"Cache-Control": "no-store"},
    )


@api.post("/auth/oauth/{name}/callback", include_in_schema=False)
async def oauth_callback_post(name: str, request: Request, db: Session = Depends(get_db)):
    """Apple posts the result (``response_mode=form_post``); the browser then follows the redirect."""
    from urllib.parse import parse_qs

    form = {k: v[0] for k, v in parse_qs((await request.body()).decode(), keep_blank_values=True).items()}
    return RedirectResponse(
        oauth.handle_callback(db, request, name, form), status_code=303, headers={"Cache-Control": "no-store"}
    )


@api.post(
    "/auth/oauth/complete",
    response_model=schemas.Session,
    responses={202: {"model": schemas.TwoFactorChallenge, "description": "Provider accepted; second factor needed"}},
    tags=["auth"],
)
def oauth_complete(body: schemas.OAuthCompleteRequest, db: Session = Depends(get_db)):
    """Exchange the ticket for a session. Like the password step: an account with two-factor
    authentication gets ``202`` + a challenge instead (the provider was the first factor)."""
    account = oauth.complete(db, body.ticket)
    if account.two_factor_enabled:
        challenge = twofactor.challenge_for(db, account)
        db.commit()
        return JSONResponse(status_code=202, content=challenge.model_dump(mode="json"))
    device = _register_device(db, account, body.device)
    db.commit()
    return _session(account, device)


@api.post("/auth/logout", status_code=204, tags=["auth"])
def logout(p: auth.Principal = Depends(auth.current_principal), db: Session = Depends(get_db)):
    p.device.revoked_at = models.utcnow()
    db.commit()
    return Response(status_code=204)


@api.post("/auth/logout-everywhere", status_code=204, tags=["auth"])
def logout_everywhere(p: auth.Principal = Depends(auth.current_principal), db: Session = Depends(get_db)):
    accounts.revoke_all_devices(db, p.account)
    db.commit()
    return Response(status_code=204)


# ==================== Me ====================


@api.get("/me", response_model=schemas.Me, tags=["me"])
def me(p: auth.Principal = Depends(auth.current_principal), db: Session = Depends(get_db)):
    devices = db.execute(
        select(models.Device).where(models.Device.account_id == p.account.id, models.Device.revoked_at.is_(None))
    ).scalars()
    return schemas.Me(
        account_id=p.account.id,
        email=p.account.email,
        role=p.account.role,
        created_at=p.account.created_at,
        vault_enabled=p.account.vault_enabled,
        devices=[_device_out(d, p.device.id) for d in devices],
        entitlements=billing.entitlements(db, p.account),
        two_factor=twofactor.status(p.account),
        has_password=p.account.has_password,
        identities=oauth.identities(db, p.account),
    )


@api.get("/me/entitlements", response_model=schemas.Entitlements, tags=["me"])
def my_entitlements(p: auth.Principal = Depends(auth.current_principal), db: Session = Depends(get_db)):
    return billing.entitlements(db, p.account)


@api.patch("/me/settings", response_model=schemas.Me, tags=["me"])
def update_settings(
    body: schemas.AccountSettings, p: auth.Principal = Depends(auth.current_principal), db: Session = Depends(get_db)
):
    if body.vault_enabled is not None:
        p.account.vault_enabled = body.vault_enabled
        if not body.vault_enabled:
            sync.delete_snapshot(db, p.account)
    db.commit()
    return me(p, db)


@api.delete("/me/devices/{device_id}", status_code=204, tags=["me"])
def revoke_device(device_id: str, p: auth.Principal = Depends(auth.current_principal), db: Session = Depends(get_db)):
    device = db.get(models.Device, device_id)
    if not device or device.account_id != p.account.id:
        raise HTTPException(status_code=404, detail="Device not found.")
    device.revoked_at = models.utcnow()
    db.commit()
    return Response(status_code=204)


# ---- Sign-in methods: passphrase, linked providers (oauth.py) ----


@api.post("/me/passphrase", response_model=schemas.Me, tags=["me"])
def set_passphrase(
    body: schemas.SetPassphraseRequest,
    p: auth.Principal = Depends(auth.current_principal),
    db: Session = Depends(get_db),
):
    """An account created by "Sign in with Google/Facebook" chooses its encryption passphrase
    (once): it wraps the data key on the device and doubles as the password for e-mail sign-in."""
    oauth.set_passphrase(db, p.account, body)
    db.commit()
    return me(p, db)


@api.post("/me/oauth/{name}/link", response_model=schemas.OAuthLinkResponse, tags=["me"])
def oauth_link(
    name: str, body: schemas.OAuthLinkRequest, request: Request, p: auth.Principal = Depends(auth.current_principal)
):
    """Add Google / Facebook as a way to sign in to this account: send the browser to ``url``;
    it returns to ``return_to#oauth_linked=<provider>``."""
    return schemas.OAuthLinkResponse(url=oauth.start_url(request, name, body.return_to, link_account=p.account))


@api.delete("/me/oauth/{name}", response_model=schemas.Me, tags=["me"])
def oauth_unlink(name: str, p: auth.Principal = Depends(auth.current_principal), db: Session = Depends(get_db)):
    """Remove a linked provider (refused when it is the only way to sign in)."""
    oauth.unlink(db, p.account, name)
    db.commit()
    return me(p, db)


# ---- Two-factor authentication (twofactor.py) ----


@api.get("/me/2fa", response_model=schemas.TwoFactorStatus, tags=["me"])
def two_factor_status(p: auth.Principal = Depends(auth.current_principal)):
    """Is the second factor on, since when, how many recovery codes are left."""
    return twofactor.status(p.account)


@api.post("/me/2fa/setup", response_model=schemas.TwoFactorSetup, tags=["me"])
def two_factor_setup(
    body: schemas.TwoFactorSetupRequest,
    p: auth.Principal = Depends(auth.current_principal),
    db: Session = Depends(get_db),
):
    """Step 1 of turning a method on (password re-check). ``totp``: a new secret to scan into an
    authenticator app. ``sms`` (with ``phone``) / ``email`` (optionally with another ``email``
    address): a code is sent there. Not enforced until ``/me/2fa/enable`` confirms the code.
    A method that is already on can be **changed** the same way — with a current ``code`` from
    any enabled method (or a recovery code) on top of the password."""
    out = twofactor.begin_setup(db, p.account, body.auth_key, body.method, body.phone, body.email, body.code)
    db.commit()
    return out


@api.post("/me/2fa/enable", response_model=schemas.TwoFactorRecoveryCodes, tags=["me"])
def two_factor_enable(
    body: schemas.TwoFactorCode, p: auth.Principal = Depends(auth.current_principal), db: Session = Depends(get_db)
):
    """Step 2: the right code activates the method. With the first method the ten one-time
    recovery codes come back — shown once, the server keeps hashes only."""
    out = twofactor.enable(db, p.account, body.code, method=body.method)
    db.commit()
    return out


@api.post("/me/2fa/resend", response_model=schemas.CodeDelivery, tags=["me"])
def two_factor_resend(p: auth.Principal = Depends(auth.current_principal), db: Session = Depends(get_db)):
    """Send the pending set-up / change confirmation code again (same new number or address)."""
    out = twofactor.resend_setup_code(db, p.account)
    db.commit()
    return out


@api.post("/me/2fa/send", response_model=schemas.CodeDelivery, tags=["me"])
def two_factor_send(
    body: schemas.TwoFactorSend, p: auth.Principal = Depends(auth.current_principal), db: Session = Depends(get_db)
):
    """A code by text message or e-mail to confirm a sensitive change (remove a method, turn
    off, new recovery codes) — for accounts without an authenticator app at hand."""
    out = twofactor.send_verify_code(db, p.account, body.method)
    db.commit()
    return out


@api.post("/me/2fa/disable", response_model=schemas.TwoFactorStatus, tags=["me"])
def two_factor_disable(
    body: schemas.TwoFactorDisable,
    p: auth.Principal = Depends(auth.current_principal),
    db: Session = Depends(get_db),
):
    """Remove one method (``method``) or turn everything off (no ``method``): password plus a
    current code from any method (or a recovery code, for a lost phone/authenticator)."""
    out = twofactor.disable(db, p.account, body.auth_key, body.code, body.method)
    db.commit()
    return out


@api.post("/me/2fa/recovery-codes", response_model=schemas.TwoFactorRecoveryCodes, tags=["me"])
def two_factor_recovery_codes(
    body: schemas.TwoFactorPasswordAndCode,
    p: auth.Principal = Depends(auth.current_principal),
    db: Session = Depends(get_db),
):
    """A fresh set of recovery codes (password + code); the previous ones stop working."""
    out = twofactor.regenerate_recovery_codes(db, p.account, body.auth_key, body.code)
    db.commit()
    return out


@api.delete("/me", status_code=204, tags=["me"])
def delete_account(p: auth.Principal = Depends(auth.current_principal), db: Session = Depends(get_db)):
    """Purge: relay ops, snapshot and devices go now; the account row becomes a billing tombstone."""
    accounts.audit(db, p.account, "account.purge_self", p.account)
    accounts.purge(db, p.account)
    db.commit()
    return Response(status_code=204)


# ==================== Membership ====================


@api.get("/membership", response_model=schemas.MembershipCatalog, tags=["membership"])
def membership_catalog():
    """The membership types and what each allows (public — the pricing/account screens)."""
    return membership.catalog()


@api.get("/membership/me", response_model=schemas.MembershipInfo, tags=["membership"])
def my_membership(p: auth.Principal = Depends(auth.current_principal), db: Session = Depends(get_db)):
    """The membership strategy this account resolves to right now."""
    return membership.resolve(db, p.account).describe()


# ==================== Billing ====================


@api.get("/billing/methods", response_model=schemas.PaymentMethods, tags=["billing"])
def payment_methods():
    """The payment methods a buyer can choose from (payments.py strategies that are configured)."""
    return payments.catalog()


@api.post("/billing/checkout", response_model=schemas.CheckoutResponse, tags=["billing"])
def checkout(
    body: schemas.CheckoutRequest, p: auth.Principal = Depends(auth.current_principal), db: Session = Depends(get_db)
):
    """Hosted checkout with the chosen payment strategy. A running trial is carried over:
    paid billing starts when it ends."""
    method = payments.get(body.method)
    trial_until = billing.trial_carry_over(billing.subscription_for(db, p.account))
    db.commit()  # subscription_for may have created the row
    return schemas.CheckoutResponse(
        checkout_url=method.create_checkout(p.account, body.plan, body.return_url, trial_until), provider=method.name
    )


@api.post("/billing/trial", response_model=schemas.Entitlements, tags=["billing"])
def trial(p: auth.Principal = Depends(auth.current_principal), db: Session = Depends(get_db)):
    billing.start_trial(db, p.account)
    return billing.entitlements(db, p.account)


@api.get("/billing/cancel/preview", response_model=schemas.RefundPreview, tags=["billing"])
def cancel_preview(p: auth.Principal = Depends(auth.current_principal), db: Session = Depends(get_db)):
    """What cancelling right now would refund (the account screen shows it before confirming)."""
    return billing.refund_preview(db, p.account)[0]


@api.post("/billing/cancel", response_model=schemas.CancelResult, tags=["billing"])
def cancel_subscription(p: auth.Principal = Depends(auth.current_principal), db: Session = Depends(get_db)):
    """End the paid membership now and refund: subscriptions get the unused share of the
    period back, Lifetime the flat REFUND_LIFETIME."""
    return billing.cancel(db, p.account)


@api.get("/billing/payments", response_model=list[schemas.PaymentOut], tags=["billing"])
def payment_history(p: auth.Principal = Depends(auth.current_principal), db: Session = Depends(get_db)):
    """This account's charges, failures and refunds (newest first)."""
    return [billing.payment_out(row) for row in billing.list_payments(db, p.account)]


@api.post("/billing/manage", response_model=schemas.ManageResponse, tags=["billing"])
def manage_subscription(
    body: schemas.ManageRequest, p: auth.Principal = Depends(auth.current_principal), db: Session = Depends(get_db)
):
    """The provider's self-service page (payment method, invoices) for this subscription."""
    url, provider = billing.manage_url(db, p.account, body.return_url)
    return schemas.ManageResponse(url=url, provider=provider)


@api.post("/billing/webhooks/{provider_name}", tags=["billing"])
async def webhook(provider_name: str, request: Request, db: Session = Depends(get_db)):
    """Provider → us. The strategy named in the path authenticates the delivery and translates
    it; billing.apply_webhook updates the subscription row (idempotent per event id)."""
    method = next((m for m in payments.enabled() if m.name == provider_name), None)
    if method is None or not method.is_configured():
        raise HTTPException(status_code=404, detail="Unknown billing provider.")
    return billing.receive_webhook(db, method, await request.body(), request.headers)


# ==================== Sync (ciphertext relay) ====================


def sync_principal(
    p: auth.Principal = Depends(auth.current_principal),
    headers: tuple = Depends(client_headers),
    db: Session = Depends(get_db),
) -> auth.Principal:
    releases.enforce_supported(db, *headers)
    billing.require_feature(db, p.account, "sync")
    version, schema_version = headers
    if version:
        p.device.client_version = version
    if schema_version:
        p.device.schema_version = schema_version
    return p


@api.get("/sync/status", response_model=schemas.SyncStatus, tags=["sync"])
def sync_status(p: auth.Principal = Depends(sync_principal), db: Session = Depends(get_db)):
    return sync.status(db, p.account, p.device)


@api.post("/sync/push", response_model=schemas.PushResponse, tags=["sync"])
def sync_push(body: schemas.PushRequest, p: auth.Principal = Depends(sync_principal), db: Session = Depends(get_db)):
    return sync.push(db, p.account, p.device, body)


@api.get("/sync/pull", response_model=schemas.PullResponse, tags=["sync"])
def sync_pull(
    since: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=1000),
    p: auth.Principal = Depends(sync_principal),
    db: Session = Depends(get_db),
):
    return sync.pull(db, p.account, p.device, since, limit)


@api.post("/sync/ack", response_model=schemas.SyncStatus, tags=["sync"])
def sync_ack(body: schemas.AckRequest, p: auth.Principal = Depends(sync_principal), db: Session = Depends(get_db)):
    return sync.ack(db, p.account, p.device, body.cursor)


@api.get("/sync/snapshot", response_model=schemas.SnapshotOut, tags=["sync"])
def snapshot_get(p: auth.Principal = Depends(sync_principal), db: Session = Depends(get_db)):
    return sync.get_snapshot(db, p.account)


@api.put("/sync/snapshot", response_model=schemas.SnapshotOut, tags=["sync"])
def snapshot_put(body: schemas.SnapshotIn, p: auth.Principal = Depends(sync_principal), db: Session = Depends(get_db)):
    billing.require_feature(db, p.account, "vault")
    return sync.put_snapshot(db, p.account, p.device, body)


# ==================== Releases ====================


@api.get("/release/manifest", response_model=schemas.ReleaseManifest | None, tags=["release"])
def release_manifest(channel: str = "stable", db: Session = Depends(get_db)):
    return releases.latest(db, channel)


@api.get("/release/check", response_model=schemas.ClientCheck, tags=["release"])
def release_check(
    version: str = Query(default=""),
    schema: int = Query(default=0, alias="schema", ge=0),
    channel: str = "stable",
    db: Session = Depends(get_db),
):
    """Is this client version still fine? (update_available / update_required)."""
    return releases.check_client(releases.latest(db, channel), version, schema)


@api.post("/release/publish", response_model=schemas.ReleaseManifest, status_code=201, tags=["release"])
def release_publish(
    body: schemas.ReleaseManifest,
    db: Session = Depends(get_db),
    admin_user: auth.Principal | None = Depends(auth.require_admin),
):
    """Called by the deploy pipeline once the new PWA build is live on the CDN (X-Admin-Token),
    or by an administrator account (e.g. to mark a release critical during an incident)."""
    log.info("Publishing release %s (critical=%s)", body.version, body.critical)
    accounts.audit(
        db, admin_user.account if admin_user else None, "release.publish", version=body.version, critical=body.critical
    )
    return releases.publish(db, body)


@api.get("/release/history", response_model=list[schemas.ReleaseManifest], tags=["release"])
def release_history(channel: str = "stable", limit: int = Query(default=20, le=100), db: Session = Depends(get_db)):
    rows = db.execute(
        select(models.Release)
        .where(models.Release.channel == channel)
        .order_by(models.Release.published_at.desc())
        .limit(limit)
    ).scalars()
    return [
        schemas.ReleaseManifest(
            channel=r.channel,
            version=r.version,
            schema_version=r.schema_version,
            min_supported_version=r.min_supported_version,
            min_supported_schema=r.min_supported_schema,
            critical=r.critical,
            notes=r.notes,
            published_at=r.published_at,
        )
        for r in rows
    ]


# ==================== Sponsored content (anonymous, first-party) ====================
# No authentication on purpose: the client never sends its token here, so a creative request
# cannot be tied to an account. Paid memberships ("ad_free") do not call these at all.


@api.get("/ads/serve", response_model=schemas.AdServeResponse, tags=["ads"])
def ads_serve(
    request: Request,
    region: str | None = Query(
        default=None,
        max_length=2,
        description="Coarse region chosen on the device (ISO 3166-1 alpha-2). 'ZZ' = the user chose no region.",
    ),
    lang: str | None = Query(
        default=None, max_length=35, description="Language (BCP 47); only the primary tag is used"
    ),
    db: Session = Depends(get_db),
):
    """Creatives for this context — contextual selection only (region, language), nothing about the viewer."""
    if not ads.SERVE_LIMITER.allow(request.client.host if request.client else None):
        raise HTTPException(status_code=429, detail="Too many requests.")
    chosen = ads.normalize_region(region)
    if chosen is None and config.ADS_REGION_HEADER:
        # Operator-enabled fallback (edge country header), only when the client said nothing. Not stored.
        chosen = ads.normalize_region(request.headers.get(config.ADS_REGION_HEADER))
    return ads.serve(db, chosen, ads.normalize_language(lang))


@api.post("/ads/metrics", response_model=schemas.AdEventsResponse, tags=["ads"])
def ads_metrics(body: schemas.AdEventsRequest, request: Request, db: Session = Depends(get_db)):
    """Anonymous impression/click counts: added to a per-day, per-region total. Nothing else is recorded."""
    if not ads.METRICS_LIMITER.allow(request.client.host if request.client else None):
        raise HTTPException(status_code=429, detail="Too many requests.")
    return schemas.AdEventsResponse(accepted=ads.record_events(db, body.events))


@api.get("/ads/fake/{name}.js", include_in_schema=False)
def ads_fake_script(name: str):
    """Built-in fake provider tags (AdSense / Microsoft / network script) — dev and test only."""
    script = ad_sources.fake_script(name) if config.ADS_FAKE_PROVIDERS else None
    if script is None:
        raise HTTPException(status_code=404, detail="Not found")
    return Response(script, media_type="application/javascript", headers={"Cache-Control": "no-store"})


@api.get("/ads/fake/network.json", include_in_schema=False)
def ads_fake_network(region: str = "ZZ", lang: str = ""):
    """What the fake third-party network answers (the server-to-server call uses the same data)."""
    if not config.ADS_FAKE_PROVIDERS:
        raise HTTPException(status_code=404, detail="Not found")
    return ad_sources.fake_network_payload(ads.normalize_region(region) or "ZZ", ads.normalize_language(lang) or "")


# ==================== Meta ====================


@api.get("/health", tags=["meta"])
def health():
    return {
        "status": "ok",
        "service": "account",
        "version": config.SERVICE_VERSION,
        "environment": config.ENV,
        "database": "postgresql" if config.IS_POSTGRES else "sqlite",
        "docs": "admin-only" if config.DOCS_REQUIRE_ADMIN else "open",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


api.include_router(admin.router)
api.include_router(fakes.router)  # built-in Stripe/PayPal stand-ins (404 unless BILLING_FAKE_PROVIDERS)
api.include_router(messaging.router)  # built-in e-mail/SMS test inbox (404 unless MESSAGING_FAKE_PROVIDERS)
api.include_router(oauth.router)  # built-in Google/Facebook consent screens (404 unless OAUTH_FAKE_PROVIDERS)
app.include_router(api)
docs_gate.install(app)  # /api/docs · /api/redoc · /api/openapi.json


# ==================== Optional: serve the separate ad-admin dashboard ====================
# The dashboard (apps/ads-admin) is its own deployment (static files, own container/CDN) and
# talks to this API across origins. For single-process setups it can also be served from here.
if config.ADS_ADMIN_DIST and config.ADS_ADMIN_DIST.is_dir():

    class AdsAdminStatic(StaticFiles):
        async def get_response(self, path: str, scope):
            response = await super().get_response(path, scope)
            response.headers["Cache-Control"] = "no-cache"
            response.headers["X-Frame-Options"] = "DENY"
            return response

    app.mount("/ads-admin", AdsAdminStatic(directory=config.ADS_ADMIN_DIST, html=True), name="ads-admin")


# ==================== Legacy paths (previous app versions) ====================
# Browsers that still run an older build of the app (its service worker caches the old page)
# keep requesting the old endpoints. Instead of 404s, heal them: /sw.js becomes a "safety"
# service worker that unregisters itself, clears the old caches and reloads every open tab,
# so the next load gets the current app. The old API paths answer 410 Gone with a clear text.

LEGACY_SAFETY_WORKER = """// Safety service worker: replaces an outdated one, cleans up and reloads the app.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.map((k) => caches.delete(k)));
    await self.registration.unregister();
    const clients = await self.clients.matchAll({ type: 'window' });
    for (const client of clients) client.navigate(client.url);
  })());
});
"""


@app.get("/sw.js", include_in_schema=False)
def legacy_service_worker():
    return Response(LEGACY_SAFETY_WORKER, media_type="application/javascript", headers={"Cache-Control": "no-cache"})


@app.api_route(
    "/{legacy:path}",
    methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
    include_in_schema=False,
)
def legacy_paths(legacy: str):
    """Catch-all (runs after the real routes and, when WEB_DIST is set, is never reached for
    files the PWA mount serves — the mount is matched first)."""
    head = legacy.split("/", 1)[0]
    if head in ("update", "tasks", "events", "today", "health", "js", "css", "icons", "manifest.webmanifest"):
        raise HTTPException(
            status_code=410,
            detail="This address belongs to an old version of the app. Reload the page to load the current version.",
        )
    if legacy in ("", "index.html"):
        if config.DEV_WEB_URL:
            return RedirectResponse(url=config.DEV_WEB_URL, status_code=307)
        return HTMLResponse(
            "<!doctype html><meta charset=utf-8><title>Personal Assistant API</title>"
            "<body style='font-family:system-ui;max-width:560px;margin:60px auto;line-height:1.5'>"
            "<h2>Personal Assistant — API only</h2>"
            "<p>This process serves the API (<a href='/api/docs'>/api/docs</a>). The web app is not served "
            "from here because <code>WEB_DIST</code> is not set.</p>"
            "<ul><li><code>python dev.py run</code> → app at <a href='http://localhost:4200/'>http://localhost:4200/</a></li>"
            "<li><code>python dev.py serve</code> → production build served from this address</li></ul></body>"
        )
    raise HTTPException(status_code=404, detail="Not found")


# ==================== Optional: serve the built PWA ====================
# In Azure the Static Web App/CDN serves apps/web; locally (and for small self-hosted setups)
# WEB_DIST lets this process serve it from the same origin. ngsw.json and index.html must not
# be cached by intermediaries so the service worker can detect new builds.
if config.WEB_DIST and config.WEB_DIST.is_dir():
    from starlette.exceptions import HTTPException as StarletteHTTPException

    class PwaStatic(StaticFiles):
        async def get_response(self, path: str, scope):
            try:
                response = await super().get_response(path, scope)
            except StarletteHTTPException as exc:
                if exc.status_code != 404:
                    raise
                path = "index.html"  # single-page app: unknown routes render the app shell
                response = await super().get_response(path, scope)
            name = path.rsplit("/", 1)[-1]
            hashed = (
                "-" in name and name.rsplit(".", 1)[0].rsplit("-", 1)[-1].isalnum() and len(name.rsplit("-", 1)[-1]) > 8
            )
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable" if hashed else "no-cache"
            return response

    # The catch-all above would shadow the mount; drop it (the mount serves index.html for unknown paths).
    app.router.routes[:] = [r for r in app.router.routes if getattr(r, "path", "") != "/{legacy:path}"]
    app.mount("/", PwaStatic(directory=config.WEB_DIST, html=True), name="pwa")
