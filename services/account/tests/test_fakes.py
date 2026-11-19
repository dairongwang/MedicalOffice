"""The built-in fakes of Stripe and PayPal (fakes.py): the unchanged payment strategies, the hosted pages,
the signed webhooks delivered in-process — every billing state without leaving the machine."""

from __future__ import annotations

import secrets

import pytest

RETURN = "http://testserver/?billing=done"


@pytest.fixture
def buyer(client):
    """A fresh account (no membership yet) and its headers."""
    email = f"dummy-buyer-{secrets.token_hex(4)}@example.com"
    res = client.post(
        "/api/v1/auth/register",
        json={"email": email, "auth_key": secrets.token_hex(32), "wrapped_data_key": "dummy-wrapped-key-" + "x" * 8},
    )
    assert res.status_code == 201, res.text
    return {"Authorization": f"Bearer {res.json()['token']}"}


def checkout(client, headers, plan: str, method: str) -> str:
    res = client.post(
        "/api/v1/billing/checkout", headers=headers, json={"plan": plan, "method": method, "return_url": RETURN}
    )
    assert res.status_code == 200, res.text
    url = res.json()["checkout_url"]
    assert url.startswith(f"/api/v1/billing/fake/{method}/")  # a page this API serves itself
    return url


def ent(client, headers) -> dict:
    return client.get("/api/v1/me/entitlements", headers=headers).json()


def test_methods_are_offered_by_the_fakes(client):
    methods = {m["name"]: m for m in client.get("/api/v1/billing/methods").json()["methods"]}
    assert methods["stripe"]["fake"] and methods["stripe"]["available"] and methods["stripe"]["sandbox"]
    assert methods["paypal"]["fake"] and set(methods["paypal"]["plans"]) == {"pro_monthly", "pro_yearly", "lifetime"}


def test_stripe_subscription_renewal_failure_and_cancel_with_refund(client, buyer):
    url = checkout(client, buyer, "pro_monthly", "stripe")
    page = client.get(url)
    assert page.status_code == 200 and "Stripe Checkout" in page.text and "1.00 USD" in page.text
    res = client.post(url, data={"action": "pay"}, follow_redirects=False)
    assert res.status_code == 303 and res.headers["location"] == RETURN
    e = ent(client, buyer)
    assert e["plan"] == "pro" and e["status"] == "active" and e["provider"] == "stripe" and e["can_manage"]
    payments = client.get("/api/v1/billing/payments", headers=buyer).json()
    assert [(p["status"], p["amount"]) for p in payments] == [("succeeded", "1.00")]
    # The page is single-use.
    assert client.post(url, data={"action": "pay"}, follow_redirects=False).status_code == 404

    # The customer portal: a failed renewal → past due (grace), a renewal → active again.
    portal = client.post("/api/v1/billing/manage", headers=buyer, json={"return_url": "http://testserver/"}).json()
    assert portal["provider"] == "stripe" and portal["url"].startswith("/api/v1/billing/fake/stripe/portal/")
    page = client.get(portal["url"])
    assert page.status_code == 200 and "Billing portal" in page.text
    sub_id = page.text.split('name=sub value="', 1)[1].split('"', 1)[0]
    client.post(portal["url"], data={"sub": sub_id, "action": "fail"})
    assert ent(client, buyer)["status"] == "past_due"
    client.post(portal["url"], data={"sub": sub_id, "action": "renew"})
    assert ent(client, buyer)["status"] == "active"

    # Cancel: the unused share of the period goes back through the provider (charge.refunded webhook).
    preview = client.get("/api/v1/billing/cancel/preview", headers=buyer).json()
    assert preview["basis"] == "prorated" and preview["amount_cents"] > 0
    res = client.post("/api/v1/billing/cancel", headers=buyer)
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["refunded_via"] == "provider" and out["refund_id"].startswith("re_dummy_")
    assert out["entitlements"]["plan"] == "free"
    statuses = {p["status"] for p in client.get("/api/v1/billing/payments", headers=buyer).json()}
    assert statuses & {"refunded", "partially_refunded"}


def test_stripe_lifetime_and_a_declined_card(client, buyer):
    declined = checkout(client, buyer, "lifetime", "stripe")
    res = client.post(declined, data={"action": "decline"}, follow_redirects=False)
    assert res.status_code == 303 and "billing=canceled" in res.headers["location"]
    assert ent(client, buyer)["plan"] == "free"
    assert [p["status"] for p in client.get("/api/v1/billing/payments", headers=buyer).json()] == ["failed"]

    url = checkout(client, buyer, "lifetime", "stripe")
    client.post(url, data={"action": "pay"}, follow_redirects=False)
    e = ent(client, buyer)
    assert e["plan"] == "lifetime" and e["status"] == "active"
    out = client.post("/api/v1/billing/cancel", headers=buyer).json()
    assert out["refund"]["basis"] == "flat" and out["refunded_via"] == "provider"


def test_stripe_checkout_can_be_abandoned(client, buyer):
    url = checkout(client, buyer, "pro_yearly", "stripe")
    res = client.post(url, data={"action": "cancel"}, follow_redirects=False)
    assert res.status_code == 303 and res.headers["location"].endswith("billing=canceled")
    assert ent(client, buyer)["plan"] == "free"


def test_paypal_subscription_and_order(client, buyer):
    url = checkout(client, buyer, "pro_monthly", "paypal")
    page = client.get(url)
    assert page.status_code == 200 and "PayPal" in page.text
    res = client.post(url, data={"action": "approve"}, follow_redirects=False)
    assert (
        res.status_code == 303
        and res.headers["location"].startswith(RETURN)
        and "subscription_id=" in (res.headers["location"])
    )
    e = ent(client, buyer)
    assert e["plan"] == "pro" and e["status"] == "active" and e["provider"] == "paypal"
    assert [p["status"] for p in client.get("/api/v1/billing/payments", headers=buyer).json()] == ["succeeded"]
    manage = client.post("/api/v1/billing/manage", headers=buyer, json={"return_url": "http://testserver/"}).json()
    assert client.get(manage["url"]).status_code == 200
    out = client.post("/api/v1/billing/cancel", headers=buyer).json()
    assert out["refunded_via"] == "provider" and out["entitlements"]["plan"] == "free"

    # Lifetime is an order: approved on the page, captured from the webhook.
    url = checkout(client, buyer, "lifetime", "paypal")
    client.post(url, data={"action": "approve"}, follow_redirects=False)
    assert ent(client, buyer)["plan"] == "lifetime"


def test_webhooks_must_be_signed(client):
    res = client.post(
        "/api/v1/billing/webhooks/stripe",
        content=b'{"id": "evt_dummy", "type": "invoice.paid", "data": {"object": {}}}',
        headers={"Stripe-Signature": "t=1,v1=dummy"},
    )
    assert res.status_code == 400
    res = client.post("/api/v1/billing/webhooks/paypal", content=b'{"id": "WH-DUMMY", "event_type": "X"}')
    assert res.status_code == 400


def test_the_fake_pages_are_off_without_the_fakes(client, monkeypatch):
    import config

    assert client.get("/api/v1/billing/fake/events").status_code == 200
    monkeypatch.setattr(config, "BILLING_FAKE_PROVIDERS", False)
    assert client.get("/api/v1/billing/fake/events").status_code == 404
    assert client.get("/api/v1/billing/fake/stripe/checkout/cs_test_dummy").status_code == 404
