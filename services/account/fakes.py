"""Built-in fakes of Stripe and PayPal — what answers when a provider's credentials are missing.

``payments.StripePayment`` / ``payments.PayPalPayment`` run **unchanged** against these: the same REST
calls (through ``transport``, an in-process stand-in for ``payments.HttpTransport``), the same signed
webhooks (``Stripe-Signature`` HMAC; PayPal's verify-webhook-signature call) and the same hosted pages —
only the "provider" lives in this process and its pages are served by this API:

    GET/POST /api/v1/billing/fake/stripe/checkout/{session}     Stripe Checkout: pay · decline · cancel
    GET/POST /api/v1/billing/fake/stripe/portal/{session}       Customer Portal: cancel at period end,
                                                                simulate a renewal / a failed renewal
    GET/POST /api/v1/billing/fake/paypal/approve/{id}           PayPal approval page (subscription / order)
    GET/POST /api/v1/billing/fake/paypal/manage/{id}            PayPal "automatic payments" page
    GET      /api/v1/billing/fake/events                        the webhooks delivered so far (debugging)

When the buyer acts on a page, the fake delivers the webhooks the real provider would send — signed,
in-process, through ``billing.receive_webhook`` — then redirects like the provider does. Every billing
state (trialing, active, past_due, canceled, refunded) can be produced locally; nothing leaves the
machine and nothing is charged. Outside prod only (``config.BILLING_FAKE_PROVIDERS``); every route here
answers 404 otherwise, and a provider with real credentials never uses its fake.

The credentials below are obviously not real ("dummy") and are accepted nowhere else. State (checkout
sessions, subscriptions, charges) lives in memory: after an API restart the fake still accepts refunds
and cancellations of ids it does not know (the database is the source of truth).
"""

# ruff: noqa: E501  (embedded HTML of the fake provider pages)
from __future__ import annotations

import hashlib
import hmac
import html
import json
import logging
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

import config

log = logging.getLogger("account.fakes")

FAKE_PREFIX = "/api/v1/billing/fake"
PERIOD_DAYS = {"pro_monthly": 30, "pro_yearly": 365}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ts(dt: datetime | None = None) -> int:
    return int((dt or _now()).timestamp())


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _rid(prefix: str, n: int = 12) -> str:
    return f"{prefix}{secrets.token_hex(n)}"


def _esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _with_query(url: str, **params: str) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.update(params)
    return urlunsplit(parts._replace(query=urlencode(query)))


def _money(cents: int) -> str:
    return f"{cents / 100:.2f}"


def _safe_redirect(url: str) -> str:
    """Where a fake page may send the browser: http(s) or a path (what the app passed as return_url)."""
    if url.startswith(("http://", "https://", "/")) and not url.startswith("//"):
        return url
    return "/"


# ---------------------------------------------------------------------------- delivery log


@dataclass
class Delivery:
    provider: str
    type: str
    event_id: str
    at: str
    result: dict[str, Any] = field(default_factory=dict)


EVENTS: deque[Delivery] = deque(maxlen=100)


def _deliver(provider: str, body: bytes, headers: dict[str, str], event_type: str, event_id: str) -> dict[str, Any]:
    """Hand a signed webhook to the real receiving code (billing.receive_webhook), in-process, with a
    session of its own — exactly what the provider's HTTP delivery would do."""
    import billing
    import payments
    from database import SessionLocal

    method = payments.STRIPE if provider == "stripe" else payments.PAYPAL
    try:
        with SessionLocal() as db:
            result = billing.receive_webhook(db, method, body, headers)
            db.commit()
    except HTTPException as exc:
        result = {"ok": False, "status": exc.status_code, "detail": exc.detail}
        log.warning("fake %s webhook %s was refused: %s", provider, event_type, exc.detail)
    EVENTS.append(Delivery(provider=provider, type=event_type, event_id=event_id, at=_iso(_now()), result=result))
    log.info("fake %s webhook %s delivered: %s", provider, event_type, result)
    return result


# ---------------------------------------------------------------------------- Stripe


class FakeStripe:
    """Stripe's REST API (the calls payments.StripePayment makes) and its hosted pages."""

    # Test-mode style key ("sk_test_…": the strategy reports sandbox = True) — a dummy, never valid at Stripe.
    secret_key = "sk_test_dummy_fake_stripe_key_not_real"
    webhook_secret = "whsec_dummy_fake_stripe_webhook_secret"

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.sessions: dict[str, dict[str, Any]] = {}
        self.subscriptions: dict[str, dict[str, Any]] = {}
        self.charges: dict[str, dict[str, Any]] = {}  # payment intent id → {amount, refunded, …}
        self.portals: dict[str, dict[str, Any]] = {}
        self.transport = _StripeTransport(self)

    # ---- webhooks ------------------------------------------------------------------------

    def send(self, kind: str, obj: dict[str, Any]) -> dict[str, Any]:
        import payments

        event_id = _rid("evt_dummy_")
        event = {
            "id": event_id,
            "object": "event",
            "type": kind,
            "created": _ts(),
            "livemode": False,
            "data": {"object": obj},
        }
        body = json.dumps(event).encode()
        headers = {"Stripe-Signature": payments.StripePayment.sign(body, secret=self.webhook_secret)}
        return _deliver("stripe", body, headers, kind, event_id)

    # ---- objects -------------------------------------------------------------------------

    def _subscription_object(self, sub: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": sub["id"],
            "object": "subscription",
            "status": sub["status"],
            "customer": sub["customer"],
            "current_period_start": sub["period_start"],
            "current_period_end": sub["period_end"],
            "cancel_at_period_end": sub["cancel_at_period_end"],
            "ended_at": sub.get("ended_at"),
            "metadata": dict(sub["metadata"]),
            "items": {"data": [{"current_period_end": sub["period_end"], "price": {"unit_amount": sub["amount"]}}]},
        }

    def _invoice(self, sub: dict[str, Any], *, failed: bool) -> dict[str, Any]:
        pi = _rid("pi_dummy_")
        if not failed:
            self.charges[pi] = {
                "amount": sub["amount"],
                "refunded": 0,
                "currency": sub["currency"],
                "customer": sub["customer"],
                "metadata": dict(sub["metadata"]),
            }
        return {
            "id": _rid("in_dummy_"),
            "object": "invoice",
            "subscription": sub["id"],
            "customer": sub["customer"],
            "payment_intent": pi,
            "amount_paid": 0 if failed else sub["amount"],
            "amount_due": sub["amount"],
            "currency": sub["currency"],
            "created": _ts(),
            "status": "open" if failed else "paid",
            "status_transitions": {"paid_at": None if failed else _ts()},
            "lines": {"data": [{"period": {"start": sub["period_start"], "end": sub["period_end"]}}]},
            "subscription_details": {"metadata": dict(sub["metadata"])},
            "last_payment_error": {"message": "Your card was declined. (built-in fake)"} if failed else None,
        }

    # ---- what the hosted pages do --------------------------------------------------------

    def complete_checkout(self, session_id: str, *, decline: bool) -> str:
        """The buyer paid (or the card was declined) on the checkout page → webhooks → success URL."""
        with self.lock:
            s = self.sessions.get(session_id)
            if s is None or s["status"] != "open":
                raise HTTPException(status_code=404, detail="fake: this checkout session is no longer open")
            s["status"] = "complete"
            customer = s.get("customer") or _rid("cus_dummy_", 8)
            s["customer"] = customer
            meta = dict(s["metadata"])
            if s["mode"] == "payment":  # Lifetime: a one-off payment
                pi = _rid("pi_dummy_")
                if not decline:
                    self.charges[pi] = {
                        "amount": s["amount"],
                        "refunded": 0,
                        "currency": s["currency"],
                        "customer": customer,
                        "metadata": dict(s["payment_intent_metadata"] or meta),
                    }
                obj = {
                    "id": session_id,
                    "object": "checkout.session",
                    "mode": "payment",
                    "payment_status": "unpaid" if decline else "paid",
                    "payment_intent": pi,
                    "amount_total": s["amount"],
                    "currency": s["currency"],
                    "client_reference_id": s["client_reference_id"],
                    "customer": customer,
                    "metadata": meta,
                }
                self.send("checkout.session.async_payment_failed" if decline else "checkout.session.completed", obj)
                return s["cancel_url"] if decline else s["success_url"]

            # Pro: a subscription (possibly with the trial carried over).
            now = _now()
            sub_meta = dict(s["subscription_metadata"] or meta)
            plan = sub_meta.get("plan", "pro_monthly")
            trial_end = s.get("trial_end")
            period_end = trial_end or _ts(now + timedelta(days=PERIOD_DAYS.get(plan, 30)))
            sub = {
                "id": _rid("sub_dummy_"),
                "customer": customer,
                "status": "trialing" if trial_end else ("incomplete" if decline else "active"),
                "period_start": _ts(now),
                "period_end": period_end,
                "cancel_at_period_end": False,
                "metadata": sub_meta,
                "amount": s["amount"],
                "currency": s["currency"],
                "plan": plan,
            }
            self.subscriptions[sub["id"]] = sub
        self.send(
            "checkout.session.completed",
            {
                "id": session_id,
                "object": "checkout.session",
                "mode": "subscription",
                "subscription": sub["id"],
                "customer": customer,
                "client_reference_id": s["client_reference_id"],
                "metadata": meta,
            },
        )
        self.send("customer.subscription.created", self._subscription_object(sub))
        if trial_end:
            return s["success_url"]  # nothing is charged until the trial ends
        self.send("invoice.payment_failed" if decline else "invoice.paid", self._invoice(sub, failed=decline))
        return s["cancel_url"] if decline else s["success_url"]

    def renew(self, sub_id: str, *, fail: bool) -> None:
        """Simulate the next billing cycle (or its failure) — the portal's test buttons."""
        with self.lock:
            sub = self.subscriptions.get(sub_id)
            if sub is None or sub["status"] == "canceled":
                raise HTTPException(status_code=404, detail="fake: no such active subscription")
            if fail:
                sub["status"] = "past_due"
            else:
                start = max(_ts(), sub["period_start"])
                sub["period_start"] = start
                sub["period_end"] = start + PERIOD_DAYS.get(sub["plan"], 30) * 86400
                sub["status"] = "active"
            invoice = self._invoice(sub, failed=fail)
        self.send("invoice.payment_failed" if fail else "invoice.paid", invoice)
        self.send("customer.subscription.updated", self._subscription_object(sub))

    def set_cancel_at_period_end(self, sub_id: str, value: bool) -> None:
        with self.lock:
            sub = self.subscriptions.get(sub_id)
            if sub is None:
                raise HTTPException(status_code=404, detail="fake: no such subscription")
            sub["cancel_at_period_end"] = value
        self.send("customer.subscription.updated", self._subscription_object(sub))

    def cancel_now(self, sub_id: str) -> dict[str, Any]:
        with self.lock:
            sub = self.subscriptions.get(sub_id) or {
                "id": sub_id,
                "customer": None,
                "status": "active",
                "period_start": _ts(),
                "period_end": _ts(),
                "cancel_at_period_end": False,
                "metadata": {},
                "amount": 0,
                "currency": config.BILLING_CURRENCY.lower(),
                "plan": "pro_monthly",
            }
            sub["status"] = "canceled"
            sub["ended_at"] = _ts()
            self.subscriptions[sub_id] = sub
            obj = self._subscription_object(sub)
        self.send("customer.subscription.deleted", obj)
        return obj

    def refund(self, payment_id: str, cents: int) -> dict[str, Any]:
        with self.lock:
            charge = self.charges.setdefault(
                payment_id,
                {"amount": cents, "refunded": 0, "currency": config.BILLING_CURRENCY.lower(), "metadata": {}},
            )
            if charge["refunded"] + cents > charge["amount"]:
                return {"error": {"message": "Refund amount is greater than the unrefunded amount of the charge."}}
            charge["refunded"] += cents
            refund_id = _rid("re_dummy_")
            obj = {
                "id": _rid("ch_dummy_"),
                "object": "charge",
                "payment_intent": payment_id if payment_id.startswith("pi_") else None,
                "amount": charge["amount"],
                "amount_refunded": charge["refunded"],
                "currency": charge["currency"],
                "customer": charge.get("customer"),
                "metadata": dict(charge.get("metadata") or {}),
                "refunds": {"data": [{"id": refund_id, "amount": cents}]},
            }
        self.send("charge.refunded", obj)
        return {"id": refund_id, "object": "refund", "amount": cents, "status": "succeeded"}


class _StripeTransport:
    """payments.HttpTransport's shape: ``request(method, url, headers=, data=) → (status, json)``."""

    def __init__(self, fake: FakeStripe) -> None:
        self.fake = fake

    def request(self, method: str, url: str, *, headers=None, data: bytes | None = None) -> tuple[int, dict[str, Any]]:
        headers = {k.lower(): v for k, v in (headers or {}).items()}
        if headers.get("authorization") != f"Bearer {self.fake.secret_key}":
            return 401, {"error": {"message": "Invalid API Key provided (built-in fake)."}}
        path = urlsplit(url).path
        fields = dict(parse_qsl((data or b"").decode(), keep_blank_values=True))
        f = self.fake
        if method == "POST" and path == "/v1/checkout/sessions":
            session_id = _rid("cs_test_dummy_")
            amount = int(fields.get("line_items[0][price_data][unit_amount]") or 0)
            trial_end = fields.get("subscription_data[trial_end]")
            session = {
                "id": session_id,
                "mode": fields.get("mode", "payment"),
                "status": "open",
                "amount": amount,
                "currency": fields.get("line_items[0][price_data][currency]", "usd"),
                "product": fields.get("line_items[0][price_data][product_data][name]", ""),
                "interval": fields.get("line_items[0][price_data][recurring][interval]"),
                "success_url": _safe_redirect(fields.get("success_url", "/")),
                "cancel_url": _safe_redirect(fields.get("cancel_url", "/")),
                "client_reference_id": fields.get("client_reference_id"),
                "customer_email": fields.get("customer_email"),
                "metadata": _nested(fields, "metadata"),
                "subscription_metadata": _nested(fields, "subscription_data[metadata]"),
                "payment_intent_metadata": _nested(fields, "payment_intent_data[metadata]"),
                "trial_end": int(trial_end) if trial_end and trial_end.isdigit() else None,
                "created": _ts(),
            }
            with f.lock:
                f.sessions[session_id] = session
            return 200, {
                "id": session_id,
                "object": "checkout.session",
                "url": f"{FAKE_PREFIX}/stripe/checkout/{session_id}",
            }
        if method == "DELETE" and path.startswith("/v1/subscriptions/"):
            return 200, f.cancel_now(path.rsplit("/", 1)[-1])
        if method == "POST" and path == "/v1/refunds":
            payment_id = fields.get("payment_intent") or fields.get("charge") or ""
            out = f.refund(payment_id, int(fields.get("amount") or 0))
            return (400 if "error" in out else 200), out
        if method == "POST" and path == "/v1/billing_portal/sessions":
            portal_id = _rid("bps_dummy_")
            with f.lock:
                f.portals[portal_id] = {
                    "customer": fields.get("customer"),
                    "return_url": _safe_redirect(fields.get("return_url", "/")),
                }
            return 200, {
                "id": portal_id,
                "object": "billing_portal.session",
                "url": f"{FAKE_PREFIX}/stripe/portal/{portal_id}",
            }
        return 404, {"error": {"message": f"Unrecognized request URL ({method} {path}) — built-in fake."}}


def _nested(fields: dict[str, str], prefix: str) -> dict[str, str]:
    """``metadata[account_id]=…`` → {"account_id": …} for the given prefix."""
    out = {}
    for key, value in fields.items():
        if key.startswith(prefix + "[") and key.endswith("]"):
            inner = key[len(prefix) + 1 : -1]
            if "[" not in inner:
                out[inner] = value
    return out


# ---------------------------------------------------------------------------- PayPal


class FakePayPal:
    """PayPal's REST API (OAuth, Orders v2, Subscriptions, refunds, webhook verification) and its pages."""

    client_id = "dummy-fake-paypal-client-id"
    client_secret = "dummy-fake-paypal-client-secret"
    webhook_id = "DUMMY-FAKE-WEBHOOK-ID"
    plan_ids = {"pro_monthly": "P-DUMMYFAKEPROMONTHLY", "pro_yearly": "P-DUMMYFAKEPROYEARLY"}
    access_token = "A21_dummy_fake_paypal_access_token"

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.orders: dict[str, dict[str, Any]] = {}
        self.subscriptions: dict[str, dict[str, Any]] = {}
        self.captures: dict[str, dict[str, Any]] = {}  # capture / sale id → {amount cents, refunded, custom_id, kind}
        self.transport = _PayPalTransport(self)

    # ---- webhooks ------------------------------------------------------------------------

    def signature(self, transmission_id: str, transmission_time: str, event: dict[str, Any]) -> str:
        digest = hashlib.sha256(json.dumps(event, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        message = f"{transmission_id}|{transmission_time}|{self.webhook_id}|{digest}".encode()
        return hmac.new(self.client_secret.encode(), message, hashlib.sha256).hexdigest()

    def send(self, kind: str, resource: dict[str, Any]) -> dict[str, Any]:
        event_id = _rid("WH-DUMMY-", 8).upper()
        event = {
            "id": event_id,
            "event_type": kind,
            "create_time": _iso(_now()),
            "resource_type": kind.split(".")[1].lower() if "." in kind else "",
            "resource": resource,
        }
        body = json.dumps(event).encode()
        transmission_id, transmission_time = secrets.token_hex(8), _iso(_now())
        headers = {
            "PAYPAL-AUTH-ALGO": "SHA256withRSA",
            "PAYPAL-CERT-URL": "https://api-m.sandbox.paypal.com/v1/notifications/certs/CERT-DUMMY-FAKE",
            "PAYPAL-TRANSMISSION-ID": transmission_id,
            "PAYPAL-TRANSMISSION-SIG": self.signature(transmission_id, transmission_time, event),
            "PAYPAL-TRANSMISSION-TIME": transmission_time,
        }
        return _deliver("paypal", body, headers, kind, event_id)

    # ---- objects -------------------------------------------------------------------------

    def _subscription_resource(self, sub: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": sub["id"],
            "status": sub["status"],
            "plan_id": sub["plan_id"],
            "custom_id": sub["custom_id"],
            "start_time": sub["start_time"],
            "subscriber": {"email_address": sub.get("email"), "payer_id": sub["payer_id"]},
            "billing_info": {
                "next_billing_time": sub.get("next_billing_time"),
                "outstanding_balance": {"currency_code": sub["currency"], "value": _money(sub.get("outstanding", 0))},
            },
            "links": [{"rel": "approve", "href": f"{FAKE_PREFIX}/paypal/approve/{sub['id']}"}],
        }

    def manage_url(self, subscription_id: str, return_url: str) -> str:
        return f"{FAKE_PREFIX}/paypal/manage/{subscription_id}?{urlencode({'return_url': return_url})}"

    def _plan_of(self, plan_id: str) -> str:
        return next((k for k, v in self.plan_ids.items() if v == plan_id), "pro_monthly")

    # ---- what the pages do ---------------------------------------------------------------

    def approve(self, resource_id: str, *, decline: bool) -> str:
        import payments

        with self.lock:
            order = self.orders.get(resource_id)
            sub = self.subscriptions.get(resource_id)
        if order is not None:
            if order["status"] != "CREATED":
                raise HTTPException(status_code=404, detail="fake: this order is no longer open")
            if decline:
                order["status"] = "VOIDED"
                return order["cancel_url"]
            order["status"] = "APPROVED"
            order["payer_id"] = order.get("payer_id") or _rid("DUMMYPAYER", 4).upper()
            # PayPal tells us the buyer approved; the strategy captures the order from the webhook.
            self.send(
                "CHECKOUT.ORDER.APPROVED",
                {
                    "id": order["id"],
                    "status": "APPROVED",
                    "intent": "CAPTURE",
                    "purchase_units": order["purchase_units"],
                    "payer": {"payer_id": order["payer_id"]},
                },
            )
            return _with_query(order["return_url"], token=order["id"], PayerID=order["payer_id"])
        if sub is None or sub["status"] != "APPROVAL_PENDING":
            raise HTTPException(status_code=404, detail="fake: this subscription is not waiting for approval")
        if decline:
            sub["status"] = "CANCELLED"
            return sub["cancel_url"]
        plan = self._plan_of(sub["plan_id"])
        now = _now()
        start = datetime.fromisoformat(sub["start_time"].replace("Z", "+00:00")) if sub.get("start_time") else now
        deferred = start > now + timedelta(minutes=5)  # the trial carried over: the first charge is later
        with self.lock:
            sub["status"] = "ACTIVE"
            sub["payer_id"] = _rid("DUMMYPAYER", 4).upper()
            sub["next_billing_time"] = _iso(start if deferred else now + timedelta(days=PERIOD_DAYS.get(plan, 30)))
        self.send("BILLING.SUBSCRIPTION.ACTIVATED", self._subscription_resource(sub))
        if not deferred:
            self.sale(sub["id"], payments.plan_price_cents(plan))
        return _with_query(sub["return_url"], subscription_id=sub["id"], ba_token=_rid("BA-DUMMY", 6).upper())

    def sale(self, sub_id: str, cents: int) -> None:
        with self.lock:
            sub = self.subscriptions[sub_id]
            sale_id = _rid("DUMMYSALE", 6).upper()
            self.captures[sale_id] = {
                "amount": cents,
                "refunded": 0,
                "currency": sub["currency"],
                "custom_id": sub["custom_id"],
                "kind": "sale",
            }
        self.send(
            "PAYMENT.SALE.COMPLETED",
            {
                "id": sale_id,
                "state": "completed",
                "billing_agreement_id": sub_id,
                "amount": {"total": _money(cents), "currency": sub["currency"]},
                "create_time": _iso(_now()),
            },
        )

    def renew(self, sub_id: str, *, fail: bool) -> None:
        import payments

        with self.lock:
            sub = self.subscriptions.get(sub_id)
            if sub is None or sub["status"] not in ("ACTIVE", "SUSPENDED"):
                raise HTTPException(status_code=404, detail="fake: no such active subscription")
            plan = self._plan_of(sub["plan_id"])
            cents = payments.plan_price_cents(plan)
            if fail:
                sub["status"] = "SUSPENDED"
                sub["outstanding"] = cents
            else:
                sub["status"] = "ACTIVE"
                sub["outstanding"] = 0
                sub["next_billing_time"] = _iso(_now() + timedelta(days=PERIOD_DAYS.get(plan, 30)))
        if fail:
            self.send("BILLING.SUBSCRIPTION.PAYMENT.FAILED", self._subscription_resource(sub))
            self.send("BILLING.SUBSCRIPTION.SUSPENDED", self._subscription_resource(sub))
        else:
            self.send("BILLING.SUBSCRIPTION.RE-ACTIVATED", self._subscription_resource(sub))
            self.sale(sub_id, cents)

    def cancel(self, sub_id: str) -> None:
        with self.lock:
            sub = self.subscriptions.get(sub_id)
            if sub is None:  # unknown after a restart: the database knows the account
                sub = {
                    "id": sub_id,
                    "status": "ACTIVE",
                    "plan_id": "",
                    "custom_id": "",
                    "start_time": _iso(_now()),
                    "payer_id": None,
                    "currency": config.BILLING_CURRENCY,
                }
                self.subscriptions[sub_id] = sub
            if sub["status"] == "CANCELLED":
                return
            sub["status"] = "CANCELLED"
        self.send("BILLING.SUBSCRIPTION.CANCELLED", self._subscription_resource(sub))

    def capture(self, order_id: str) -> tuple[int, dict[str, Any]]:
        with self.lock:
            order = self.orders.get(order_id)
            if order is None:
                return 404, {"name": "RESOURCE_NOT_FOUND", "message": "The specified resource does not exist."}
            if order["status"] == "COMPLETED":
                return 422, {
                    "name": "UNPROCESSABLE_ENTITY",
                    "details": [{"issue": "ORDER_ALREADY_CAPTURED"}],
                    "message": "Order already captured.",
                }
            if order["status"] != "APPROVED":
                return 422, {"name": "UNPROCESSABLE_ENTITY", "details": [{"issue": "ORDER_NOT_APPROVED"}]}
            order["status"] = "COMPLETED"
            unit = order["purchase_units"][0]
            capture_id = _rid("DUMMYCAPTURE", 5).upper()
            cents = int(round(float(unit["amount"]["value"]) * 100))
            self.captures[capture_id] = {
                "amount": cents,
                "refunded": 0,
                "currency": unit["amount"]["currency_code"],
                "custom_id": unit.get("custom_id"),
                "kind": "capture",
            }
            order["capture_id"] = capture_id
            return 201, {
                "id": order_id,
                "status": "COMPLETED",
                "payer": {"payer_id": order.get("payer_id")},
                "purchase_units": [
                    {
                        **unit,
                        "payments": {
                            "captures": [
                                {
                                    "id": capture_id,
                                    "status": "COMPLETED",
                                    "amount": unit["amount"],
                                    "custom_id": unit.get("custom_id"),
                                    "create_time": _iso(_now()),
                                }
                            ]
                        },
                    }
                ],
            }

    def refund(self, capture_id: str, cents: int, currency: str) -> tuple[int, dict[str, Any]]:
        with self.lock:
            cap = self.captures.setdefault(
                capture_id, {"amount": cents, "refunded": 0, "currency": currency, "custom_id": None, "kind": "capture"}
            )
            if cap["refunded"] + cents > cap["amount"]:
                return 422, {
                    "name": "UNPROCESSABLE_ENTITY",
                    "message": "The refund amount exceeds the captured amount.",
                }
            cap["refunded"] += cents
            refund_id = _rid("DUMMYREFUND", 5).upper()
        kind = "PAYMENT.SALE.REFUNDED" if cap["kind"] == "sale" else "PAYMENT.CAPTURE.REFUNDED"
        self.send(
            kind,
            {
                "id": refund_id,
                "status": "COMPLETED",
                "amount": {"value": _money(cents), "currency_code": currency},
                "custom_id": cap.get("custom_id"),
                "links": [{"rel": "up", "href": f"https://api-m.sandbox.paypal.com/v2/payments/captures/{capture_id}"}],
            },
        )
        return 201, {"id": refund_id, "status": "COMPLETED"}


class _PayPalTransport:
    def __init__(self, fake: FakePayPal) -> None:
        self.fake = fake

    def request(self, method: str, url: str, *, headers=None, data: bytes | None = None) -> tuple[int, dict[str, Any]]:
        import base64

        f = self.fake
        headers = {k.lower(): v for k, v in (headers or {}).items()}
        path = urlsplit(url).path
        if method == "POST" and path == "/v1/oauth2/token":
            expected = "Basic " + base64.b64encode(f"{f.client_id}:{f.client_secret}".encode()).decode()
            if headers.get("authorization") != expected:
                return 401, {
                    "error": "invalid_client",
                    "error_description": "Client Authentication failed (built-in fake)",
                }
            return 200, {"access_token": f.access_token, "token_type": "Bearer", "expires_in": 32400}
        if headers.get("authorization") != f"Bearer {f.access_token}":
            return 401, {"name": "AUTHENTICATION_FAILURE", "message": "Authentication failed (built-in fake)."}
        try:
            payload = json.loads(data or b"{}")
        except ValueError:
            return 400, {"name": "INVALID_REQUEST", "message": "Request is not well-formed."}

        if method == "POST" and path == "/v2/checkout/orders":
            order_id = _rid("DUMMYORDER", 6).upper()
            context = payload.get("application_context") or {}
            with f.lock:
                f.orders[order_id] = {
                    "id": order_id,
                    "status": "CREATED",
                    "purchase_units": payload.get("purchase_units") or [],
                    "return_url": _safe_redirect(context.get("return_url", "/")),
                    "cancel_url": _safe_redirect(context.get("cancel_url", "/")),
                }
            return 201, {
                "id": order_id,
                "status": "CREATED",
                "links": [{"rel": "approve", "href": f"{FAKE_PREFIX}/paypal/approve/{order_id}"}],
            }
        if method == "POST" and path.startswith("/v2/checkout/orders/") and path.endswith("/capture"):
            return f.capture(path.split("/")[4])
        if method == "POST" and path == "/v1/billing/subscriptions":
            if payload.get("plan_id") not in f.plan_ids.values():
                return 422, {"name": "UNPROCESSABLE_ENTITY", "message": "The plan does not exist (built-in fake)."}
            sub_id = _rid("I-DUMMY", 5).upper()
            context = payload.get("application_context") or {}
            sub = {
                "id": sub_id,
                "status": "APPROVAL_PENDING",
                "plan_id": payload["plan_id"],
                "custom_id": payload.get("custom_id", ""),
                "email": (payload.get("subscriber") or {}).get("email_address"),
                "start_time": payload.get("start_time") or _iso(_now()),
                "payer_id": None,
                "currency": config.BILLING_CURRENCY,
                "return_url": _safe_redirect(context.get("return_url", "/")),
                "cancel_url": _safe_redirect(context.get("cancel_url", "/")),
            }
            with f.lock:
                f.subscriptions[sub_id] = sub
            return 201, f._subscription_resource(sub)
        if method == "GET" and path.startswith("/v1/billing/subscriptions/"):
            sub = f.subscriptions.get(path.rsplit("/", 1)[-1])
            if sub is None:
                return 404, {"name": "RESOURCE_NOT_FOUND", "message": "The specified resource does not exist."}
            return 200, f._subscription_resource(sub)
        if method == "POST" and path.startswith("/v1/billing/subscriptions/") and path.endswith("/cancel"):
            f.cancel(path.split("/")[4])
            return 204, {}
        if method == "POST" and path.startswith("/v2/payments/captures/") and path.endswith("/refund"):
            amount = payload.get("amount") or {}
            cents = int(round(float(amount.get("value") or 0) * 100))
            return f.refund(path.split("/")[4], cents, amount.get("currency_code") or config.BILLING_CURRENCY)
        if method == "POST" and path == "/v1/notifications/verify-webhook-signature":
            event = payload.get("webhook_event") or {}
            ok = payload.get("webhook_id") == f.webhook_id and hmac.compare_digest(
                str(payload.get("transmission_sig") or ""),
                f.signature(str(payload.get("transmission_id")), str(payload.get("transmission_time")), event),
            )
            return 200, {"verification_status": "SUCCESS" if ok else "FAILURE"}
        return 404, {"name": "RESOURCE_NOT_FOUND", "message": f"{method} {path} is not part of the built-in fake."}


STRIPE = FakeStripe()
PAYPAL = FakePayPal()


# ---------------------------------------------------------------------------- the hosted pages

router = APIRouter(prefix="/billing/fake", include_in_schema=False)


def _guard(provider: str) -> None:
    """404 unless this provider's fake is the one in use (never in prod, never with real credentials)."""
    import payments

    if config.IS_PROD or not config.BILLING_FAKE_PROVIDERS:
        raise HTTPException(status_code=404)
    method = payments.STRIPE if provider == "stripe" else payments.PAYPAL
    if method.fake is None:
        raise HTTPException(status_code=404)


async def _form(request: Request) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs((await request.body()).decode(), keep_blank_values=True).items()}


def _page(title: str, brand: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        f"""<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>{_esc(title)} (built-in fake)</title>
<style>
body{{font-family:system-ui,sans-serif;max-width:460px;margin:40px auto;padding:0 16px;line-height:1.5;color:#111827}}
.note{{background:#fff3cd;border:1px solid #ffe69c;padding:8px 12px;border-radius:8px;font-size:14px}}
.box{{border:1px solid #e2e5ea;border-radius:10px;padding:12px 14px;margin:14px 0}}
.amount{{font-size:1.6rem;font-weight:700}} .muted{{color:#6b7280;font-size:.9rem}}
dl{{display:grid;grid-template-columns:auto 1fr;gap:4px 12px;margin:0}} dt{{color:#6b7280}}
input{{width:100%;box-sizing:border-box;padding:10px 12px;font-size:16px;border:1px solid #e2e5ea;border-radius:8px;margin-top:4px}}
button{{margin-top:12px;padding:12px 18px;font-size:16px;border-radius:8px;border:0;cursor:pointer;min-height:44px;margin-right:8px}}
.go{{background:{brand};color:#fff}} .alt{{background:#fff;border:1px solid #999}} .bad{{background:#fff;border:1px solid #b91c1c;color:#b91c1c}}
</style>
{body}</html>"""
    )


# ---- Stripe Checkout


@router.get("/stripe/checkout/{session_id}", response_class=HTMLResponse)
def stripe_checkout_page(session_id: str):
    _guard("stripe")
    s = STRIPE.sessions.get(session_id)
    if s is None or s["status"] != "open":
        raise HTTPException(status_code=404, detail="fake: unknown or finished checkout session")
    every = f" / {s['interval']}" if s.get("interval") else " once"
    trial = (
        f"<p class=muted>Free until {datetime.fromtimestamp(s['trial_end'], tz=timezone.utc):%Y-%m-%d} (your trial carries over) — "
        "then charged every period.</p>"
        if s.get("trial_end")
        else ""
    )
    return _page(
        "Stripe Checkout",
        "#635bff",
        f"""<h2>Stripe Checkout</h2>
<p class=note><strong>Built-in fake.</strong> No Stripe keys are configured on this server, so this page stands in for
Stripe's hosted checkout. Nothing is charged and no card is asked for. "Pay" sends the same signed webhooks
Stripe would; "Decline" produces a failed payment.</p>
<div class=box><div class=muted>{_esc(s["product"])}</div>
<div class=amount id=fake-amount>{_esc(_money(s["amount"]))} {_esc(s["currency"].upper())}<span class=muted>{_esc(every)}</span></div>
{trial}<div class=muted>{_esc(s.get("customer_email") or "")}</div></div>
<form method=post action="{FAKE_PREFIX}/stripe/checkout/{_esc(session_id)}">
<button class=go id=fake-pay name=action value=pay type=submit>Pay</button>
<button class=bad id=fake-decline name=action value=decline type=submit>Decline (test a failed payment)</button>
<button class=alt id=fake-cancel name=action value=cancel type=submit>Cancel</button>
</form>""",
    )


@router.post("/stripe/checkout/{session_id}")
async def stripe_checkout_submit(session_id: str, request: Request):
    _guard("stripe")
    action = (await _form(request)).get("action", "pay")
    s = STRIPE.sessions.get(session_id)
    if s is None:
        raise HTTPException(status_code=404, detail="fake: unknown checkout session")
    if action == "cancel":
        s["status"] = "expired"
        return RedirectResponse(s["cancel_url"], status_code=303)
    target = STRIPE.complete_checkout(session_id, decline=action == "decline")
    return RedirectResponse(target, status_code=303)


# ---- Stripe Customer Portal


@router.get("/stripe/portal/{portal_id}", response_class=HTMLResponse)
def stripe_portal_page(portal_id: str):
    _guard("stripe")
    portal = STRIPE.portals.get(portal_id)
    if portal is None:
        raise HTTPException(status_code=404, detail="fake: unknown portal session")
    subs = [s for s in STRIPE.subscriptions.values() if s["customer"] == portal["customer"]]
    rows = "".join(
        f"""<div class=box id="fake-sub-{_esc(s["id"])}"><dl>
<dt>Subscription</dt><dd>{_esc(s["id"])}</dd><dt>Status</dt><dd>{_esc(s["status"])}{
            " · cancels at period end" if s["cancel_at_period_end"] else ""
        }</dd>
<dt>Amount</dt><dd>{_esc(_money(s["amount"]))} {_esc(s["currency"].upper())}</dd>
<dt>Period ends</dt><dd>{datetime.fromtimestamp(s["period_end"], tz=timezone.utc):%Y-%m-%d}</dd></dl>
{
            ""
            if s["status"] == "canceled"
            else f'''<form method=post action="{FAKE_PREFIX}/stripe/portal/{_esc(portal_id)}"><input type=hidden name=sub value="{_esc(s["id"])}">
<button class=alt name=action value={"resume" if s["cancel_at_period_end"] else "cancel_at_period_end"} type=submit id=fake-toggle-cancel>{"Resume renewal" if s["cancel_at_period_end"] else "Cancel at period end"}</button>
<button class=go name=action value=renew type=submit id=fake-renew>Simulate renewal</button>
<button class=bad name=action value=fail type=submit id=fake-fail>Simulate failed renewal</button></form>'''
        }</div>"""
        for s in subs
    )
    return _page(
        "Stripe billing portal",
        "#635bff",
        f"""<h2>Billing portal</h2>
<p class=note><strong>Built-in fake.</strong> Stands in for Stripe's Customer Portal. The test buttons produce the
webhooks a renewal / a failed renewal would (e.g. to see the "past due" state).</p>
{rows or "<p class=muted>No subscriptions for this customer (the fake forgets them when the API restarts).</p>"}
<p><a id=fake-return href="{_esc(portal["return_url"])}">← Return to the app</a></p>""",
    )


@router.post("/stripe/portal/{portal_id}")
async def stripe_portal_submit(portal_id: str, request: Request):
    _guard("stripe")
    portal = STRIPE.portals.get(portal_id)
    if portal is None:
        raise HTTPException(status_code=404, detail="fake: unknown portal session")
    form = await _form(request)
    sub_id, action = form.get("sub", ""), form.get("action", "")
    sub = STRIPE.subscriptions.get(sub_id)
    if sub is None or sub["customer"] != portal["customer"]:
        raise HTTPException(status_code=404, detail="fake: no such subscription for this customer")
    if action == "renew":
        STRIPE.renew(sub_id, fail=False)
    elif action == "fail":
        STRIPE.renew(sub_id, fail=True)
    elif action in ("cancel_at_period_end", "resume"):
        STRIPE.set_cancel_at_period_end(sub_id, action == "cancel_at_period_end")
    return RedirectResponse(f"{FAKE_PREFIX}/stripe/portal/{portal_id}", status_code=303)


# ---- PayPal approval + automatic payments


@router.get("/paypal/approve/{resource_id}", response_class=HTMLResponse)
def paypal_approve_page(resource_id: str):
    _guard("paypal")
    order = PAYPAL.orders.get(resource_id)
    sub = PAYPAL.subscriptions.get(resource_id)
    if order is not None and order["status"] == "CREATED":
        unit = (order["purchase_units"] or [{}])[0]
        what = _esc(unit.get("description") or "Order")
        amount = f"{_esc(unit.get('amount', {}).get('value'))} {_esc(unit.get('amount', {}).get('currency_code'))}"
        every = " once"
    elif sub is not None and sub["status"] == "APPROVAL_PENDING":
        import payments

        plan = PAYPAL._plan_of(sub["plan_id"])
        what = _esc(payments.PLAN_TITLES.get(plan, plan))
        amount = f"{_esc(_money(payments.plan_price_cents(plan)))} {_esc(sub['currency'])}"
        every = " / month" if plan == "pro_monthly" else " / year"
    else:
        raise HTTPException(status_code=404, detail="fake: nothing to approve here")
    return _page(
        "PayPal",
        "#0070ba",
        f"""<h2>PayPal</h2>
<p class=note><strong>Built-in fake.</strong> No PayPal credentials are configured on this server, so this page stands
in for PayPal's approval screen. Nothing is charged; approving sends the webhooks PayPal would.</p>
<div class=box><div class=muted>{what}</div><div class=amount id=fake-amount>{amount}<span class=muted>{every}</span></div>
<div class=muted>Paying as dummy-buyer@example.com (sandbox)</div></div>
<form method=post action="{FAKE_PREFIX}/paypal/approve/{_esc(resource_id)}">
<button class=go id=fake-approve name=action value=approve type=submit>Agree &amp; pay</button>
<button class=alt id=fake-cancel name=action value=cancel type=submit>Cancel and return</button>
</form>""",
    )


@router.post("/paypal/approve/{resource_id}")
async def paypal_approve_submit(resource_id: str, request: Request):
    _guard("paypal")
    action = (await _form(request)).get("action", "approve")
    target = PAYPAL.approve(resource_id, decline=action != "approve")
    return RedirectResponse(target, status_code=303)


@router.get("/paypal/manage/{subscription_id}", response_class=HTMLResponse)
def paypal_manage_page(subscription_id: str, return_url: str = "/"):
    _guard("paypal")
    sub = PAYPAL.subscriptions.get(subscription_id)
    back = _safe_redirect(return_url)
    if sub is None:
        info = "<p class=muted>The fake does not know this subscription (it forgets them when the API restarts).</p>"
    else:
        live = sub["status"] in ("ACTIVE", "SUSPENDED")
        info = f"""<div class=box><dl><dt>Subscription</dt><dd>{_esc(sub["id"])}</dd><dt>Status</dt><dd>{
            _esc(sub["status"])
        }</dd>
<dt>Next payment</dt><dd>{_esc(sub.get("next_billing_time") or "—")}</dd></dl>
{
            ""
            if not live
            else f'''<form method=post action="{FAKE_PREFIX}/paypal/manage/{_esc(subscription_id)}"><input type=hidden name=return_url value="{_esc(back)}">
<button class=go name=action value=renew type=submit id=fake-renew>Simulate next payment</button>
<button class=bad name=action value=fail type=submit id=fake-fail>Simulate failed payment</button>
<button class=alt name=action value=cancel type=submit id=fake-cancel-sub>Cancel automatic payments</button></form>'''
        }</div>"""
    return _page(
        "PayPal automatic payments",
        "#0070ba",
        f"""<h2>Automatic payments</h2>
<p class=note><strong>Built-in fake.</strong> Stands in for PayPal's "automatic payments" page.</p>
{info}<p><a id=fake-return href="{_esc(back)}">← Return to the app</a></p>""",
    )


@router.post("/paypal/manage/{subscription_id}")
async def paypal_manage_submit(subscription_id: str, request: Request):
    _guard("paypal")
    form = await _form(request)
    action = form.get("action", "")
    if action == "renew":
        PAYPAL.renew(subscription_id, fail=False)
    elif action == "fail":
        PAYPAL.renew(subscription_id, fail=True)
    elif action == "cancel":
        PAYPAL.cancel(subscription_id)
    back = urlencode({"return_url": _safe_redirect(form.get("return_url", "/"))})
    return RedirectResponse(f"{FAKE_PREFIX}/paypal/manage/{subscription_id}?{back}", status_code=303)


@router.get("/events")
def fake_events():
    """The webhooks the fakes delivered (newest first) and what the receiving code answered."""
    if config.IS_PROD or not config.BILLING_FAKE_PROVIDERS:
        raise HTTPException(status_code=404)
    return JSONResponse(
        {
            "events": [
                {"provider": e.provider, "type": e.type, "event_id": e.event_id, "at": e.at, "result": e.result}
                for e in reversed(EVENTS)
            ],
            "stripe": {"secret_key": STRIPE.secret_key[:12] + "…", "sessions": len(STRIPE.sessions)},
            "paypal": {"client_id": PAYPAL.client_id, "subscriptions": len(PAYPAL.subscriptions)},
            "time": time.time(),
        }
    )
