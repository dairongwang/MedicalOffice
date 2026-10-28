"""Sign in with Google / Facebook: the authorization-code round trip through the built-in fake
providers (start → consent screen → callback → ticket → session), account creation, linking by
verified e-mail, the encryption passphrase, linking/unlinking from the account screen, the
second factor on top, tampering, and the real adapters against stub transports."""

from __future__ import annotations

import base64
import json
import re
import time
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from conftest import auth_key_for, headers, login, register

import auth
import config
import database
import httpclient
import models
import oauth
import totp

EMAIL, PASSWORD = "ann@example.com", "correct horse"


def oauth_sign_in(
    client,
    provider="google",
    email="ann@gmail.com",
    *,
    unverified=False,
    cancel=False,
    return_to="/",
    start_url: str | None = None,
) -> dict[str, str]:
    """Walk the browser's path through the fake provider; returns the fragment of the final URL."""
    if start_url:  # the link endpoint hands out the provider's URL itself
        consent = start_url
    else:
        r = client.get(f"/api/v1/auth/oauth/{provider}/start?return_to={return_to}", follow_redirects=False)
        assert r.status_code == 302, r.text
        consent = r.headers["location"]
    assert consent.startswith(f"http://testserver/api/v1/auth/oauth/fake/{provider}/authorize?")
    page = client.get(consent)
    assert page.status_code == 200 and "Built-in fake" in page.text and "id=fake-continue" in page.text
    q = {k: v[0] for k, v in parse_qs(urlsplit(consent).query).items()}
    assert q["redirect_uri"] == f"http://testserver/api/v1/auth/oauth/{provider}/callback"
    form = {"redirect_uri": q["redirect_uri"], "state": q["state"], "email": email, "name": "Ann Example"}
    if unverified:
        form["unverified"] = "on"
    if cancel:
        form["cancel"] = "1"
    r = client.post(f"/api/v1/auth/oauth/fake/{provider}/authorize", data=form, follow_redirects=False)
    assert r.status_code == 303, r.text
    callback = r.headers["location"]
    assert callback.startswith(q["redirect_uri"] + "?")
    r = client.get(callback, follow_redirects=False)
    assert r.status_code == 302, r.text
    final = r.headers["location"]
    assert final.split("#")[0] == return_to
    return {k: v[0] for k, v in parse_qs(urlsplit(final).fragment).items()}


def complete(client, ticket: str):
    return client.post("/api/v1/auth/oauth/complete", json={"ticket": ticket, "device": {"platform": "test"}})


def set_passphrase(client, session, email, passphrase):
    return client.post(
        "/api/v1/me/passphrase",
        json={
            "auth_key": auth_key_for(passphrase, email),
            "wrapped_data_key": base64.b64encode(b"wrapped-by-passphrase-" + email.encode()).decode(),
            "kdf_params": {"kdf": "pbkdf2", "iterations": 1000},
        },
        headers=headers(session),
    )


# ---------------------------------------------------------------------------- catalogue


def test_providers_are_fakes_in_test_real_when_configured_absent_otherwise(client, monkeypatch):
    cat = {p["name"]: p for p in client.get("/api/v1/auth/oauth/providers").json()["providers"]}
    assert cat["google"] == {"name": "google", "label": "Google", "available": True, "fake": True}
    assert cat["facebook"]["available"] and cat["facebook"]["fake"]
    monkeypatch.setattr(config, "OAUTH_FAKE_PROVIDERS", False)
    cat = {p["name"]: p for p in client.get("/api/v1/auth/oauth/providers").json()["providers"]}
    assert not cat["google"]["available"] and not cat["facebook"]["available"]
    assert client.get("/api/v1/auth/oauth/google/start", follow_redirects=False).status_code == 503
    assert client.get("/api/v1/auth/oauth/fake/google/authorize").status_code == 404
    monkeypatch.setattr(config, "GOOGLE_CLIENT_ID", "cid.apps.googleusercontent.com")
    monkeypatch.setattr(config, "GOOGLE_CLIENT_SECRET", "shh")
    cat = {p["name"]: p for p in client.get("/api/v1/auth/oauth/providers").json()["providers"]}
    assert cat["google"] == {"name": "google", "label": "Google", "available": True, "fake": False}
    r = client.get("/api/v1/auth/oauth/google/start?return_to=/", follow_redirects=False)
    url = urlsplit(r.headers["location"])
    q = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert url.netloc == "accounts.google.com" and q["client_id"] == "cid.apps.googleusercontent.com"
    assert q["redirect_uri"] == "http://testserver/api/v1/auth/oauth/google/callback"
    assert (
        q["scope"] == "openid email profile"
        and q["code_challenge_method"] == "S256"
        and q["prompt"] == "select_account"
    )
    assert oauth.unseal(q["state"])["p"] == "google"
    assert client.get("/api/v1/auth/oauth/nope/start", follow_redirects=False).status_code == 404


# ---------------------------------------------------------------------------- new account via Google


def test_new_account_signs_in_with_google_then_chooses_its_passphrase(client):
    frag = oauth_sign_in(client, "google", "ann@gmail.com")
    assert set(frag) == {"oauth", "provider"} and frag["provider"] == "google"
    r = complete(client, frag["oauth"])
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["email"] == "ann@gmail.com" and s["token"] and s["has_password"] is False and s["wrapped_data_key"] == ""
    me = client.get("/api/v1/me", headers=headers(s)).json()
    assert me["has_password"] is False and [i["provider"] for i in me["identities"]] == ["google"]
    assert me["identities"][0]["email"] == "ann@gmail.com" and me["entitlements"]["membership"] == "free"
    # A ticket is exchanged once … well, it is stateless: but it dies with its short TTL.
    stale = auth.sign({"typ": "oauth", "sub": s["account_id"], "exp": int(time.time()) - 1})
    assert complete(client, stale).status_code == 401
    assert complete(client, s["token"]).status_code == 401  # a session token is not a ticket
    # No password yet: e-mail + anything is refused, and the passphrase can be set exactly once.
    r = client.post("/api/v1/auth/login", json={"email": "ann@gmail.com", "auth_key": "0" * 64})
    assert r.status_code == 401
    r = set_passphrase(client, s, "ann@gmail.com", "my sync passphrase")
    assert r.status_code == 200 and r.json()["has_password"] is True
    assert set_passphrase(client, s, "ann@gmail.com", "another").status_code == 409
    # From now on both ways in work, and they hand out the same wrapped key.
    s2 = login(client, email="ann@gmail.com", password="my sync passphrase")
    frag = oauth_sign_in(client, "google", "ann@gmail.com")
    s3 = complete(client, frag["oauth"]).json()
    assert s3["wrapped_data_key"] == s2["wrapped_data_key"] != "" and s3["has_password"] is True
    assert s3["kdf_params"] == {"kdf": "pbkdf2", "iterations": 1000}
    assert s3["account_id"] == s["account_id"]
    with database.SessionLocal() as db:
        actions = [a.action for a in db.query(models.AuditLog).order_by(models.AuditLog.id)]
        assert actions == ["oauth.register", "oauth.linked", "passphrase.set"]
        assert db.query(models.OAuthIdentity).count() == 1 and db.query(models.Account).count() == 1


def test_cancel_and_errors_come_back_to_the_app(client):
    frag = oauth_sign_in(client, "google", cancel=True)
    assert "cancelled" in frag["oauth_error"]
    frag = oauth_sign_in(
        client, "facebook", email="", cancel=False
    )  # the fake page requires one; the API must not trust it
    assert "did not share an e-mail" in frag["oauth_error"]
    # Tampered / foreign state → the app, with a message; a forged code → provider refusal.
    r = client.get("/api/v1/auth/oauth/google/callback?state=garbage&code=x", follow_redirects=False)
    assert (
        r.status_code == 302
        and "expired or is invalid" in parse_qs(urlsplit(r.headers["location"]).fragment)["oauth_error"][0]
    )
    state = oauth.seal({"p": "google", "n": "x", "v": "v", "r": "/", "link": None})
    r = client.get(f"/api/v1/auth/oauth/google/callback?state={state}&code=forged", follow_redirects=False)
    assert "refused the sign-in" in parse_qs(urlsplit(r.headers["location"]).fragment)["oauth_error"][0]
    r = client.get(f"/api/v1/auth/oauth/facebook/callback?state={state}&code=forged", follow_redirects=False)
    assert (
        "expired or is invalid" in parse_qs(urlsplit(r.headers["location"]).fragment)["oauth_error"][0]
    )  # wrong provider
    assert client.post("/api/v1/auth/oauth/complete", json={"ticket": "not-a-ticket-at-all"}).status_code == 401


# ---------------------------------------------------------------------------- existing accounts


def test_verified_provider_email_links_to_the_existing_account_unverified_does_not(client):
    s = register(client)  # ann@example.com with a password
    frag = oauth_sign_in(client, "google", EMAIL, unverified=True)
    assert "has not verified the address" in frag["oauth_error"]
    assert client.get("/api/v1/me", headers=headers(s)).json()["identities"] == []
    frag = oauth_sign_in(client, "google", EMAIL)
    s2 = complete(client, frag["oauth"]).json()
    assert s2["account_id"] == s["account_id"] and s2["wrapped_data_key"] == s["wrapped_data_key"]
    assert s2["has_password"] is True  # the existing password unlocks the data on the new device
    me = client.get("/api/v1/me", headers=headers(s2)).json()
    assert [i["provider"] for i in me["identities"]] == ["google"] and len(me["devices"]) == 2
    with database.SessionLocal() as db:
        row = db.query(models.AuditLog).filter_by(action="oauth.linked").one()
        assert '"how": "verified_email"' in row.detail
    # Facebook hands out confirmed addresses only → links too; the two identities are separate.
    frag = oauth_sign_in(client, "facebook", EMAIL)
    s3 = complete(client, frag["oauth"]).json()
    assert s3["account_id"] == s["account_id"]
    assert [i["provider"] for i in client.get("/api/v1/me", headers=headers(s3)).json()["identities"]] == [
        "google",
        "facebook",
    ]
    # A different Google identity with the same e-mail? The fake's subject derives from the e-mail, so
    # use another address: it is a *new* account, not a takeover.
    frag = oauth_sign_in(client, "google", "someone.else@gmail.com")
    assert complete(client, frag["oauth"]).json()["account_id"] != s["account_id"]


def test_link_and_unlink_from_the_account_screen(client):
    s = register(client)
    r = client.post("/api/v1/me/oauth/facebook/link", json={"return_to": "/"}, headers=headers(s))
    assert r.status_code == 200
    frag = oauth_sign_in(client, "facebook", "ann.fb@example.org", start_url=r.json()["url"])
    assert frag == {"oauth_linked": "facebook"}
    me = client.get("/api/v1/me", headers=headers(s)).json()
    assert me["identities"][0]["provider"] == "facebook" and me["identities"][0]["email"] == "ann.fb@example.org"
    # The same Facebook identity cannot be linked to a second account.
    other = register(client, email="bob@example.com")
    r = client.post("/api/v1/me/oauth/facebook/link", json={}, headers=headers(other))
    frag = oauth_sign_in(client, "facebook", "ann.fb@example.org", start_url=r.json()["url"])
    assert "already linked to another account" in frag["oauth_linked" if "oauth_linked" in frag else "oauth_error"]
    # Signing in with it now reaches ann's account.
    frag = oauth_sign_in(client, "facebook", "ann.fb@example.org")
    assert complete(client, frag["oauth"]).json()["account_id"] == s["account_id"]
    # Unlink.
    assert client.delete("/api/v1/me/oauth/google", headers=headers(s)).status_code == 404
    r = client.delete("/api/v1/me/oauth/facebook", headers=headers(s))
    assert r.status_code == 200 and r.json()["identities"] == []
    frag = oauth_sign_in(client, "facebook", "ann.fb@example.org")
    assert complete(client, frag["oauth"]).json()["account_id"] != s["account_id"]  # a fresh account now
    # Unlinking the only way in is refused until a passphrase exists.
    frag = oauth_sign_in(client, "google", "lonely@gmail.com")
    g = complete(client, frag["oauth"]).json()
    r = client.delete("/api/v1/me/oauth/google", headers=headers(g))
    assert r.status_code == 409 and "only way to sign in" in r.json()["detail"]
    set_passphrase(client, g, "lonely@gmail.com", "now i have one")
    assert client.delete("/api/v1/me/oauth/google", headers=headers(g)).status_code == 200
    assert set_passphrase(client, s, EMAIL, "x").status_code == 409  # password accounts already have one


def test_second_factor_still_applies_after_the_provider(client, monkeypatch):
    s = register(client)
    client.post("/api/v1/me/2fa/setup", json={"auth_key": auth_key_for(PASSWORD, EMAIL)}, headers=headers(s))
    with database.SessionLocal() as db:
        secret = db.query(models.Account).one().totp_pending_secret
    client.post("/api/v1/me/2fa/enable", json={"code": totp.code_at(secret)}, headers=headers(s))
    frag = oauth_sign_in(client, "google", EMAIL)
    r = complete(client, frag["oauth"])
    assert r.status_code == 202 and r.json()["two_factor_required"] and "token" not in r.json()
    monkeypatch.setattr(totp, "_now", lambda: time.time() + 30)
    r = client.post("/api/v1/auth/login/2fa", json={"challenge": r.json()["challenge"], "code": totp.code_at(secret)})
    assert r.status_code == 200 and r.json()["account_id"] == s["account_id"]


def test_admin_sees_sign_in_methods_and_purge_forgets_identities(client, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_EMAILS", {"admin@example.com"})
    admin = register(client, email="admin@example.com", password="Admin-pass-123")
    frag = oauth_sign_in(client, "google", "ann@gmail.com")
    s = complete(client, frag["oauth"]).json()
    detail = client.get(f"/api/v1/admin/accounts/{s['account_id']}", headers=headers(admin)).json()
    assert detail["has_password"] is False and detail["oauth_providers"] == ["google"]
    assert client.delete("/api/v1/me", headers=headers(s)).status_code == 204
    with database.SessionLocal() as db:
        assert db.query(models.OAuthIdentity).count() == 0
    frag = oauth_sign_in(client, "google", "ann@gmail.com")  # the identity starts over: a new account
    assert complete(client, frag["oauth"]).json()["account_id"] != s["account_id"]


def test_return_to_is_restricted_to_the_app(client, monkeypatch):
    class Req:
        base_url = "http://testserver/"

    monkeypatch.setattr(config, "WEB_APP_URL", "https://app.example.com")
    monkeypatch.setattr(config, "CORS_ORIGINS", ["https://console.example.com"])
    f = oauth.safe_return_to
    assert f(None, Req()) == "https://app.example.com"
    assert f("/settings", Req()) == "/settings"
    assert f("//evil.example/x", Req()) == "https://app.example.com"
    assert f("https://app.example.com/?x=1#frag", Req()) == "https://app.example.com/?x=1"
    assert f("https://console.example.com/", Req()) == "https://console.example.com/"
    assert f("http://testserver/", Req()) == "http://testserver/"
    assert f("https://evil.example/", Req()) == "https://app.example.com"
    assert f("http://localhost:4200/", Req()) == "http://localhost:4200/"  # dev
    monkeypatch.setattr(config, "IS_PROD", True)
    assert f("http://localhost:4200/", Req()) == "https://app.example.com"


# ---------------------------------------------------------------------------- the real adapters


class StubTransport:
    def __init__(self, answers):
        self.answers, self.calls = list(answers), []

    def request(self, method, url, *, headers=None, data=None):
        self.calls.append((method, url, headers or {}, data))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def test_google_adapter(monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setattr(config, "GOOGLE_CLIENT_SECRET", "sec")
    t = StubTransport(
        [
            (200, {"access_token": "ya29.x", "id_token": "jwt", "token_type": "Bearer"}),
            (200, {"sub": "1234567890", "email": "Ann@Gmail.com", "email_verified": True, "name": "Ann"}),
        ]
    )
    p = oauth.GoogleProvider(transport=t)
    assert p.configured() and not p.fake
    profile = p.fetch_profile("the-code", "https://api.example.com/cb", "verifier")
    assert profile == oauth.Profile(subject="1234567890", email="ann@gmail.com", email_verified=True, name="Ann")
    method, url, hdrs, data = t.calls[0]
    assert (method, url) == ("POST", "https://oauth2.googleapis.com/token")
    form = {k: v[0] for k, v in parse_qs(data.decode()).items()}
    assert form == {
        "code": "the-code",
        "client_id": "cid",
        "client_secret": "sec",
        "redirect_uri": "https://api.example.com/cb",
        "grant_type": "authorization_code",
        "code_verifier": "verifier",
    }
    assert t.calls[1][1] == "https://openidconnect.googleapis.com/v1/userinfo"
    assert t.calls[1][2]["Authorization"] == "Bearer ya29.x"
    with pytest.raises(oauth.OAuthError, match="refused the sign-in \\(Bad Request\\)"):
        oauth.GoogleProvider(
            transport=StubTransport([(400, {"error": "invalid_grant", "error_description": "Bad Request"})])
        ).fetch_profile("c", "r", "v")
    with pytest.raises(oauth.OAuthError, match="could not be reached"):
        oauth.GoogleProvider(transport=StubTransport([httpclient.TransportError("timed out")])).fetch_profile(
            "c", "r", "v"
        )


def test_facebook_adapter(monkeypatch):
    monkeypatch.setattr(config, "FACEBOOK_APP_ID", "app")
    monkeypatch.setattr(config, "FACEBOOK_APP_SECRET", "sec")
    t = StubTransport(
        [
            (200, {"access_token": "EAAB", "token_type": "bearer"}),
            (200, {"id": "10001", "name": "Ann", "email": "ann@example.com"}),
        ]
    )
    p = oauth.FacebookProvider(transport=t)
    profile = p.fetch_profile("the-code", "https://api.example.com/cb", "")
    assert profile == oauth.Profile(subject="10001", email="ann@example.com", email_verified=True, name="Ann")
    url = urlsplit(t.calls[0][1])
    assert url.netloc == "graph.facebook.com" and url.path == "/v19.0/oauth/access_token"
    q = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert q == {
        "client_id": "app",
        "client_secret": "sec",
        "redirect_uri": "https://api.example.com/cb",
        "code": "the-code",
    }
    url = urlsplit(t.calls[1][1])
    assert url.path == "/v19.0/me" and parse_qs(url.query)["fields"] == ["id,name,email"]
    # No e-mail from Facebook → the profile is unverified/empty and the flow refuses it.
    p = oauth.FacebookProvider(transport=StubTransport([(200, {"access_token": "x"}), (200, {"id": "1", "name": "N"})]))
    assert p.fetch_profile("c", "r", "").email_verified is False
    with pytest.raises(oauth.OAuthError, match="message from graph"):
        oauth.FacebookProvider(
            transport=StubTransport([(400, {"error": {"message": "message from graph", "code": 100}})])
        ).fetch_profile("c", "r", "")
    r_url = oauth.FacebookProvider().authorization_url("http://api", "http://api/cb", "st", "chal")
    assert r_url.startswith("https://www.facebook.com/v19.0/dialog/oauth?") and "code_challenge" not in r_url
    assert "scope=email%2Cpublic_profile" in r_url


def test_sealed_state_round_trip_and_expiry():
    blob = oauth.seal({"a": 1})
    assert oauth.unseal(blob) == {"a": 1, "exp": pytest.approx(time.time() + 600, abs=5)}
    assert oauth.unseal(blob[:-4] + "AAAA") is None and oauth.unseal("") is None and oauth.unseal(None) is None
    assert oauth.unseal(oauth.seal({"a": 1}, ttl=-1)) is None
    assert urlencode({"state": blob}) == f"state={blob}"  # URL-safe without escaping


# ---------------------------------------------------------------------------- Apple


def apple_sign_in(client, email="ann@icloud.com", *, private=False, cancel=False, start_url=None) -> dict[str, str]:
    """Apple posts the result to the callback (form_post): the fake answers with an auto-submitting
    form, which this helper "submits" like the browser would."""
    if start_url:
        consent = start_url
    else:
        r = client.get("/api/v1/auth/oauth/apple/start?return_to=/", follow_redirects=False)
        assert r.status_code == 302
        consent = r.headers["location"]
    q = {k: v[0] for k, v in parse_qs(urlsplit(consent).query).items()}
    form = {"redirect_uri": q["redirect_uri"], "state": q["state"], "email": email, "name": "Ann Apple"}
    if private:
        form["private"] = "on"
    if cancel:
        form["cancel"] = "1"
    r = client.post("/api/v1/auth/oauth/fake/apple/authorize", data=form)
    assert r.status_code == 200 and 'method=post action="http://testserver/api/v1/auth/oauth/apple/callback"' in r.text
    fields = dict(re.findall(r'name=(\w+) value="([^"]*)"', r.text.replace("&quot;", '"')))
    r = client.post("/api/v1/auth/oauth/apple/callback", data=fields, follow_redirects=False)
    assert r.status_code == 303, r.text
    return {k: v[0] for k, v in parse_qs(urlsplit(r.headers["location"]).fragment).items()}


def test_apple_round_trip_through_the_fake_including_hide_my_email(client):
    cat = {p["name"]: p for p in client.get("/api/v1/auth/oauth/providers").json()["providers"]}
    assert cat["apple"] == {"name": "apple", "label": "Apple", "available": True, "fake": True}
    frag = apple_sign_in(client, "ann@icloud.com")
    assert frag["provider"] == "apple"
    s = complete(client, frag["oauth"]).json()
    assert s["email"] == "ann@icloud.com" and s["has_password"] is False
    me = client.get("/api/v1/me", headers=headers(s)).json()
    assert [i["provider"] for i in me["identities"]] == ["apple"]
    # "Hide my email": a relay address — still verified, still the same Apple identity.
    frag = apple_sign_in(client, "ann@icloud.com", private=True)
    s2 = complete(client, frag["oauth"]).json()
    assert s2["account_id"] == s["account_id"]  # same subject → same account, whatever the address
    frag = apple_sign_in(client, "bob@icloud.com", private=True)
    s3 = complete(client, frag["oauth"]).json()
    assert s3["email"].endswith("@privaterelay.appleid.com") and s3["account_id"] != s["account_id"]
    # Cancel at Apple comes back as a message.
    assert "cancelled" in apple_sign_in(client, "ann@icloud.com", cancel=True)["oauth_error"]
    # Linking from the account screen works through the form post too.
    ann = register(client)
    r = client.post("/api/v1/me/oauth/apple/link", json={"return_to": "/"}, headers=headers(ann))
    assert apple_sign_in(client, "ann.link@icloud.com", start_url=r.json()["url"]) == {"oauth_linked": "apple"}
    # A verified Apple address joins an existing password account, like Google's.
    frag = apple_sign_in(client, EMAIL)
    assert complete(client, frag["oauth"]).json()["account_id"] == ann["account_id"]


def _ec_key_pem() -> str:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()


def _rsa_jwks_and_signer():
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding, rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    numbers = key.public_key().public_numbers()
    jwks = {
        "keys": [
            {
                "kty": "RSA",
                "kid": "k1",
                "use": "sig",
                "alg": "RS256",
                "n": oauth._b64url(numbers.n.to_bytes(256, "big")),
                "e": oauth._b64url(numbers.e.to_bytes(3, "big")),
            }
        ]
    }

    def sign(claims: dict, kid="k1", alg="RS256") -> str:
        head = oauth._b64url(json.dumps({"alg": alg, "kid": kid}).encode())
        body = oauth._b64url(json.dumps(claims).encode())
        sig = key.sign(f"{head}.{body}".encode(), padding.PKCS1v15(), hashes.SHA256())
        return f"{head}.{body}.{oauth._b64url(sig)}"

    return jwks, sign


def test_apple_client_secret_is_an_es256_jwt_for_the_team(monkeypatch):
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

    pem = _ec_key_pem()
    monkeypatch.setattr(config, "APPLE_CLIENT_ID", "com.example.assistant.web")
    monkeypatch.setattr(config, "APPLE_TEAM_ID", "TEAM123456")
    monkeypatch.setattr(config, "APPLE_KEY_ID", "KEY1234567")
    monkeypatch.setattr(config, "APPLE_PRIVATE_KEY", pem)
    token = oauth.apple_client_secret(now=1_700_000_000)
    head, body, sig = token.split(".")
    assert json.loads(oauth._unb64url(head)) == {"alg": "ES256", "kid": "KEY1234567", "typ": "JWT"}
    claims = json.loads(oauth._unb64url(body))
    assert claims == {
        "iss": "TEAM123456",
        "iat": 1_700_000_000,
        "exp": 1_700_000_600,
        "aud": "https://appleid.apple.com",
        "sub": "com.example.assistant.web",
    }
    raw = oauth._unb64url(sig)
    assert len(raw) == 64  # r ‖ s, as JWTs want it — not DER
    public = serialization.load_pem_private_key(pem.encode(), None).public_key()
    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    public.verify(der, f"{head}.{body}".encode(), ec.ECDSA(hashes.SHA256()))  # raises if wrong
    monkeypatch.setattr(config, "APPLE_PRIVATE_KEY", "not a key")
    with pytest.raises(oauth.OAuthError, match="misconfigured"):
        oauth.apple_client_secret()


def test_apple_id_token_verification_and_adapter(monkeypatch):
    jwks, sign = _rsa_jwks_and_signer()
    now = 1_700_000_000
    good = {
        "iss": "https://appleid.apple.com",
        "aud": "com.example.assistant.web",
        "sub": "001234.abcdef",
        "email": "Ann@icloud.com",
        "email_verified": "true",
        "exp": now + 300,
    }
    claims = oauth.verify_apple_id_token(sign(good), jwks, "com.example.assistant.web", now=now)
    assert claims["sub"] == "001234.abcdef"
    for bad, reason in (
        ({**good, "aud": "other.app"}, "not issued for this app"),
        ({**good, "iss": "https://evil.example"}, "not issued for this app"),
        ({**good, "exp": now - 1}, "expired"),
    ):
        with pytest.raises(oauth.OAuthError, match=reason):
            oauth.verify_apple_id_token(sign(bad), jwks, "com.example.assistant.web", now=now)
    with pytest.raises(oauth.OAuthError, match="unknown key"):
        oauth.verify_apple_id_token(sign(good, kid="nope"), jwks, "com.example.assistant.web", now=now)
    tampered = sign(good)[:-6] + "AAAAAA"
    with pytest.raises(oauth.OAuthError, match="failed verification"):
        oauth.verify_apple_id_token(tampered, jwks, "com.example.assistant.web", now=now)
    with pytest.raises(oauth.OAuthError, match="could not read"):
        oauth.verify_apple_id_token("garbage", jwks, "x")

    # The adapter: token exchange with a self-signed client secret, then the id_token's claims.
    monkeypatch.setattr(config, "APPLE_CLIENT_ID", "com.example.assistant.web")
    monkeypatch.setattr(config, "APPLE_TEAM_ID", "TEAM123456")
    monkeypatch.setattr(config, "APPLE_KEY_ID", "KEY1234567")
    monkeypatch.setattr(config, "APPLE_PRIVATE_KEY", _ec_key_pem())
    monkeypatch.setattr(time, "time", lambda: now)
    t = StubTransport([(200, {"access_token": "a", "id_token": sign(good)}), (200, jwks)])
    p = oauth.AppleProvider(transport=t)
    assert p.configured() and not p.fake
    profile = p.fetch_profile("the-code", "https://api.example.com/cb", "")
    assert profile == oauth.Profile(subject="001234.abcdef", email="ann@icloud.com", email_verified=True, name="")
    method, url, hdrs, data = t.calls[0]
    assert (method, url) == ("POST", "https://appleid.apple.com/auth/token")
    form = {k: v[0] for k, v in parse_qs(data.decode()).items()}
    assert form["client_id"] == "com.example.assistant.web" and form["grant_type"] == "authorization_code"
    assert form["client_secret"].count(".") == 2 and form["redirect_uri"] == "https://api.example.com/cb"
    assert t.calls[1][1] == "https://appleid.apple.com/auth/keys"
    # The published keys are cached; a second sign-in does not fetch them again.
    t.answers.append((200, {"access_token": "a", "id_token": sign(good)}))
    p.fetch_profile("c2", "https://api.example.com/cb", "")
    assert len(t.calls) == 3
    url = oauth.AppleProvider().authorization_url("http://api", "http://api/cb", "st", "chal")
    q = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
    assert (
        urlsplit(url).netloc == "appleid.apple.com" and q["response_mode"] == "form_post" and q["scope"] == "name email"
    )
    assert "code_challenge" not in q
