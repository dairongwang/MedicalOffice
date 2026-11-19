"""Dummy data for development and tests — every account, record and e-mail here is fake ("dummy").

    python seed.py                       everything below (the dummy accounts' passwords are reset)
    python seed.py --ads [--force]       only the sponsored-content campaigns (--force: also when APP_ENV=prod)
    python seed.py --promote <email>     give an existing account the administrator role
    python seed.py --demote <email>      … and take it away again (back to a plain account)

``python dev.py seed`` runs it against the development database; ``python dev.py run`` creates the same
data at every start of the API (``SEED_FAKE_ACCOUNTS=true`` — main.py's lifespan), never in production.

What is created (idempotent — running it again refreshes, never duplicates):

    seed()                  one account per membership type and role — real accounts the apps can sign in
                            to (clientcrypto.py derives the keys exactly like the browser):
                              dummy-free@ · dummy-trial@ · dummy-pro@ · dummy-lifetime@ · dummy-pastdue@ ·
                              dummy-expired@ · dummy-admin@ (administrator) · dummy-twofa@ (authenticator
                              app on: TOTP secret TWOFA_SECRET, recovery codes TWOFA_RECOVERY_CODES) ·
                              dummy-patient@ (patient account)            all @example.com
                            password: ``Dummy-<Kind>-pass-123`` (password_for), e.g. Dummy-Admin-pass-123
    seed_ads()              four sponsored-content campaigns (internal, sponsored ×2, affiliate)
    seed_workers()          the staff roster: Dr. Alex Example (doctor, W-000001), Sam Sample (nurse),
                            Robin Placeholder (front desk — no account, to try "Create staff account"),
                            Jamie Helper and Taylor Aide (medical assistants) — and staff accounts for the
                            doctor, the nurse and both assistants (dummy-doctor@, dummy-nurse@,
                            dummy-assistant@, dummy-assistant2@)
    seed_applications()     three job applications (licence to confirm · licence expiring soon · too young)
    seed_payment_methods()  the patient account's payment methods: a card (the well-known test number
                            4242 4242 4242 4242, tokenized by the fake vault) and a PayPal wallet
    seed_pharmacies()       three pharmacies (one without e-mail)
    seed_patients()         three patients — Pat Dummy (the patient account's record: insurance, medical
                            history, a past visit with everything a chart has and a **signed visit summary**,
                            and two upcoming visits: an annual check-up and a blood-pressure follow-up that is
                            already checked in), Casey Dummy (self-pay, phone only), Riley Dummy (HMO plan,
                            an incoming referral)
    seed_assignments()      the medical assistant Jamie Helper on Pat's upcoming visits
    seed_reports()          immunization histories (Pat, Riley); Pat's annual physical ten months ago; a signed
                            immunization record for Pat's employer and her open request for an annual health
                            examination report (for the running club) — waiting in the office's queue
    seed_family()           Sky Dummy, Pat's daughter, on the patient account too (so the dashboard's "My
                            medicines" has a tab for each): asthma, an amoxicillin allergy, her asthma check six
                            weeks ago with two prescriptions (an albuterol inhaler, cetirizine) that carry
                            instructions for the patient, and its signed visit summary

Phone numbers are in the 555-01xx range reserved for fiction; e-mail addresses use example.com.
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import os
import sys
from collections.abc import Callable
from datetime import date, datetime, time, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

import accounts
import appointments
import assignments
import auth
import billing
import check_ins
import clientcrypto
import config
import health_records
import hiring
import immunizations
import models
import patient_payment_methods as ppm
import patient_payments
import patient_reports
import patients
import pdf
import prescriptions
import schemas
import totp
import visit_summaries
import workers

Log = Callable[..., None]

DOMAIN = "example.com"
# The membership types / roles of the dummy accounts (seed()).
KINDS = ("free", "trial", "pro", "lifetime", "pastdue", "expired", "admin", "twofa", "patient")
# The staff accounts (seed_workers()): kind → the worker record's e-mail is email_for(kind).
STAFF_KINDS = ("doctor", "nurse", "assistant", "assistant2")
# dummy-twofa@example.com: the authenticator secret (base32) and the recovery codes — all fake.
TWOFA_SECRET = "DUMMYDUMMYDUMMYDUMMYDUMMYDUMMYDU"
TWOFA_RECOVERY_CODES = tuple(f"dummy-{n:05d}" for n in range(1, 11))
# The dummy patient's card: the public test number every payment provider documents (never a real card).
DUMMY_CARD_NUMBER = "4242424242424242"


def email_for(kind: str) -> str:
    return f"dummy-{kind}@{DOMAIN}"


def password_for(kind: str) -> str:
    """``Dummy-Admin-pass-123`` — what the sign-in forms accept for ``email_for(kind)``."""
    return f"Dummy-{kind.capitalize()}-pass-123"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _quiet(*_args) -> None:
    return None


# ---------------------------------------------------------------------------- a dummy signature


def signature_png(seed_text: str, *, width: int = 360, height: int = 90) -> str:
    """A drawn-looking signature (a PNG data URL) — generated, different per ``seed_text``."""
    ink = (17, 24, 39)
    pixels = bytearray(b"\xff" * (width * height * 3))
    h = sum(ord(c) for c in seed_text)
    a1, a2, f1, f2 = 14 + h % 9, 6 + h % 5, 0.045 + (h % 7) / 400, 0.11 + (h % 11) / 300

    def dot(x: int, y: int) -> None:
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                px, py = x + dx, y + dy
                if 0 <= px < width and 0 <= py < height:
                    i = (py * width + px) * 3
                    pixels[i : i + 3] = bytes(ink)

    prev = None
    for x in range(20, width - 20):
        t = x - 20
        y = int(height / 2 + a1 * math.sin(t * f1) * math.exp(-t / (width * 1.4)) + a2 * math.sin(t * f2 + h))
        if prev is not None:
            steps = max(1, abs(y - prev[1]))
            for s in range(steps + 1):
                dot(prev[0] + (x - prev[0]) * s // steps, prev[1] + (y - prev[1]) * s // steps)
        prev = (x, y)
    for x in range(30, width - 60):  # the flourish under the name
        dot(x, height - 18 + int(2 * math.sin(x / 17)))
    return "data:image/png;base64," + base64.b64encode(pdf.encode_png(width, height, bytes(pixels))).decode()


# ---------------------------------------------------------------------------- accounts


def _account(db: Session, kind: str, role: str, *, reset_password: bool) -> tuple[models.Account, bool]:
    """The dummy account ``email_for(kind)`` with its role — created (keys derived like the browser does)
    when missing; its password is set again when asked to, or when the account was purged."""
    email = email_for(kind)
    account = db.execute(select(models.Account).where(models.Account.email == email)).scalar_one_or_none()
    created = account is None
    if created or reset_password or not account.auth_hash or account.deleted_at is not None:
        auth_key, wrap_key = clientcrypto.derive_keys(password_for(kind), email)
        salt = auth.new_salt()
        if created:
            account = models.Account(email=email, auth_salt=salt, auth_hash="", wrapped_data_key="")
            db.add(account)
        account.auth_salt = salt
        account.auth_hash = auth.hash_auth_key(auth_key, salt)
        account.wrapped_data_key = clientcrypto.wrap_data_key(os.urandom(32), wrap_key)
        account.kdf_params = json.dumps(clientcrypto.KDF_PARAMS)
        account.deleted_at = None
    account.role = role
    db.flush()
    return account, created


def _subscription(db: Session, account: models.Account, plan: str, status: str, period_end: datetime | None) -> None:
    sub = billing.subscription_for(db, account)
    if sub.provider != "seed" and (sub.provider_subscription_id or sub.provider_customer_id):
        return  # a subscription bought while testing (through the provider / its fake): leave it alone
    sub.provider = "seed"
    sub.provider_customer_id = None
    sub.provider_subscription_id = None
    sub.plan, sub.status, sub.current_period_end = plan, status, period_end
    sub.cancel_at_period_end = False


def seed(db: Session, log: Log = _quiet, *, reset_passwords: bool = False) -> dict[str, models.Account]:
    """One account per membership type / role (see the module docstring). Returns them by kind."""
    if config.IS_PROD:
        log("seed: APP_ENV=prod — the dummy accounts are not created")
        return {}
    now = _now()
    memberships = {
        "free": ("free", "none", None),
        "trial": ("pro", "trialing", now + timedelta(days=config.TRIAL_DAYS)),
        "pro": ("pro", "active", now + timedelta(days=30)),
        "lifetime": ("lifetime", "active", None),
        "pastdue": ("pro", "past_due", now - timedelta(days=2)),
        "expired": ("pro", "canceled", now - timedelta(days=120)),
    }
    out = {}
    for kind in KINDS:
        role = {"admin": models.ROLE_ADMIN, "patient": models.ROLE_PATIENT}.get(kind, models.ROLE_USER)
        account, created = _account(db, kind, role, reset_password=reset_passwords)
        plan, status, period_end = memberships.get(kind, ("free", "none", None))
        _subscription(db, account, plan, status, period_end)
        if kind == "twofa":
            account.totp_secret = TWOFA_SECRET
            account.totp_enabled_at = account.totp_enabled_at or now
            account.totp_pending_secret = None
            account.recovery_codes = json.dumps(
                [totp.hash_recovery_code(code, account.auth_salt) for code in TWOFA_RECOVERY_CODES]
            )
        out[kind] = account
        if created:
            log("seed: %s / %s (%s)", email_for(kind), password_for(kind), kind)
    db.commit()
    log(
        "seed: dummy accounts ready — %s (password Dummy-<Kind>-pass-123; dummy-twofa@: recovery code %s)",
        ", ".join(email_for(k) for k in KINDS),
        TWOFA_RECOVERY_CODES[0],
    )
    return out


def set_role(db: Session, email: str, admin: bool, log: Log = print) -> bool:
    """``python seed.py --promote / --demote <email>`` — audited (no actor: the command line)."""
    account = db.execute(
        select(models.Account).where(models.Account.email == email.strip().lower())
    ).scalar_one_or_none()
    if account is None or account.deleted_at is not None:
        log("No account with the e-mail %s.", email)
        return False
    if admin and account.role == models.ROLE_PATIENT:
        log("%s is a patient account — patient accounts cannot become administrators.", email)
        return False
    before = account.role
    account.role = models.ROLE_ADMIN if admin else (models.ROLE_USER if before == models.ROLE_ADMIN else before)
    accounts.audit(db, None, "account.role.cli", account, before=before, after=account.role)
    db.commit()
    log("%s: role %s → %s", account.email, before, account.role)
    return True


# ---------------------------------------------------------------------------- sponsored content

CAMPAIGNS = (
    {
        "kind": "internal",
        "sponsor": "Medic Office management",
        "headline": "Go Pro — unlimited tasks, encrypted sync",
        "blurb": "Keep every device in step, end-to-end encrypted. Try it free for two weeks.",
        "cta_label": "Start trial",
        "target_url": "app:upgrade",
        "weight": 30,
    },
    {
        "kind": "sponsored",
        "sponsor": "Example Coffee Roasters",
        "headline": "Freshly roasted beans, delivered weekly",
        "blurb": "Single-origin coffee for focused mornings. Dummy campaign — the link goes to example.com.",
        "cta_label": "Learn more",
        "target_url": "https://example.com/coffee?utm_source=dummy-medic-office",
        "weight": 20,
    },
    {
        "kind": "sponsored",
        "sponsor": "Example Notebooks",
        "headline": "The notebook that survives your bag",
        "blurb": "Dotted pages, lay-flat binding. Dummy campaign targeted at German-speaking countries.",
        "cta_label": "Shop now",
        "target_url": "https://example.com/notebooks?utm_source=dummy-medic-office",
        "regions": "DE,AT,CH",
        "languages": "de,en",
        "weight": 10,
    },
    {
        "kind": "affiliate",
        "sponsor": "Example Books",
        "headline": "Getting Things Done — the classic on stress-free productivity",
        "blurb": "We may earn a commission if you buy through this link. Dummy affiliate campaign.",
        "cta_label": "View book",
        "target_url": "https://example.com/books/gtd",
        "weight": 10,
    },
)


def seed_ads(db: Session, log: Log = _quiet, *, force: bool = False) -> int:
    """The dummy campaigns (first-party sources). In prod only with ``force`` (``--ads --force``)."""
    if config.IS_PROD and not force:
        log("seed: APP_ENV=prod — dummy campaigns are only created with --ads --force")
        return 0
    n = 0
    for c in CAMPAIGNS:
        exists = db.execute(
            select(models.AdCampaign).where(
                models.AdCampaign.sponsor == c["sponsor"], models.AdCampaign.headline == c["headline"]
            )
        ).scalar_one_or_none()
        if exists is not None:
            continue
        db.add(models.AdCampaign(**{"status": "active", "regions": "", "languages": "", **c}))
        n += 1
    db.commit()
    if n:
        log("seed: %d dummy sponsored-content campaign(s)", n)
    return n


# ---------------------------------------------------------------------------- the staff roster

WORKERS = (
    {
        "kind": "doctor",
        "first_name": "Alex",
        "last_name": "Example",
        "role": "doctor",
        "specialty": "General practice",
        "license_number": "DUMMY-MD-0001",
        "phone": "+1 555 010 0001",
        "employment_type": "full_time",
    },
    {
        "kind": "nurse",
        "first_name": "Sam",
        "last_name": "Sample",
        "role": "nurse",
        "specialty": "Triage",
        "license_number": "DUMMY-RN-0002",
        "phone": "+1 555 010 0002",
        "employment_type": "part_time",
    },
    {
        "kind": "frontdesk",
        "first_name": "Robin",
        "last_name": "Placeholder",
        "role": "receptionist",
        "phone": "+1 555 010 0003",
        "employment_type": "full_time",
    },
    {
        "kind": "assistant",
        "first_name": "Jamie",
        "last_name": "Helper",
        "role": "medical_assistant",
        "specialty": "Clinical medical assistant (dummy CMA)",
        "phone": "+1 555 010 0004",
        "employment_type": "full_time",
        "notes": "Dummy medical assistant (seed data): the standard week, Monday–Friday 9–5.",
    },
    {
        "kind": "assistant2",
        "first_name": "Taylor",
        "last_name": "Aide",
        "role": "medical_assistant",
        "specialty": "Clinical medical assistant (dummy CMA)",
        "phone": "+1 555 010 0005",
        "employment_type": "part_time",
        "notes": "Dummy medical assistant (seed data): afternoons only, Monday–Thursday 1–5 PM.",
    },
)


def _worker(db: Session, email: str) -> models.Worker | None:
    return db.execute(
        select(models.Worker).where(models.Worker.email_norm == patients.normalize_email(email))
    ).scalar_one_or_none()


def seed_workers(db: Session, log: Log = _quiet, *, reset_passwords: bool = False) -> dict[str, models.Worker]:
    """The dummy staff and their staff-dashboard accounts (see the module docstring)."""
    if config.IS_PROD:
        return {}
    out = {}
    for spec in WORKERS:
        spec = dict(spec)
        kind = spec.pop("kind")
        email = email_for(kind)
        w = _worker(db, email)
        if w is None:
            w = workers.create(db, schemas.WorkerIn(email=email, consent=True, **spec))
            if kind == "assistant2":  # afternoons only, Monday–Thursday
                workers._apply_week(
                    db,
                    w,
                    [
                        schemas.DayAvailability(
                            weekday=d, available=d < 4, start_time=time(13, 0), end_time=time(17, 0)
                        )
                        for d in range(7)
                    ],
                )
            log("seed: staff %s %s (%s, %s)", w.first_name, w.last_name, w.role, w.staff_number)
        w.active = True
        out[kind] = w
        if kind in STAFF_KINDS:
            account, _ = _account(db, kind, models.ROLE_STAFF, reset_password=reset_passwords)
            billing.subscription_for(db, account)
            if w.account_id != account.id:
                db.execute(
                    models.Worker.__table__.update()
                    .where(models.Worker.account_id == account.id, models.Worker.id != w.id)
                    .values(account_id=None)
                )
                w.account_id = account.id
    db.commit()
    return out


# ---------------------------------------------------------------------------- job applications


def seed_applications(db: Session, log: Log = _quiet) -> int:
    """Three applications in the queue: a doctor whose licence a manager must confirm, a nurse whose
    licence expires soon, and a receptionist too young to be hired."""
    if config.IS_PROD:
        return 0
    today = date.today()
    specs = (
        {
            "first_name": "Morgan",
            "last_name": "Applicant",
            "email": "dummy-morgan.applicant@example.com",
            "phone": "+1 555 010 0101",
            "date_of_birth": date(1990, 4, 12),
            "role": "doctor",
            "specialty": "Internal medicine",
            "employment_type": "full_time",
            "years_experience": 8,
            "license_number": "DUMMY-MD-10101",
            "license_authority": "Example State Medical Board",
            "license_expires": today + timedelta(days=500),
            "certifications": "BLS, ACLS",
            "emergency_contact_name": "Jamie Applicant",
            "emergency_contact_phone": "+1 555 010 0102",
        },
        {
            "first_name": "Riley",
            "last_name": "Hopeful",
            "email": "dummy-riley.hopeful@example.com",
            "phone": "+1 555 010 0201",
            "date_of_birth": date(1996, 9, 30),
            "role": "nurse",
            "specialty": "Paediatrics",
            "employment_type": "part_time",
            "years_experience": 3,
            "license_number": "DUMMY-RN-20202",
            "license_authority": "Example State Board of Nursing",
            "license_expires": today + timedelta(days=45),
            "certifications": "BLS",
        },
        {
            "first_name": "Quinn",
            "last_name": "Tooyoung",
            "email": "dummy-quinn.tooyoung@example.com",
            "date_of_birth": _years_ago(today, 16),
            "role": "receptionist",
            "employment_type": "part_time",
        },
    )
    n = 0
    for spec in specs:
        email_norm = patients.normalize_email(spec["email"]) or ""
        if db.execute(select(models.JobApplication).where(models.JobApplication.email_norm == email_norm)).first():
            continue
        age_status, age_note = hiring.age_check(spec["date_of_birth"])
        lic_status, lic_note = hiring.license_check(
            spec["role"], spec.get("license_number"), spec.get("license_authority"), spec.get("license_expires")
        )
        db.add(
            models.JobApplication(
                **spec,
                cover_note="Dummy application (seed data).",
                background_consent=True,
                status="screening" if age_status == "passed" else "submitted",
                age_check=age_status,
                age_note=age_note,
                license_check=lic_status,
                license_note=lic_note,
                background_check="pending",
                email_norm=email_norm,
            )
        )
        n += 1
    db.commit()
    if n:
        log("seed: %d dummy job application(s)", n)
    return n


def _years_ago(today: date, years: int) -> date:
    try:
        return today.replace(year=today.year - years)
    except ValueError:  # February 29th
        return today.replace(year=today.year - years, day=28)


# ---------------------------------------------------------------------------- payment methods and pharmacies


def _patient_account(db: Session) -> models.Account | None:
    return db.execute(
        select(models.Account).where(models.Account.email == email_for("patient"), models.Account.deleted_at.is_(None))
    ).scalar_one_or_none()


def seed_payment_methods(db: Session, log: Log = _quiet) -> int:
    """The patient account's card (the public test number) and PayPal wallet — tokenized and validated
    by the built-in fake vault, exactly like the patient adding them in the app."""
    if config.IS_PROD:
        return 0
    account = _patient_account(db)
    if account is None:
        return 0
    methods = (
        schemas.PatientPaymentMethodIn(
            kind="card",
            label="Family card (dummy)",
            holder_name="Pat Dummy",
            is_default=True,
            card_number=DUMMY_CARD_NUMBER,
            card_exp_month=12,
            card_exp_year=date.today().year + 5,
            billing_postal_code="00000",
        ),
        schemas.PatientPaymentMethodIn(kind="paypal", holder_name="Pat Dummy", wallet_email=email_for("patient")),
    )
    on_file = ppm.list_methods(db, account)
    n = 0
    for body in methods:
        # Matched on what is kept in the clear: the fingerprint is keyed with TOKEN_SECRET, which a
        # development server makes up at every start when none is configured.
        if any(
            m.kind == body.kind
            and (
                m.card_last4 == DUMMY_CARD_NUMBER[-4:]
                if body.kind == "card"
                else m.wallet_email_masked == ppm.mask_email(str(body.wallet_email))
            )
            for m in on_file
        ):
            continue
        try:
            ppm.add(db, account, body, triggered_by="system")
            n += 1
        except HTTPException as exc:  # already on file (409) — the seed ran before
            db.rollback()
            if exc.status_code != 409:
                raise
            continue
        db.commit()
    if n:
        log("seed: %d payment method(s) on %s", n, account.email)
    return n


PHARMACIES = (
    {
        "name": "Example Pharmacy — Main Street (dummy)",
        "email": "dummy-pharmacy-main@example.com",
        "phone": "+1 555 010 0401",
        "fax": "+1 555 010 0402",
        "address": "10 Main Street, Exampleville (dummy)",
    },
    {
        "name": "Exampleville Drugstore (dummy)",
        "email": "dummy-pharmacy-drugstore@example.com",
        "phone": "+1 555 010 0411",
        "address": "252 Market Road, Exampleville (dummy)",
    },
    {
        "name": "Corner Chemist — no e-mail (dummy)",
        "phone": "+1 555 010 0421",
        "fax": "+1 555 010 0422",
        "address": "3 Corner Lane, Exampleville (dummy)",
    },
)


def seed_pharmacies(db: Session, log: Log = _quiet) -> int:
    if config.IS_PROD:
        return 0
    n = 0
    for spec in PHARMACIES:
        if db.execute(select(models.Pharmacy).where(models.Pharmacy.name == spec["name"])).first():
            continue
        db.add(models.Pharmacy(**spec, source="seed"))
        n += 1
    db.commit()
    if n:
        log("seed: %d dummy pharmacies", n)
    return n


# ---------------------------------------------------------------------------- patients and their visits

PATIENTS = (
    {
        "first_name": "Pat",
        "last_name": "Dummy",
        "date_of_birth": date(1985, 3, 14),
        "sex": "female",
        "email": email_for("patient"),
        "phone": "+1 555 010 0301",
        "address_line": "1 Example Street (dummy)",
        "city": "Exampleville",
        "postal_code": "00000",
        "insurance_provider": "Example Health (dummy)",
        "insurance_plan_name": "PPO Silver (dummy)",
        "insurance_member_id": "DUMMY-INS-0301",
        "insurance_group_number": "DUMMY-GRP-01",
        "insurance_policyholder_relationship": "self",
        "emergency_contact_name": "Jordan Dummy",
        "emergency_contact_phone": "+1 555 010 0309",
    },
    {
        "first_name": "Casey",
        "last_name": "Dummy",
        "date_of_birth": date(1972, 11, 2),
        "sex": "male",
        "phone": "+1 555 010 0302",
        "city": "Exampleville",
    },
    {
        "first_name": "Riley",
        "last_name": "Dummy",
        "date_of_birth": date(2001, 6, 21),
        "sex": "other",
        "email": "dummy-riley@example.com",
        "city": "Exampleville",
        "insurance_provider": "Example Health (dummy)",
        "insurance_plan_name": "HMO Basic (dummy)",
        "insurance_member_id": "DUMMY-HMO-0303",
        "insurance_policyholder_name": "Jordan Dummy",
        "insurance_policyholder_relationship": "parent",
    },
)

HEALTH_ITEMS = {
    "Pat": (
        ("medication", "Lisinopril", "10 mg tablet — Take 1 tablet by mouth once daily"),
        ("medication", "Atorvastatin", "20 mg at night"),
        ("allergy", "Penicillin", "rash"),
        ("condition", "Hypertension", "since 2019 — on lisinopril"),
        ("condition", "Prediabetes", "A1c 6.1 % last year — diet and exercise"),
        ("surgery", "Appendectomy", "2008, laparoscopic"),
        ("family_history", "Heart attack — father", "at 62"),
        ("family_history", "Type 2 diabetes — mother", None),
        ("social_history", "Never smoked", "occasional alcohol; walks three times a week"),
    ),
    "Riley": (
        ("allergy", "Peanuts", "anaphylaxis — carries an epinephrine pen (dummy)"),
        ("condition", "Asthma", "mild intermittent — inhaler as needed"),
    ),
}

PAST_REASON = "Blood pressure check (dummy past visit)."
LISINOPRIL_INSTRUCTIONS = (
    "Take it at the same time every day, with or without food. Stand up slowly if you feel dizzy. "
    "Call the office if you get a dry cough that does not go away or any swelling of the face or lips. (Dummy.)"
)
CHECKUP_REASON = "Annual check-up (dummy appointment)."
FOLLOW_UP_REASON = "Blood pressure follow-up (dummy appointment)."


def _patient(db: Session, spec: dict) -> tuple[models.Patient, bool]:
    row = db.execute(
        select(models.Patient).where(
            models.Patient.first_name_norm == patients.normalize_name(spec["first_name"]),
            models.Patient.last_name_norm == patients.normalize_name(spec["last_name"]),
            models.Patient.date_of_birth == spec["date_of_birth"],
        )
    ).scalar_one_or_none()
    if row is not None:
        return row, False
    return patients.create(db, schemas.PatientIn(consent=True, **spec)), True


def _office_moment(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), appointments.office_tz()).astimezone(timezone.utc)


def _weekday_on_or_before(day: date) -> date:
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def _appointment(db: Session, patient: models.Patient, reason: str, *, upcoming: bool) -> models.Appointment | None:
    q = select(models.Appointment).where(
        models.Appointment.patient_id == patient.id, models.Appointment.reason == reason
    )
    if upcoming:
        q = q.where(models.Appointment.status == "scheduled", models.Appointment.end_at >= _now())
    return db.execute(q.order_by(models.Appointment.start_at.desc())).scalars().first()


def _free_slot(db: Session, provider: models.Worker, hour: int) -> datetime | None:
    """The next weekday (from tomorrow, office time) with a free slot at ``hour`` — or the first free slot
    of that day after it."""
    day = _now().astimezone(appointments.office_tz()).date() + timedelta(days=1)
    for _ in range(30):
        if day.weekday() < 5:
            slots = appointments.day_slots(db, provider, day).slots
            wanted = next((s for s in slots if s.label == f"{hour:02d}:00" and s.available), None)
            later = next((s for s in slots if s.available and int(s.label[:2]) >= hour), None)
            choice = wanted or later
            if choice is not None:
                return choice.start_at
        day += timedelta(days=1)
    return None


def _past_visit(
    db: Session, pat: models.Patient, doctor: models.Worker, assistant: models.Worker | None, log: Log
) -> models.Appointment:
    """A completed visit about three months ago with everything a chart has: the assistant's work (vital
    signs), the doctor's note, two diagnoses, a prescription e-mailed to the pharmacy, lab results, a
    referral to cardiology — and the visit summary the doctor signed."""
    today = _now().astimezone(appointments.office_tz()).date()
    day = _weekday_on_or_before(today - timedelta(days=90))
    start = _office_moment(day, 10)
    a = models.Appointment(
        patient_id=pat.id,
        provider_id=doctor.id,
        start_at=start,
        end_at=start + appointments.slot_length(),
        status="completed",
        reason=PAST_REASON,
        payment="insurance",
        coverage_status="active",
        confirmed_at=start - timedelta(days=1),
        confirmed_via="link",
        created_at=start - timedelta(days=14),
    )
    db.add(a)
    db.flush()
    if assistant is not None:
        db.add(
            models.AppointmentAssignment(
                appointment_id=a.id,
                worker_id=assistant.id,
                status="completed",
                instructions="Dummy task (seed data): blood pressure check.",
                assigned_at=start - timedelta(days=2),
                accepted_at=start - timedelta(hours=2),
                completed_at=start - timedelta(minutes=5),
                duties=json.dumps([key for key, _ in models.ASSISTANT_DUTIES]),
                completion_note="Home readings around 140/90 (dummy).",
                vitals=json.dumps(
                    {
                        "bp_systolic": 146,
                        "bp_diastolic": 92,
                        "pulse": 78,
                        "temperature": 98.4,
                        "temperature_unit": "F",
                        "weight": 158,
                        "weight_unit": "lb",
                        "note": "Repeated after 5 minutes: 144/90 (dummy)",
                    }
                ),
                checklist=json.dumps(
                    {
                        "room_patient": True,
                        "check_in_reviewed": True,
                        "provider_ready": True,
                        "check_in_note": "No check-in on the app — questions asked in the room.",
                    },
                    sort_keys=True,
                ),
                provider_notified_at=start - timedelta(minutes=5),
            )
        )
    db.add(
        models.VisitNote(
            appointment_id=a.id,
            worker_id=doctor.id,
            seen_at=start + timedelta(minutes=3),
            note="Follow-up of hypertension. Home readings 135–145 / 85–92. No chest pain, no headaches. "
            "Exam unremarkable (dummy note).",
            assessment="Essential hypertension, not at goal. Prediabetes.",
            plan="Continue lisinopril 10 mg daily; basic metabolic panel, lipid panel and A1c; referral to "
            "cardiology (family history); recheck in three months (dummy plan).",
        )
    )
    primary = models.VisitDiagnosis(
        appointment_id=a.id,
        patient_id=pat.id,
        worker_id=doctor.id,
        description="Essential hypertension",
        code="I10",
        is_primary=True,
        created_at=start + timedelta(minutes=15),
    )
    db.add(primary)
    db.add(
        models.VisitDiagnosis(
            appointment_id=a.id,
            patient_id=pat.id,
            worker_id=doctor.id,
            description="Prediabetes",
            code="R73.03",
            created_at=start + timedelta(minutes=16),
        )
    )
    db.flush()
    doctor_account = db.get(models.Account, doctor.account_id) if doctor.account_id else None
    rx = prescriptions.issue(
        db,
        a,
        doctor,
        schemas.PrescriptionIn(
            items=[
                schemas.PrescriptionItemIn(
                    drug="Lisinopril",
                    strength="10 mg",
                    form="tablet",
                    sig="Take 1 tablet by mouth once daily",
                    quantity=90,
                    refills=3,
                    patient_instructions=LISINOPRIL_INSTRUCTIONS,
                )
            ],
            diagnosis_id=primary.id,
            note_to_pharmacist="Dummy prescription (seed data).",
        ),
        doctor_account,
    )
    rx.issued_at = start + timedelta(minutes=20)
    rx.verification = prescriptions._verification(rx)
    pharmacy = db.execute(select(models.Pharmacy).where(models.Pharmacy.email.is_not(None))).scalars().first()
    if pharmacy is not None:
        db.add(
            models.PrescriptionDelivery(
                prescription_id=rx.id,
                method="email",
                pharmacy_id=pharmacy.id,
                destination="d•••@example.com",
                status="sent",
                note="dummy data",
                created_at=start + timedelta(minutes=21),
            )
        )
    labs = (
        (
            "Basic metabolic panel",
            2,
            "Fasting glucose above the reference range (dummy result).",
            [
                ("Sodium", "139", "mmol/L", "135–145", ""),
                ("Potassium", "4.1", "mmol/L", "3.5–5.1", ""),
                ("Creatinine", "0.9", "mg/dL", "0.6–1.2", ""),
                ("Glucose (fasting)", "112", "mg/dL", "70–99", "H"),
            ],
        ),
        (
            "Lipid panel",
            2,
            None,
            [
                ("Total cholesterol", "212", "mg/dL", "< 200", "H"),
                ("LDL cholesterol", "142", "mg/dL", "< 100", "H"),
                ("HDL cholesterol", "48", "mg/dL", "> 40", ""),
                ("Triglycerides", "160", "mg/dL", "< 150", "H"),
            ],
        ),
        ("Hemoglobin A1c", 3, None, [("Hemoglobin A1c", "6.3", "%", "4.0–5.6", "H")]),
    )
    for name, days, summary, values in labs:
        db.add(
            models.PatientResult(
                patient_id=pat.id,
                kind="lab",
                name=name,
                status="final",
                ordered_on=day,
                resulted_on=day + timedelta(days=days),
                performed_by="Example Labs (dummy)",
                summary=summary,
                lab_values=json.dumps(
                    [{"name": n, "value": v, "unit": u, "range": r, "flag": f} for n, v, u, r, f in values]
                ),
                appointment_id=a.id,
                ordered_by=doctor.id,
            )
        )
    db.add(
        models.PatientResult(
            patient_id=pat.id,
            kind="imaging",
            name="Chest X-ray (2 views)",
            status="final",
            ordered_on=day - timedelta(days=310),
            resulted_on=day - timedelta(days=309),
            performed_by="Exampleville Imaging (dummy)",
            summary="IMPRESSION: No acute cardiopulmonary abnormality. Normal heart size. (Dummy report.)",
            lab_values="[]",
        )
    )
    db.add(
        models.PatientReferral(
            patient_id=pat.id,
            direction="outgoing",
            counterpart="Dr. Casey Heart — Exampleville Cardiology (dummy)",
            specialty="Cardiology",
            referral_date=day,
            valid_until=day + timedelta(days=180),
            reason="Blood pressure above goal on treatment; father had a heart attack at 62.",
            note="Referral letter sent with the last results (dummy). Cardiology visit planned.",
            status="scheduled",
            appointment_id=a.id,
        )
    )
    db.flush()

    db.flush()
    log("seed: past visit %s (completed)", a.number)
    return a


PAT_SUMMARY_INSTRUCTIONS = (
    "Keep taking lisinopril 10 mg once a day, at the same time every day.\n"
    "Check your blood pressure at home twice a week and write the readings down.\n"
    "Go to the lab for the blood tests (fasting — nothing to eat after midnight).\n"
    "Call the cardiology office to book the appointment the referral is for.\n"
    "Less salt, more vegetables, and keep walking three times a week. (Dummy instructions.)"
)
PAT_SUMMARY_FOLLOW_UP = "Blood pressure follow-up in three months — book it from My appointments. (Dummy.)"


def _signed_summary(
    db: Session,
    a: models.Appointment,
    pat: models.Patient,
    doctor: models.Worker,
    log: Log,
    *,
    instructions: str = PAT_SUMMARY_INSTRUCTIONS,
    follow_up: str = PAT_SUMMARY_FOLLOW_UP,
    sent_to: str | None = None,
) -> None:
    """The visit summary the doctor signed the day of the past visit (delivered to the patient back then)."""
    if visit_summaries.versions_of(db, a):
        return
    doctor_account = db.get(models.Account, doctor.account_id) if doctor.account_id else None
    if doctor_account is None:
        return
    start = appointments.as_utc(a.start_at)
    s, _ = visit_summaries.ensure_draft(db, a, "seed")
    s.instructions = instructions
    s.follow_up = follow_up
    visit_summaries.sign(
        db,
        a,
        s,
        doctor,
        doctor_account,
        schemas.VisitSummarySign(
            signature=schemas.PortalSignature(
                signer_name=f"{doctor.first_name} {doctor.last_name}",
                image_png=signature_png(f"{doctor.first_name} {doctor.last_name}"),
                consent=True,
            ),
            send_to_patient=False,
        ),
    )
    s.signed_at = start + timedelta(minutes=40)
    s.generated_at = start + timedelta(minutes=35)
    s.verification = visit_summaries._verification(s)
    s.sent_at = s.signed_at + timedelta(minutes=1)
    s.sent_channels = "email"
    email = sent_to or pat.email
    s.sent_to = f"email:{ppm.mask_email(email)}" if email else ""
    s.delivery_note = "dummy data (seed) — not actually sent"
    s.patient_viewed_at = s.signed_at + timedelta(hours=5)
    db.flush()
    log("seed: visit summary %s of %s, signed by %s", s.number, a.number, s.signer_name)


def _book(
    db: Session, pat: models.Patient, doctor: models.Worker, reason: str, hour: int, log: Log
) -> models.Appointment | None:
    """An upcoming visit for the patient, booked like the app does it: the coverage checked for the day, the
    copay put on hold on the default payment method, the appointment link sent (the fake outbox)."""
    start = _free_slot(db, doctor, hour)
    if start is None:
        log("seed: no free slot found for %s", reason)
        return None
    day = start.astimezone(appointments.office_tz()).date()
    cover = appointments.coverage_for(db, pat, day)
    a = models.Appointment(
        patient_id=pat.id,
        provider_id=doctor.id,
        start_at=start,
        end_at=start + appointments.slot_length(),
        status="scheduled",
        reason=reason,
        payment="insurance" if cover.covered else "self_pay",
        coverage_status=cover.status,
        coverage_verification_id=cover.verification.id if cover.verification else None,
        self_pay_agreed_at=None if cover.covered else _now(),
    )
    db.add(a)
    db.flush()
    account = db.get(models.Account, pat.account_id) if pat.account_id else None
    method = patient_payments.default_method(db, account) if account is not None else None
    if method is not None:
        try:
            patient_payments.hold_for_appointment(db, a, pat, account, method, cover, created_by="system")
        except HTTPException as exc:
            log("seed: no hold for %s: %s", a.number, exc.detail)
    appointments.send_link(db, a, None)
    db.flush()
    log("seed: upcoming %s — %s", a.number, reason)
    return a


def _check_in(db: Session, a: models.Appointment, pat: models.Patient, log: Log) -> None:
    """The office started the check-in and the patient completed the page (answers, estimate, both
    signatures, the pre-authorization) — the visit is checked in."""
    c, _token = check_ins.start(db, a, check_ins.STAFF, None)
    if c.status != "pending":
        return
    sig = schemas.CheckInSignature(
        signer_name=f"{pat.first_name} {pat.last_name}", image_png=signature_png(f"{pat.first_name} {pat.last_name}")
    )
    estimate = check_ins.estimate(db, a, pat)
    check_ins.submit(
        db,
        c,
        schemas.CheckInSubmit(
            answers={
                "reason": "Blood pressure follow-up",
                "symptoms_since": "none",
                "pain_level": 0,
                "fever": "no",
                "medications_changed": "yes",
                "medications": "Lisinopril 20 mg once daily\nMetformin 500 mg twice daily",
                "allergies": "Penicillin (rash), latex",
                "pregnant": "no",
                "details_changed": "no",
                "consent_treatment": True,
            },
            answers_signature=sig,
            estimate_cents=estimate.total_cents,
            accept_financial_responsibility=True,
            financial_signature=sig,
        ),
    )
    db.flush()
    log("seed: %s checked in (%s)", a.number, c.status)


def seed_patients(db: Session, log: Log = _quiet) -> dict[str, models.Patient]:
    """The three dummy patients and Pat's visits (see the module docstring)."""
    if config.IS_PROD:
        return {}
    out = {}
    account = _patient_account(db)
    for spec in PATIENTS:
        p, created = _patient(db, spec)
        out[p.first_name] = p
        if p.first_name == "Pat" and account is not None and p.account_id is None:
            p.account_id = account.id
        if created:
            if patients.has_insurance(p):
                patients.verify_insurance(db, p, "seed")
            for kind, name, detail in HEALTH_ITEMS.get(p.first_name, ()):
                health_records.add(db, p, kind, name, detail, source="seed")
            log("seed: patient %s %s (%s)", p.first_name, p.last_name, p.mrn)
    db.flush()
    riley = out.get("Riley")
    if (
        riley is not None
        and not db.execute(select(models.PatientReferral).where(models.PatientReferral.patient_id == riley.id)).first()
    ):
        today = _now().astimezone(appointments.office_tz()).date()
        db.add(
            models.PatientReferral(
                patient_id=riley.id,
                direction="incoming",
                counterpart="Exampleville Pediatrics (dummy)",
                specialty="Pediatrics",
                referral_date=today - timedelta(days=30),
                valid_until=today + timedelta(days=150),
                reason="Transfer of care — asthma follow-up (HMO referral).",
                status="open",
            )
        )
    db.commit()

    pat = out.get("Pat")
    doctor = _worker(db, email_for("doctor"))
    assistant = _worker(db, email_for("assistant"))
    if pat is None or doctor is None:
        return out
    past = _appointment(db, pat, PAST_REASON, upcoming=False)
    if past is None:
        past = _past_visit(db, pat, doctor, assistant, log)
        db.commit()
    if past.status == "completed" and past.provider_id == doctor.id:
        _signed_summary(db, past, pat, doctor, log)
        db.commit()
    if _appointment(db, pat, CHECKUP_REASON, upcoming=True) is None:
        _book(db, pat, doctor, CHECKUP_REASON, 10, log)
        db.commit()
    follow_up = _appointment(db, pat, FOLLOW_UP_REASON, upcoming=True)
    if follow_up is None:
        follow_up = _book(db, pat, doctor, FOLLOW_UP_REASON, 11, log)
        db.commit()
        if follow_up is not None:
            _check_in(db, follow_up, pat, log)
            db.commit()
    return out


def seed_assignments(db: Session, log: Log = _quiet) -> int:
    """The medical assistant Jamie Helper (or Taylor Aide, when Jamie is not in) on Pat's upcoming visits."""
    if config.IS_PROD:
        return 0
    pat = db.execute(select(models.Patient).where(models.Patient.email_norm == email_for("patient"))).scalars().first()
    helpers = [w for w in (_worker(db, email_for("assistant")), _worker(db, email_for("assistant2"))) if w]
    if pat is None or not helpers:
        return 0
    n = 0
    instructions = {
        CHECKUP_REASON: "Dummy task (seed data): room the patient for the check-up.",
        FOLLOW_UP_REASON: "Dummy task (seed data): blood pressure follow-up.",
    }
    for reason, text in instructions.items():
        a = _appointment(db, pat, reason, upcoming=True)
        if a is None or assignments.history(db, a):
            continue
        for w in helpers:
            try:
                assignments.assign(db, a, w, actor=None, instructions=text)
            except HTTPException:
                db.rollback()
                continue
            db.commit()
            n += 1
            log("seed: %s %s assigned to %s", w.first_name, w.last_name, a.number)
            break
    return n


# ---------------------------------------------------------------------------- immunizations and reports

ANNUAL_REASON = "Annual physical (dummy past visit)."
VACCINE_MAKER = "Example Vaccines Inc. (dummy)"


def _immunization_history(today: date) -> dict[str, list[dict]]:
    """Pat's and Riley's vaccines (dates relative to today where they must be recent)."""
    flu_year = today.year if today.month >= 10 else today.year - 1
    flu = date(flu_year, 10, 6)
    return {
        "Pat": [
            {"vaccine": "Measles, mumps, rubella (MMR)", "cvx_code": "03", "date_given": date(1986, 3, 20),
             "dose_number": 1, "series_doses": 2, "administered_by": "Exampleville Pediatrics (dummy)",
             "source": "external"},
            {"vaccine": "Measles, mumps, rubella (MMR)", "cvx_code": "03", "date_given": date(1990, 4, 2),
             "dose_number": 2, "series_doses": 2, "administered_by": "Exampleville Pediatrics (dummy)",
             "source": "external"},
            {"vaccine": "Hepatitis B", "cvx_code": "08", "date_given": date(1997, 9, 1), "dose_number": 1,
             "series_doses": 3, "administered_by": "Exampleville School Clinic (dummy)", "source": "external"},
            {"vaccine": "Hepatitis B", "cvx_code": "08", "date_given": date(1997, 10, 1), "dose_number": 2,
             "series_doses": 3, "administered_by": "Exampleville School Clinic (dummy)", "source": "external"},
            {"vaccine": "Hepatitis B", "cvx_code": "08", "date_given": date(1998, 3, 2), "dose_number": 3,
             "series_doses": 3, "administered_by": "Exampleville School Clinic (dummy)", "source": "external"},
            {"vaccine": "Tetanus, diphtheria, pertussis (Tdap)", "cvx_code": "115", "date_given": date(2019, 5, 14),
             "manufacturer": VACCINE_MAKER, "lot_number": "DUMMY-LOT-TDAP-19", "site": "left deltoid", "route": "IM",
             "worker": "nurse", "source": "office"},
            {"vaccine": "COVID-19, updated formula", "cvx_code": "309", "date_given": today - timedelta(days=400),
             "administered_by": "Exampleville Drugstore (dummy)", "source": "external"},
            {"vaccine": "Influenza, inactivated (flu shot)", "cvx_code": "150", "date_given": min(flu, today),
             "dose_number": 1, "series_doses": 1, "manufacturer": VACCINE_MAKER, "lot_number": "DUMMY-LOT-FLU",
             "site": "right deltoid", "route": "IM", "worker": "nurse", "source": "office"},
        ],
        "Riley": [
            {"vaccine": "Measles, mumps, rubella (MMR)", "cvx_code": "03", "date_given": date(2002, 7, 1),
             "dose_number": 1, "series_doses": 2, "administered_by": "Exampleville Pediatrics (dummy)",
             "source": "external"},
            {"vaccine": "Measles, mumps, rubella (MMR)", "cvx_code": "03", "date_given": date(2006, 8, 14),
             "dose_number": 2, "series_doses": 2, "administered_by": "Exampleville Pediatrics (dummy)",
             "source": "external"},
            {"vaccine": "Varicella (chickenpox)", "cvx_code": "21", "date_given": date(2002, 7, 1),
             "administered_by": "Exampleville Pediatrics (dummy)", "source": "external"},
            {"vaccine": "Tetanus, diphtheria, pertussis (Tdap)", "cvx_code": "115", "date_given": date(2013, 9, 3),
             "administered_by": "Exampleville Pediatrics (dummy)", "source": "external"},
        ],
    }  # fmt: skip


def _annual_visit(
    db: Session, pat: models.Patient, doctor: models.Worker, assistant: models.Worker | None, log: Log
) -> models.Appointment:
    """Pat's annual physical about ten months ago: the assistant's vital signs, the doctor's note, Z00.00."""
    today = _now().astimezone(appointments.office_tz()).date()
    start = _office_moment(_weekday_on_or_before(today - timedelta(days=300)), 9)
    a = models.Appointment(
        patient_id=pat.id,
        provider_id=doctor.id,
        start_at=start,
        end_at=start + appointments.slot_length(),
        status="completed",
        reason=ANNUAL_REASON,
        payment="insurance",
        coverage_status="active",
        confirmed_at=start - timedelta(days=1),
        confirmed_via="account",
        created_at=start - timedelta(days=20),
    )
    db.add(a)
    db.flush()
    if assistant is not None:
        db.add(
            models.AppointmentAssignment(
                appointment_id=a.id,
                worker_id=assistant.id,
                status="completed",
                instructions="Dummy task (seed data): annual physical — vital signs, height, vision screen.",
                assigned_at=start - timedelta(days=3),
                accepted_at=start - timedelta(hours=2),
                completed_at=start - timedelta(minutes=5),
                duties=json.dumps([key for key, _ in models.ASSISTANT_DUTIES]),
                completion_note="Height 165 cm; vision 20/20 both eyes with glasses (dummy).",
                vitals=json.dumps(
                    {
                        "bp_systolic": 132,
                        "bp_diastolic": 84,
                        "pulse": 70,
                        "temperature": 98.2,
                        "temperature_unit": "F",
                        "weight": 152,
                        "weight_unit": "lb",
                    }
                ),
                checklist=json.dumps({"room_patient": True, "check_in_reviewed": True, "provider_ready": True}),
                provider_notified_at=start - timedelta(minutes=5),
            )
        )
    db.add(
        models.VisitNote(
            appointment_id=a.id,
            worker_id=doctor.id,
            seen_at=start + timedelta(minutes=2),
            note="Annual physical examination. Feels well, exercises three times a week. Full exam unremarkable "
            "apart from borderline blood pressure (dummy note).",
            assessment="Healthy adult. Borderline blood pressure.",
            plan="Home blood pressure readings; labs before the next visit; flu shot in the autumn (dummy plan).",
        )
    )
    db.add(
        models.VisitDiagnosis(
            appointment_id=a.id,
            patient_id=pat.id,
            worker_id=doctor.id,
            description="Encounter for general adult medical examination",
            code="Z00.00",
            is_primary=True,
            created_at=start + timedelta(minutes=20),
        )
    )
    db.flush()
    log("seed: annual physical %s (completed)", a.number)
    return a


def seed_reports(db: Session, log: Log = _quiet) -> int:
    """Immunization histories, Pat's annual physical and her two reports (see the module docstring)."""
    if config.IS_PROD:
        return 0
    today = _now().astimezone(appointments.office_tz()).date()
    nurse = _worker(db, email_for("nurse"))
    doctor = _worker(db, email_for("doctor"))
    assistant = _worker(db, email_for("assistant"))
    n = 0
    for spec_patient in PATIENTS:
        p = db.execute(
            select(models.Patient).where(
                models.Patient.first_name_norm == patients.normalize_name(spec_patient["first_name"]),
                models.Patient.last_name_norm == patients.normalize_name(spec_patient["last_name"]),
                models.Patient.date_of_birth == spec_patient["date_of_birth"],
            )
        ).scalar_one_or_none()
        history = _immunization_history(today).get(spec_patient["first_name"], [])
        if p is None or not history or immunizations.of_patient(db, p):
            continue
        for spec in history:
            spec = dict(spec)
            who = spec.pop("worker", None)
            source = spec.pop("source")
            body = schemas.ImmunizationIn(**spec, worker_id=nurse.id if who == "nurse" and nurse else None)
            immunizations.add(db, p, body, entered_by=None, source="seed" if source == "external" else "office")
            n += 1
        log("seed: %d immunizations for %s %s", len(history), p.first_name, p.last_name)
    db.commit()

    pat = db.execute(select(models.Patient).where(models.Patient.email_norm == email_for("patient"))).scalars().first()
    admin = db.execute(select(models.Account).where(models.Account.email == email_for("admin"))).scalar_one_or_none()
    patient_account = _patient_account(db)
    if pat is None or doctor is None or admin is None:
        return n
    if _appointment(db, pat, ANNUAL_REASON, upcoming=False) is None:
        _annual_visit(db, pat, doctor, assistant, log)
        db.commit()
    if db.execute(select(models.PatientReport).where(models.PatientReport.patient_id == pat.id)).first():
        return n
    doctor_account = db.get(models.Account, doctor.account_id) if doctor.account_id else None
    now = _now()

    # The immunization record for the employer: requested in the app ten days ago, signed the next day.
    r = patient_reports.create_request(
        db,
        pat,
        kind="immunization",
        purpose="work",
        purpose_detail="Example Corp onboarding (dummy)",
        needed_by=None,
        note="My new employer asks for my vaccination record (dummy request).",
        via="patient",
        actor=patient_account,
    )
    r.requested_at = now - timedelta(days=10)
    patient_reports.generate(db, r, doctor.id, None, admin)
    r.findings = schemas.ReportFindings(
        contraindications="None known.",
        vaccines_due="Influenza every autumn. Td/Tdap booster due in 2029. (Dummy.)",
    ).model_dump_json()
    if doctor_account is not None:
        patient_reports.sign(
            db,
            r,
            doctor,
            doctor_account,
            schemas.ReportSign(
                signature=schemas.PortalSignature(
                    signer_name=f"{doctor.first_name} {doctor.last_name}",
                    image_png=signature_png(f"{doctor.first_name} {doctor.last_name}"),
                    consent=True,
                ),
                send_to_patient=False,
            ),
        )
        r.signed_at = now - timedelta(days=9)
        r.generated_at = now - timedelta(days=9, hours=2)
        r.verification = patient_reports._verification(r)
        r.sent_at = r.signed_at + timedelta(minutes=1)
        r.sent_channels = "email"
        r.sent_to = f"email:{ppm.mask_email(pat.email)}" if pat.email else ""
        r.delivery_note = "dummy data (seed) — not actually sent"
    log("seed: %s — immunization record for Pat (%s)", r.number, r.status)

    # The annual health examination report for the running club: asked for yesterday, not prepared yet.
    q = patient_reports.create_request(
        db,
        pat,
        kind="annual_exam",
        purpose="sports",
        purpose_detail="Exampleville Running Club (dummy)",
        needed_by=today + timedelta(days=14),
        note="The club needs the signed form before the season starts (dummy request).",
        via="patient",
        actor=patient_account,
    )
    q.requested_at = now - timedelta(days=1)
    db.commit()
    log("seed: %s — annual health examination report requested by Pat (waiting for the office)", q.number)
    return n + 2


# ---------------------------------------------------------------------------- the family (My medicines)

SKY = {
    "first_name": "Sky",
    "last_name": "Dummy",
    "date_of_birth": date(2017, 4, 9),
    "sex": "female",
    "city": "Exampleville",
    "insurance_provider": "Example Health (dummy)",
    "insurance_plan_name": "PPO Silver (dummy)",
    "insurance_member_id": "DUMMY-INS-0304",
    "insurance_group_number": "DUMMY-GRP-01",
    "insurance_policyholder_name": "Pat Dummy",
    "insurance_policyholder_relationship": "parent",
    "emergency_contact_name": "Pat Dummy",
    "emergency_contact_phone": "+1 555 010 0301",
}
SKY_HEALTH_ITEMS = (
    ("condition", "Asthma", "mild intermittent — since 2021"),
    ("condition", "Seasonal allergies", "spring pollen"),
    ("allergy", "Amoxicillin", "hives"),
)
SKY_REASON = "Asthma check (dummy past visit)."
ALBUTEROL_INSTRUCTIONS = (
    "Use it with the spacer. Shake the inhaler before each puff and wait one minute between puffs. "
    "If it is needed more than twice a week, or does not help, call the office. (Dummy.)"
)
CETIRIZINE_INSTRUCTIONS = (
    "Measure it with the dosing syringe that comes with it — not a kitchen spoon. It can make you a bit "
    "sleepy, so the evening is best. (Dummy.)"
)


def _sky_visit(db: Session, sky: models.Patient, doctor: models.Worker, log: Log) -> models.Appointment:
    """Sky's asthma check about six weeks ago: two diagnoses, two prescriptions (an inhaler, sent to the
    pharmacy, and an allergy medicine) with instructions for the patient, and the signed visit summary."""
    today = _now().astimezone(appointments.office_tz()).date()
    day = _weekday_on_or_before(today - timedelta(days=42))
    start = _office_moment(day, 9)
    a = models.Appointment(
        patient_id=sky.id,
        provider_id=doctor.id,
        start_at=start,
        end_at=start + appointments.slot_length(),
        status="completed",
        reason=SKY_REASON,
        payment="insurance",
        coverage_status="active",
        confirmed_at=start - timedelta(days=1),
        confirmed_via="link",
        created_at=start - timedelta(days=10),
    )
    db.add(a)
    db.flush()
    db.add(
        models.VisitNote(
            appointment_id=a.id,
            worker_id=doctor.id,
            seen_at=start + timedelta(minutes=2),
            note="Wheezing with colds and when running, about once a week; sneezing and itchy eyes since the "
            "spring. Lungs clear today (dummy note).",
            assessment="Mild intermittent asthma. Seasonal allergic rhinitis.",
            plan="Albuterol with a spacer as needed; cetirizine in the evening through the pollen season; "
            "recheck in three months (dummy plan).",
        )
    )
    asthma = models.VisitDiagnosis(
        appointment_id=a.id,
        patient_id=sky.id,
        worker_id=doctor.id,
        description="Mild intermittent asthma, uncomplicated",
        code="J45.20",
        is_primary=True,
        created_at=start + timedelta(minutes=12),
    )
    rhinitis = models.VisitDiagnosis(
        appointment_id=a.id,
        patient_id=sky.id,
        worker_id=doctor.id,
        description="Allergic rhinitis due to pollen",
        code="J30.1",
        created_at=start + timedelta(minutes=13),
    )
    db.add_all([asthma, rhinitis])
    db.flush()
    doctor_account = db.get(models.Account, doctor.account_id) if doctor.account_id else None
    pharmacy = db.execute(select(models.Pharmacy).where(models.Pharmacy.email.is_not(None))).scalars().first()
    scripts = (
        (
            asthma,
            schemas.PrescriptionItemIn(
                drug="Albuterol HFA",
                strength="90 mcg/puff",
                form="inhaler",
                sig="Inhale 2 puffs by mouth every 4 to 6 hours as needed for wheezing or shortness of breath",
                quantity=1,
                quantity_unit="inhaler",
                refills=2,
                patient_instructions=ALBUTEROL_INSTRUCTIONS,
            ),
            True,
        ),
        (
            rhinitis,
            schemas.PrescriptionItemIn(
                drug="Cetirizine",
                strength="5 mg/5 mL",
                form="oral solution",
                sig="Take 5 mL by mouth once daily in the evening",
                quantity=150,
                quantity_unit="mL",
                refills=1,
                patient_instructions=CETIRIZINE_INSTRUCTIONS,
            ),
            False,
        ),
    )
    for minutes, (diagnosis, item, sent) in enumerate(scripts, start=20):
        rx = prescriptions.issue(
            db,
            a,
            doctor,
            schemas.PrescriptionIn(
                items=[item], diagnosis_id=diagnosis.id, note_to_pharmacist="Dummy prescription (seed data)."
            ),
            doctor_account,
        )
        rx.issued_at = start + timedelta(minutes=minutes)
        rx.verification = prescriptions._verification(rx)
        if sent and pharmacy is not None:
            db.add(
                models.PrescriptionDelivery(
                    prescription_id=rx.id,
                    method="email",
                    pharmacy_id=pharmacy.id,
                    destination="d•••@example.com",
                    status="sent",
                    note="dummy data",
                    created_at=rx.issued_at + timedelta(minutes=1),
                )
            )
    if pharmacy is not None:
        sky.preferred_pharmacy_id = pharmacy.id
        sky.preferred_pharmacy_set_at = start + timedelta(minutes=19)
        sky.preferred_pharmacy_set_by = f"{appointments._provider_name(doctor)} at {a.number}"
    db.flush()
    log("seed: past visit %s for Sky (completed, two prescriptions)", a.number)
    return a


def seed_family(db: Session, log: Log = _quiet) -> int:
    """Sky Dummy, Pat's daughter, on the patient account too — so the dashboard's "My medicines" has a tab for
    each of them — with her asthma check and its prescriptions; and the patient instructions on Pat's
    lisinopril (databases seeded before instructions existed get them here)."""
    if config.IS_PROD:
        return 0
    account = _patient_account(db)
    doctor = _worker(db, email_for("doctor"))
    pat = db.execute(select(models.Patient).where(models.Patient.email_norm == email_for("patient"))).scalars().first()
    if account is None or pat is None:
        return 0
    n = 0
    for item in db.execute(
        select(models.PrescriptionItem)
        .join(models.Prescription, models.Prescription.id == models.PrescriptionItem.prescription_id)
        .where(
            models.Prescription.patient_id == pat.id,
            models.Prescription.note_to_pharmacist == "Dummy prescription (seed data).",
            models.PrescriptionItem.drug == "Lisinopril",
            models.PrescriptionItem.patient_instructions.is_(None),
        )
    ).scalars():
        item.patient_instructions = LISINOPRIL_INSTRUCTIONS
    sky, created = _patient(db, SKY)
    if sky.account_id is None:
        sky.account_id = account.id
    if created:
        if patients.has_insurance(sky):
            patients.verify_insurance(db, sky, "seed")
        for kind, name, detail in SKY_HEALTH_ITEMS:
            health_records.add(db, sky, kind, name, detail, source="seed")
        amoxicillin = health_records.add(
            db,
            sky,
            "medication",
            "Amoxicillin",
            "250 mg/5 mL oral suspension — 5 mL by mouth twice daily for 10 days",
            source="seed",
            note="Stopped after three days — hives (now on the allergy list). (Dummy.)",
        )
        amoxicillin.status = "inactive"
        log("seed: patient %s %s (%s) — on the patient account with Pat", sky.first_name, sky.last_name, sky.mrn)
        n += 1
    db.commit()
    if doctor is not None and _appointment(db, sky, SKY_REASON, upcoming=False) is None:
        a = _sky_visit(db, sky, doctor, log)
        db.commit()
        _signed_summary(
            db,
            a,
            sky,
            doctor,
            log,
            instructions=(
                "Use the albuterol inhaler with the spacer when Sky wheezes or is short of breath — 2 puffs, "
                "at most every 4 hours.\n"
                "Give cetirizine 5 mL every evening while the pollen season lasts.\n"
                "Keep the inhaler in the school bag and give the school nurse the asthma action plan.\n"
                "Call the office if the inhaler is needed more than twice a week. (Dummy instructions.)"
            ),
            follow_up="Asthma check in three months — book it from My appointments. (Dummy.)",
            sent_to=account.email,
        )
        db.commit()
        n += 1
    return n


def seed_all(db: Session, log: Log = _quiet, *, reset_passwords: bool = False) -> None:
    """Everything, in the order main.py's lifespan uses."""
    seed(db, log, reset_passwords=reset_passwords)
    seed_ads(db, log)
    seed_workers(db, log, reset_passwords=reset_passwords)
    seed_applications(db, log)
    seed_payment_methods(db, log)
    seed_pharmacies(db, log)
    seed_patients(db, log)
    seed_assignments(db, log)
    seed_reports(db, log)
    seed_family(db, log)


# ---------------------------------------------------------------------------- command line


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create / refresh the dummy data (development and tests).")
    parser.add_argument("--ads", action="store_true", help="only the sponsored-content campaigns")
    parser.add_argument("--force", action="store_true", help="with --ads: also when APP_ENV=prod")
    parser.add_argument("--promote", metavar="EMAIL", help="give an existing account the administrator role")
    parser.add_argument("--demote", metavar="EMAIL", help="take the administrator role away")
    parser.add_argument("--keep-passwords", action="store_true", help="do not reset the dummy accounts' passwords")
    args = parser.parse_args(argv)

    from database import SessionLocal, init_db

    init_db()

    def log(fmt: str, *values) -> None:
        print(fmt % values if values else fmt, flush=True)

    with SessionLocal() as db:
        if args.promote or args.demote:
            return 0 if set_role(db, args.promote or args.demote, bool(args.promote), log) else 1
        if args.ads:
            seed_ads(db, log, force=args.force)
            return 0
        if config.IS_PROD:
            print("APP_ENV=prod: the dummy accounts and records are never created in production.", file=sys.stderr)
            return 1
        seed_all(db, log, reset_passwords=not args.keep_passwords)
    print("\nDummy accounts (password Dummy-<Kind>-pass-123):")
    for kind in KINDS + STAFF_KINDS:
        print(f"  {email_for(kind):32} {password_for(kind)}")
    print(
        f"  dummy-twofa@: authenticator secret {TWOFA_SECRET} · recovery codes {', '.join(TWOFA_RECOVERY_CODES[:3])} …"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
