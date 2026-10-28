"""Sign in with Google, Facebook or Apple — OAuth 2.0 authorization-code flow (OpenID Connect
for Google and Apple) as an alternative to the password at sign-in, and as a link on an
existing account.

What the provider replaces — and what it cannot
  The provider proves *who* is signing in; it replaces the **password check**. It cannot
  replace the password's other job in this zero-knowledge design: deriving the key that wraps
  the user's data key on the device (PLATFORM_DESIGN.md §3.2). So a sign-in-with-provider account
  chooses an **encryption passphrase** once (it becomes the account's password too, so e-mail +
  passphrase keeps working), and types it on each new device to unlock the data — exactly what
  the password does for a password account. Neither the provider nor this server ever sees it.

Flow
  1. ``GET  /auth/oauth/{provider}/start?return_to=…``  → 302 to the provider. The ``state`` is
     an encrypted, signed blob (AES-GCM under TOKEN_SECRET): nonce, PKCE verifier, where to
     return, and — when linking from the account screen — the account. Nothing is stored.
  2. provider → ``GET /auth/oauth/{provider}/callback?code&state`` → code exchanged for tokens
     (server-to-server, client secret + PKCE), the profile fetched, then:
       * an identity already linked → that account;
       * else an account with the same e-mail, if the provider says it is verified → linked
         (audited) — a provider e-mail that is *not* verified never opens an account;
       * else a new account (no password, no data key yet).
     → 302 ``return_to#oauth=<ticket>`` (fragment: never in server logs or Referer headers).
  3. ``POST /auth/oauth/complete {ticket, device}`` → the session — or ``202`` with the
     two-factor challenge when the account has a second factor (the provider counts as the
     first factor, like the password).
  4. New accounts: ``POST /me/passphrase {auth_key, wrapped_data_key, kdf_params}`` — the device
     derived the keys from the chosen passphrase, generated and wrapped the data key.

Linking: ``POST /me/oauth/{provider}/link`` → the authorization URL; the callback attaches the
identity to the signed-in account → ``return_to#oauth_linked=<provider>``.
``DELETE /me/oauth/{provider}`` unlinks — refused when it would leave no way to sign in.

Built-in fakes (config.OAUTH_FAKE_PROVIDERS: dev/test by default, never in prod) stand in for
a provider without credentials: the authorization page is served by this API
(``/auth/oauth/fake/{provider}/authorize`` — type any e-mail), the code/token/userinfo exchange
happens in-process through the same adapter code that talks to the real provider.
"""

# ruff: noqa: E501  (embedded HTML of the fake consent screen)
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, quote, urlencode, urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

import accounts
import auth
import billing
import config
import httpclient
import models
import schemas

GOOGLE = "google"
FACEBOOK = "facebook"
APPLE = "apple"
PROVIDERS = (GOOGLE, FACEBOOK, APPLE)
STATE_TTL_SECONDS = 600
TICKET_TTL_SECONDS = 90
TICKET_TYPE = "oauth"
FAKE_PATH = "/api/v1/auth/oauth/fake"


class OAuthError(Exception):
    """Something the user should see on the sign-in screen (provider refused, no e-mail, …)."""


@dataclass
class Profile:
    subject: str
    email: str
    email_verified: bool
    name: str = ""


# ---------------------------------------------------------------------------- sealed state


def _key() -> bytes:
    return hashlib.sha256((config.TOKEN_SECRET + ":oauth-state").encode()).digest()


def seal(payload: dict[str, Any], ttl: int = STATE_TTL_SECONDS) -> str:
    """AES-GCM-encrypted, authenticated blob with an expiry — the ``state`` parameter and the
    fake provider's codes. Stateless: any replica can open it."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce = secrets.token_bytes(12)
    body = json.dumps({**payload, "exp": int(time.time()) + ttl}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(nonce + AESGCM(_key()).encrypt(nonce, body, b"oauth")).decode().rstrip("=")


def unseal(token: str | None) -> dict[str, Any] | None:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if not token:
        return None
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        payload = json.loads(AESGCM(_key()).decrypt(raw[:12], raw[12:], b"oauth"))
    except (ValueError, InvalidTag, IndexError):
        return None
    if not isinstance(payload, dict) or payload.get("exp", 0) < time.time():
        return None
    return payload


def issue_ticket(account_id: str) -> str:
    """Hands the browser its sign-in result: short-lived, exchanged once for a session."""
    return auth.sign(
        {
            "typ": TICKET_TYPE,
            "sub": account_id,
            "nonce": secrets.token_hex(8),
            "exp": int(time.time()) + TICKET_TTL_SECONDS,
        }
    )


def decode_ticket(token: str) -> str | None:
    payload = auth.verify_signed(token)
    if payload is None or payload.get("typ") != TICKET_TYPE:
        return None
    return payload.get("sub") or None


# ---------------------------------------------------------------------------- providers


class Provider:
    """One identity provider (the strategy). Subclasses know the endpoints and the shape of the
    profile; everything else — state, linking, fakes — is shared."""

    name = "base"
    label = ""
    authorize_url = ""
    token_url = ""
    userinfo_url = ""
    scope = ""
    pkce = True

    def __init__(self, transport: httpclient.HttpTransport | None = None):
        self.transport = transport or httpclient.HttpTransport()

    # ---- configuration -------------------------------------------------------------------
    def credentials(self) -> tuple[str, str]:
        raise NotImplementedError

    def configured(self) -> bool:
        cid, secret = self.credentials()
        return bool(cid and secret)

    @property
    def fake(self) -> bool:
        return not self.configured() and config.OAUTH_FAKE_PROVIDERS

    @property
    def available(self) -> bool:
        return self.configured() or self.fake

    def describe(self) -> schemas.OAuthProviderInfo:
        return schemas.OAuthProviderInfo(name=self.name, label=self.label, available=self.available, fake=self.fake)

    # ---- the two provider calls ------------------------------------------------------------
    def authorization_url(self, api_base: str, redirect_uri: str, state: str, code_challenge: str) -> str:
        if self.fake:
            return f"{api_base}{FAKE_PATH}/{self.name}/authorize?" + urlencode(
                {"redirect_uri": redirect_uri, "state": state}
            )
        cid, _ = self.credentials()
        params = {
            "client_id": cid,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": self.scope,
            "state": state,
        }
        if self.pkce:
            params.update(code_challenge=code_challenge, code_challenge_method="S256")
        return f"{self.authorize_url}?{urlencode(params)}"

    def _transport(self):
        return _FakeTransport(self) if self.fake else self.transport

    def fetch_profile(self, code: str, redirect_uri: str, code_verifier: str) -> Profile:
        raise NotImplementedError

    def _request(self, method: str, url: str, **kw) -> dict[str, Any]:
        try:
            status, body = self._transport().request(method, url, **kw)
        except httpclient.TransportError as exc:
            raise OAuthError(f"{self.label} could not be reached ({exc}). Please try again.") from exc
        if status >= 400:
            detail = body.get("error_description") or body.get("error") or status
            if isinstance(detail, dict):
                detail = detail.get("message") or json.dumps(detail)
            raise OAuthError(f"{self.label} refused the sign-in ({detail}).")
        return body


class GoogleProvider(Provider):
    name = GOOGLE
    label = "Google"
    authorize_url = "https://accounts.google.com/o/oauth2/v2/auth"
    token_url = "https://oauth2.googleapis.com/token"
    userinfo_url = "https://openidconnect.googleapis.com/v1/userinfo"
    scope = "openid email profile"

    def credentials(self) -> tuple[str, str]:
        return config.GOOGLE_CLIENT_ID, config.GOOGLE_CLIENT_SECRET

    def authorization_url(self, api_base, redirect_uri, state, code_challenge):
        url = super().authorization_url(api_base, redirect_uri, state, code_challenge)
        return url if self.fake else url + "&prompt=select_account"

    def fetch_profile(self, code, redirect_uri, code_verifier):
        cid, secret = ("fake-client", "fake-secret") if self.fake else self.credentials()
        tokens = self._request(
            "POST",
            self.token_url,
            headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
            data=urlencode(
                {
                    "code": code,
                    "client_id": cid,
                    "client_secret": secret,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                    "code_verifier": code_verifier,
                }
            ).encode(),
        )
        access_token = tokens.get("access_token")
        if not access_token:
            raise OAuthError("Google did not return an access token.")
        info = self._request("GET", self.userinfo_url, headers={"Authorization": f"Bearer {access_token}"})
        if not info.get("sub"):
            raise OAuthError("Google did not return a user id.")
        return Profile(
            subject=str(info["sub"]),
            email=str(info.get("email") or "").lower(),
            email_verified=bool(info.get("email_verified")),
            name=str(info.get("name") or ""),
        )


class FacebookProvider(Provider):
    name = FACEBOOK
    label = "Facebook"
    authorize_url = "https://www.facebook.com/v19.0/dialog/oauth"
    token_url = "https://graph.facebook.com/v19.0/oauth/access_token"
    userinfo_url = "https://graph.facebook.com/v19.0/me"
    scope = "email,public_profile"
    pkce = False  # Facebook Login's web flow is client-secret based

    def credentials(self) -> tuple[str, str]:
        return config.FACEBOOK_APP_ID, config.FACEBOOK_APP_SECRET

    def fetch_profile(self, code, redirect_uri, code_verifier):
        cid, secret = ("fake-client", "fake-secret") if self.fake else self.credentials()
        tokens = self._request(
            "GET",
            self.token_url
            + "?"
            + urlencode({"client_id": cid, "client_secret": secret, "redirect_uri": redirect_uri, "code": code}),
        )
        access_token = tokens.get("access_token")
        if not access_token:
            raise OAuthError("Facebook did not return an access token.")
        info = self._request(
            "GET", self.userinfo_url + "?" + urlencode({"fields": "id,name,email", "access_token": access_token})
        )
        if not info.get("id"):
            raise OAuthError("Facebook did not return a user id.")
        # Facebook only hands out e-mail addresses it has confirmed.
        return Profile(
            subject=str(info["id"]),
            email=str(info.get("email") or "").lower(),
            email_verified=bool(info.get("email")),
            name=str(info.get("name") or ""),
        )


# ---- Apple: JWT plumbing (standard library + cryptography, which is already a dependency) ----


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unb64url(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def apple_client_secret(now: float | None = None) -> str:
    """Apple's "client secret" is a JWT you sign yourself with the Sign in with Apple key
    (ES256): iss = Team ID, sub = Services ID, aud = Apple, short-lived (we mint one per call)."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

    try:
        key = serialization.load_pem_private_key(config.APPLE_PRIVATE_KEY.encode(), password=None)
    except (ValueError, TypeError) as exc:
        raise OAuthError("Sign in with Apple is misconfigured on this server (APPLE_PRIVATE_KEY).") from exc
    if not isinstance(key, ec.EllipticCurvePrivateKey):
        raise OAuthError("Sign in with Apple is misconfigured on this server (the key is not an EC P-256 key).")
    issued = int(now or time.time())
    header = {"alg": "ES256", "kid": config.APPLE_KEY_ID, "typ": "JWT"}
    claims = {
        "iss": config.APPLE_TEAM_ID,
        "iat": issued,
        "exp": issued + 600,
        "aud": "https://appleid.apple.com",
        "sub": config.APPLE_CLIENT_ID,
    }
    signing_input = f"{_b64url(json.dumps(header, separators=(',', ':')).encode())}.{_b64url(json.dumps(claims, separators=(',', ':')).encode())}"
    r, s_ = decode_dss_signature(key.sign(signing_input.encode(), ec.ECDSA(hashes.SHA256())))
    return f"{signing_input}.{_b64url(r.to_bytes(32, 'big') + s_.to_bytes(32, 'big'))}"


def verify_apple_id_token(token: str, jwks: dict[str, Any], audience: str, now: float | None = None) -> dict:
    """The claims of an ``id_token`` whose RS256 signature one of Apple's published keys
    confirms, and whose issuer / audience / expiry fit. Raises OAuthError otherwise."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding, rsa

    try:
        head_b64, body_b64, sig_b64 = token.split(".")
        header = json.loads(_unb64url(head_b64))
        claims = json.loads(_unb64url(body_b64))
        signature = _unb64url(sig_b64)
    except (ValueError, UnicodeDecodeError) as exc:
        raise OAuthError("Apple returned an identity token this server could not read.") from exc
    key = next((k for k in jwks.get("keys", []) if k.get("kid") == header.get("kid") and k.get("kty") == "RSA"), None)
    if key is None or header.get("alg") != "RS256":
        raise OAuthError("Apple's identity token was signed with an unknown key.")
    try:
        public = rsa.RSAPublicNumbers(
            int.from_bytes(_unb64url(key["e"]), "big"), int.from_bytes(_unb64url(key["n"]), "big")
        ).public_key()
        public.verify(signature, f"{head_b64}.{body_b64}".encode(), padding.PKCS1v15(), hashes.SHA256())
    except (InvalidSignature, ValueError) as exc:
        raise OAuthError("Apple's identity token failed verification.") from exc
    aud = claims.get("aud")
    if claims.get("iss") != "https://appleid.apple.com" or audience not in (aud if isinstance(aud, list) else [aud]):
        raise OAuthError("Apple's identity token was not issued for this app.")
    if float(claims.get("exp", 0)) < (now or time.time()):
        raise OAuthError("Apple's identity token has expired. Please try again.")
    return claims


def _truthy(value: Any) -> bool:
    return value is True or str(value).lower() == "true"


class AppleProvider(Provider):
    """Sign in with Apple (web). Differences from the others: the consent screen POSTs the
    result to the callback (``response_mode=form_post``), the client secret is a JWT we sign,
    and the profile comes from the signed ``id_token`` — there is no userinfo endpoint."""

    name = APPLE
    label = "Apple"
    authorize_url = "https://appleid.apple.com/auth/authorize"
    token_url = "https://appleid.apple.com/auth/token"
    userinfo_url = ""  # the id_token carries the profile
    jwks_url = "https://appleid.apple.com/auth/keys"
    scope = "name email"
    pkce = False
    form_post = True

    def __init__(self, transport: httpclient.HttpTransport | None = None):
        super().__init__(transport)
        self._jwks: tuple[float, dict[str, Any]] | None = None

    def credentials(self) -> tuple[str, str]:
        ok = config.APPLE_CLIENT_ID and config.APPLE_TEAM_ID and config.APPLE_KEY_ID and config.APPLE_PRIVATE_KEY
        return (config.APPLE_CLIENT_ID, "configured") if ok else ("", "")

    def authorization_url(self, api_base, redirect_uri, state, code_challenge):
        if self.fake:
            return super().authorization_url(api_base, redirect_uri, state, code_challenge)
        cid, _ = self.credentials()
        params = {
            "client_id": cid,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": self.scope,
            "response_mode": "form_post",
            "state": state,
        }
        return f"{self.authorize_url}?{urlencode(params)}"

    def _keys(self) -> dict[str, Any]:
        if self._jwks is None or self._jwks[0] < time.time() - 3600:
            self._jwks = (time.time(), self._request("GET", self.jwks_url))
        return self._jwks[1]

    def fetch_profile(self, code, redirect_uri, code_verifier):
        cid, _ = ("fake-client", "fake-secret") if self.fake else self.credentials()
        secret = "fake-secret" if self.fake else apple_client_secret()
        tokens = self._request(
            "POST",
            self.token_url,
            headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
            data=urlencode(
                {
                    "client_id": cid,
                    "client_secret": secret,
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": redirect_uri,
                }
            ).encode(),
        )
        id_token = tokens.get("id_token")
        if not id_token:
            raise OAuthError("Apple did not return an identity token.")
        if self.fake:  # the in-process fake minted it: read the claims (no Apple key to check against)
            claims = json.loads(_unb64url(id_token.split(".")[1]))
        else:
            claims = verify_apple_id_token(id_token, self._keys(), cid)
        if not claims.get("sub"):
            raise OAuthError("Apple did not return a user id.")
        return Profile(
            subject=str(claims["sub"]),
            email=str(claims.get("email") or "").lower(),
            # Apple shares verified addresses only (real or its private relay); both are trustworthy.
            email_verified=bool(claims.get("email")) and _truthy(claims.get("email_verified", True)),
            name="",
        )


GOOGLE_PROVIDER = GoogleProvider()
FACEBOOK_PROVIDER = FacebookProvider()
APPLE_PROVIDER = AppleProvider()


def provider(name: str) -> Provider:
    if name == GOOGLE:
        return GOOGLE_PROVIDER
    if name == FACEBOOK:
        return FACEBOOK_PROVIDER
    if name == APPLE:
        return APPLE_PROVIDER
    raise HTTPException(status_code=404, detail="Unknown sign-in provider.")


def catalog() -> schemas.OAuthProviders:
    return schemas.OAuthProviders(providers=[provider(n).describe() for n in PROVIDERS])


# ---------------------------------------------------------------------------- the fake provider


def _fake_subject(email: str) -> str:
    return "fake-" + hashlib.sha256(email.lower().encode()).hexdigest()[:16]


class _FakeTransport:
    """Answers the token and userinfo calls of a provider in-process. The ``code`` the fake
    authorization page issued is a sealed profile; the access token is the same blob."""

    def __init__(self, p: Provider):
        self.p = p

    def request(self, method: str, url: str, *, headers=None, data: bytes | None = None) -> tuple[int, dict]:
        base, _, query = url.partition("?")
        params = {k: v[0] for k, v in parse_qs(query).items()}
        form = {k: v[0] for k, v in parse_qs((data or b"").decode()).items()}
        if base == self.p.token_url:
            profile = unseal(form.get("code") or params.get("code"))
            if not profile or profile.get("provider") != self.p.name:
                return 400, {"error": "invalid_grant", "error_description": "fake: unknown or expired code"}
            answer = {"access_token": form.get("code") or params.get("code"), "token_type": "Bearer"}
            if self.p.name == APPLE:  # Apple: the profile travels in a (here unsigned) id_token
                claims = {
                    "iss": "https://appleid.apple.com",
                    "aud": "fake-client",
                    "sub": profile["sub"],
                    "email": profile["email"],
                    "email_verified": "true",
                    "is_private_email": "true" if profile.get("private") else "false",
                    "exp": int(time.time()) + 600,
                }
                header = _b64url(b'{"alg":"fake"}')
                answer["id_token"] = f"{header}.{_b64url(json.dumps(claims).encode())}."
            return 200, answer
        if base == self.p.userinfo_url:
            token = (headers or {}).get("Authorization", "").removeprefix("Bearer ").strip() or params.get(
                "access_token"
            )
            profile = unseal(token)
            if not profile:
                return 401, {"error": "invalid_token"}
            if self.p.name == GOOGLE:
                return 200, {
                    "sub": profile["sub"],
                    "email": profile["email"],
                    "email_verified": profile.get("verified", True),
                    "name": profile.get("name", ""),
                }
            return 200, {"id": profile["sub"], "name": profile.get("name", ""), "email": profile["email"]}
        return 404, {"error": "fake: unknown endpoint"}


router = APIRouter(prefix="/auth/oauth/fake", include_in_schema=False)


def _fake_guard(name: str) -> Provider:
    p = provider(name)
    if not p.fake or config.IS_PROD:
        raise HTTPException(status_code=404)
    return p


@router.get("/{name}/authorize", response_class=HTMLResponse)
def fake_authorize(name: str, redirect_uri: str = "", state: str = ""):
    """Stands in for the provider's consent screen: pick the identity to continue with."""
    p = _fake_guard(name)
    brand = {GOOGLE: "#4285f4", FACEBOOK: "#1877f2"}.get(name, "#000")
    return HTMLResponse(
        f"""<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Sign in with {p.label} (built-in fake)</title>
<style>
body{{font-family:system-ui,sans-serif;max-width:420px;margin:40px auto;padding:0 16px;line-height:1.5;color:#111827}}
.note{{background:#fff3cd;border:1px solid #ffe69c;padding:8px 12px;border-radius:8px;font-size:14px}}
label{{display:block;font-size:.9rem;color:#6b7280;margin-top:12px}}
input[type=email],input[type=text]{{width:100%;box-sizing:border-box;padding:10px 12px;font-size:16px;border:1px solid #e2e5ea;border-radius:8px}}
button{{margin-top:16px;padding:12px 18px;font-size:16px;border-radius:8px;border:0;cursor:pointer;min-height:44px}}
.go{{background:{brand};color:#fff}} .cancel{{background:#fff;border:1px solid #999;margin-left:8px}}
</style>
<h2>Sign in with {p.label}</h2>
<p class=note><strong>Built-in fake.</strong> No {p.label} credentials are configured on this server, so this
page stands in for {p.label}'s sign-in screen. Type any e-mail address to continue as that person —
nothing leaves this machine.</p>
<form method=post action="{FAKE_PATH}/{name}/authorize">
<input type=hidden name=redirect_uri value="{_attr(redirect_uri)}"><input type=hidden name=state value="{_attr(state)}">
<label for=email>E-mail address at {p.label}</label>
<input id=email name=email type=email required autocomplete=email value="you@{"gmail.com" if name == GOOGLE else "icloud.com" if name == APPLE else "example.com"}">
<label for=name>Name</label><input id=name name=name type=text value="Test Person">
{"<label><input type=checkbox name=unverified> Pretend the address is NOT verified (Google then refuses to auto-link)</label>" if name == GOOGLE else ""}
{"<label><input type=checkbox name=private id=fake-private> Hide my e-mail (Apple's private relay address instead)</label>" if name == APPLE else ""}
<button class=go id=fake-continue type=submit>Continue as this person</button>
<button class=cancel id=fake-cancel type=submit name=cancel value=1 formnovalidate>Cancel</button>
</form></html>"""
    )


def _attr(value: str) -> str:
    return value.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")


@router.post("/{name}/authorize")
async def fake_authorize_submit(name: str, request: Request):
    p = _fake_guard(name)
    form = {k: v[0] for k, v in parse_qs((await request.body()).decode(), keep_blank_values=True).items()}
    redirect_uri, state = form.get("redirect_uri", ""), form.get("state", "")
    if not redirect_uri.startswith(("http://", "https://", "/")):
        raise HTTPException(status_code=400, detail="fake: bad redirect_uri")
    if form.get("cancel"):
        if getattr(p, "form_post", False):
            return HTMLResponse(
                f"""<!doctype html><meta charset=utf-8><form id=f method=post action="{_attr(redirect_uri)}">
<input type=hidden name=error value="user_cancelled_authorize"><input type=hidden name=state value="{_attr(state)}">
</form><script>document.getElementById('f').submit()</script>"""
            )
        return RedirectResponse(
            f"{redirect_uri}?{urlencode({'error': 'access_denied', 'state': state})}", status_code=303
        )
    email = form.get("email", "").strip().lower()
    subject = _fake_subject(email)
    if p.name == APPLE and form.get("private") and email:
        email = f"{subject.removeprefix('fake-')}@privaterelay.appleid.com"  # Apple's "Hide my email" relay
    code = seal(
        {
            "provider": p.name,
            "sub": subject,
            "email": email,
            "name": form.get("name", "").strip(),
            "verified": not form.get("unverified"),
            "private": bool(form.get("private")),
        },
        ttl=120,
    )
    if getattr(p, "form_post", False):
        # Apple posts the result to the callback (response_mode=form_post): an auto-submitting form.
        user = json.dumps({"name": {"firstName": form.get("name", "").split(" ")[0], "lastName": ""}, "email": email})
        return HTMLResponse(
            f"""<!doctype html><meta charset=utf-8><title>Continuing…</title>
<form id=f method=post action="{_attr(redirect_uri)}"><input type=hidden name=code value="{_attr(code)}">
<input type=hidden name=state value="{_attr(state)}"><input type=hidden name=user value="{_attr(user)}">
<noscript><button id=fake-post>Continue</button></noscript></form><script>document.getElementById('f').submit()</script>"""
        )
    return RedirectResponse(f"{redirect_uri}?{urlencode({'code': code, 'state': state})}", status_code=303)


# ---------------------------------------------------------------------------- URLs


def api_base(request: Request) -> str:
    return config.PUBLIC_API_URL or str(request.base_url).rstrip("/")


def redirect_uri(base: str, name: str) -> str:
    return f"{base}/api/v1/auth/oauth/{name}/callback"


def safe_return_to(value: str | None, request: Request) -> str:
    """Where the browser may be sent after the provider: the web app (WEB_APP_URL / DEV_WEB_URL /
    this origin / CORS_ORIGINS — and localhost outside prod). Anything else falls back to the app."""
    default = config.WEB_APP_URL or config.DEV_WEB_URL or "/"
    if not value:
        return default
    value = value.split("#", 1)[0]
    parts = urlsplit(value)
    if not parts.scheme and not parts.netloc:
        return value if value.startswith("/") and not value.startswith("//") else default
    origin = f"{parts.scheme}://{parts.netloc}"
    allowed = {o.rstrip("/") for o in (config.WEB_APP_URL, config.DEV_WEB_URL, *config.CORS_ORIGINS) if o}
    allowed.add(str(request.base_url).rstrip("/"))
    if origin in allowed or (not config.IS_PROD and parts.hostname in ("localhost", "127.0.0.1")):
        return value
    return default


def _with_fragment(url: str, **params: str) -> str:
    return url + "#" + urlencode(params, quote_via=quote)


# ---------------------------------------------------------------------------- the flow


def start_url(request: Request, name: str, return_to: str | None, *, link_account: models.Account | None = None) -> str:
    """The provider's authorization URL for a sign-in (or for linking ``link_account``)."""
    p = provider(name)
    if not p.available:
        raise HTTPException(status_code=503, detail=f"Sign in with {p.label} is not set up on this server.")
    base = api_base(request)
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    state = seal(
        {
            "p": name,
            "n": secrets.token_hex(8),
            "v": verifier,
            "r": safe_return_to(return_to, request),
            "link": link_account.id if link_account else None,
        }
    )
    return p.authorization_url(base, redirect_uri(base, name), state, challenge)


def handle_callback(db: Session, request: Request, name: str, params: dict[str, str]) -> str:
    """The provider sent the browser back: returns where to send it next (with the result in the
    URL fragment). Never raises for user-facing problems — they travel as ``oauth_error``."""
    p = provider(name)
    state = unseal(params.get("state"))
    fallback = safe_return_to(None, request)
    if not state or state.get("p") != name:
        return _with_fragment(fallback, oauth_error="This sign-in link has expired or is invalid. Please try again.")
    return_to = state.get("r") or fallback
    if params.get("error"):
        reason = params.get("error_description") or params["error"]
        message = (
            f"You cancelled the sign-in with {p.label}."
            if params["error"] in ("access_denied", "user_cancelled_authorize")
            else f"{p.label} refused the sign-in ({reason})."
        )
        return _with_fragment(return_to, oauth_error=message)
    try:
        code = params.get("code") or ""
        if not code:
            raise OAuthError(f"{p.label} did not return a code.")
        profile = p.fetch_profile(code, redirect_uri(api_base(request), name), state.get("v", ""))
        if state.get("link"):
            return _with_fragment(return_to, oauth_linked=link_identity(db, p, profile, state["link"]).provider)
        account = account_for(db, p, profile)
    except OAuthError as exc:
        return _with_fragment(return_to, oauth_error=str(exc))
    db.commit()
    return _with_fragment(return_to, oauth=issue_ticket(account.id), provider=name)


def _identity(db: Session, p: Provider, subject: str) -> models.OAuthIdentity | None:
    return db.execute(
        select(models.OAuthIdentity).where(
            models.OAuthIdentity.provider == p.name, models.OAuthIdentity.subject == subject
        )
    ).scalar_one_or_none()


def account_for(db: Session, p: Provider, profile: Profile) -> models.Account:
    """The account this provider identity signs in to: linked before, linked now by verified
    e-mail, or created. Caller commits."""
    identity = _identity(db, p, profile.subject)
    if identity is not None:
        account = db.get(models.Account, identity.account_id)
        if account is None or account.deleted_at:
            raise OAuthError("This account was deleted. Create a new one to continue.")
        identity.last_login_at = models.utcnow()
        auth.apply_bootstrap_role(account)
        return account
    if not profile.email:
        raise OAuthError(
            f"{p.label} did not share an e-mail address. Allow it to share your e-mail, or sign in another way."
        )
    account = db.execute(
        select(models.Account).where(models.Account.email == profile.email, models.Account.deleted_at.is_(None))
    ).scalar_one_or_none()
    if account is not None:
        if not profile.email_verified:
            raise OAuthError(
                f"An account with this e-mail exists, but {p.label} has not verified the address. Sign in with your "
                f"password, then link {p.label} from the account screen."
            )
        how = "verified_email"
    else:
        salt = auth.new_salt()
        account = models.Account(
            email=profile.email, auth_salt=salt, auth_hash="", wrapped_data_key="", kdf_params="{}"
        )
        auth.apply_bootstrap_role(account)
        db.add(account)
        db.flush()
        billing.subscription_for(db, account)
        accounts.audit(db, account, "oauth.register", account, provider=p.name)
        how = "new_account"
    db.add(
        models.OAuthIdentity(
            provider=p.name,
            subject=profile.subject,
            account_id=account.id,
            email=profile.email,
            last_login_at=models.utcnow(),
        )
    )
    accounts.audit(db, account, "oauth.linked", account, provider=p.name, how=how)
    return account


def link_identity(db: Session, p: Provider, profile: Profile, account_id: str) -> models.OAuthIdentity:
    """From the account screen: attach this provider identity to the signed-in account."""
    account = db.get(models.Account, account_id)
    if account is None or account.deleted_at:
        raise OAuthError("This account no longer exists.")
    existing = _identity(db, p, profile.subject)
    if existing is not None and existing.account_id != account.id:
        raise OAuthError(f"This {p.label} account is already linked to another account.")
    if existing is None:
        existing = models.OAuthIdentity(provider=p.name, subject=profile.subject, account_id=account.id)
        db.add(existing)
    existing.email = profile.email
    existing.last_login_at = models.utcnow()
    accounts.audit(db, account, "oauth.linked", account, provider=p.name, how="account_screen")
    db.commit()
    return existing


def complete(db: Session, ticket: str) -> models.Account:
    account_id = decode_ticket(ticket)
    account = db.get(models.Account, account_id) if account_id else None
    if account is None or account.deleted_at:
        raise HTTPException(status_code=401, detail="This sign-in has expired. Please sign in again.")
    return account


def identities(db: Session, account: models.Account) -> list[schemas.OAuthIdentityOut]:
    rows = db.execute(
        select(models.OAuthIdentity)
        .where(models.OAuthIdentity.account_id == account.id)
        .order_by(models.OAuthIdentity.created_at)
    ).scalars()
    return [
        schemas.OAuthIdentityOut(
            provider=r.provider, email=r.email, linked_at=r.created_at, last_login_at=r.last_login_at
        )
        for r in rows
    ]


def unlink(db: Session, account: models.Account, name: str) -> None:
    """Caller commits."""
    p = provider(name)
    rows = db.execute(select(models.OAuthIdentity).where(models.OAuthIdentity.account_id == account.id)).scalars().all()
    mine = [r for r in rows if r.provider == p.name]
    if not mine:
        raise HTTPException(status_code=404, detail=f"{p.label} is not linked to this account.")
    if not account.has_password and len(rows) == len(mine):
        raise HTTPException(
            status_code=409,
            detail=f"{p.label} is the only way to sign in to this account. Set a passphrase (password) first.",
        )
    for r in mine:
        db.delete(r)
    accounts.audit(db, account, "oauth.unlinked", account, provider=p.name)


def set_passphrase(db: Session, account: models.Account, body: schemas.SetPassphraseRequest) -> None:
    """A sign-in-with-provider account chooses its encryption passphrase (= password). The device
    derived the auth key and wrapped its new data key with the passphrase. Once. Caller commits."""
    if account.has_password:
        raise HTTPException(status_code=409, detail="This account already has a password.")
    salt = auth.new_salt()
    account.auth_salt = salt
    account.auth_hash = auth.hash_auth_key(body.auth_key, salt)
    account.wrapped_data_key = body.wrapped_data_key
    account.kdf_params = json.dumps(body.kdf_params)
    accounts.audit(db, account, "passphrase.set", account)
