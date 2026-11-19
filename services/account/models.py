"""Database model of the Account & Sync Service.

Only what billing and multi-device sync strictly need (PLATFORM_DESIGN.md §1.4). No content:
``sync_ops.ciphertext`` / ``sync_snapshots.ciphertext`` are opaque bytes encrypted on the
user's device with a key the server never sees. ``tests/test_privacy.py`` guards this list.

The sponsored-content tables (``ad_campaigns``, ``ad_daily_stats``) hold *our* creatives and
anonymous daily totals — by design there is no column that could identify a user, device or
request (no account id, device id, IP address, user agent, timestamp per event).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    Time,
    TypeDecorator,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid.uuid4())


class UTCDateTime(TypeDecorator):
    """Naive-UTC in the database, timezone-aware UTC in Python (portable SQLite/Postgres)."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value

    def process_result_value(self, value, dialect):
        return None if value is None else value.replace(tzinfo=timezone.utc)


class Base(DeclarativeBase):
    pass


ROLE_USER = "user"
ROLE_ADMIN = "admin"  # production support & management (membership.AdminMembership)
# A patient of the office: the only role the patients' web app creates or signs in (auth.py
# audiences). Sees their own patient records and appointments (/me/appointments); never the
# staff / office administration dashboards.
ROLE_PATIENT = "patient"
# A member of staff (doctor, nurse, …) signed in to the staff dashboard: the account is linked to
# their ``Worker`` record (staff_accounts.py) and sees their own appointments and availability —
# never the manager tabs (those need ROLE_ADMIN), never the patients' app.
ROLE_STAFF = "staff"
ROLES = (ROLE_USER, ROLE_ADMIN, ROLE_PATIENT, ROLE_STAFF)


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    # user | admin. Membership type (free/paid/admin) is derived: role first, then subscription.
    role: Mapped[str] = mapped_column(String(16), nullable=False, default=ROLE_USER)
    # Argon2/PBKDF2 of the client-derived *auth key* (never the password). Salted again here.
    # "" = no password yet: the account was created by "Sign in with Google/Facebook" (oauth.py)
    # and has not set its encryption passphrase; such an account cannot sign in with a password.
    auth_salt: Mapped[str] = mapped_column(String(64), nullable=False)
    auth_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    # The user's data key, wrapped on the device with a key derived from the password.
    # Opaque to us; lets a second device unwrap it after proving the password. "" until a
    # sign-in-with-provider account has chosen its passphrase.
    wrapped_data_key: Mapped[str] = mapped_column(Text, nullable=False)
    kdf_params: Mapped[str] = mapped_column(Text, nullable=False, default="{}")  # pinned, versioned
    # Opt-in durable encrypted snapshot ("vault"); see sync_snapshots.
    vault_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Optional second factor (twofactor.py): a TOTP secret shared with the user's authenticator
    # app — set = 2FA on. ``totp_pending_secret`` lives only between "set up" and the first
    # correct code; ``totp_last_step`` refuses a replayed code; ``recovery_codes`` is a JSON
    # list of salted hashes of the unused one-time recovery codes.
    totp_secret: Mapped[str | None] = mapped_column(String(64), nullable=True)
    totp_enabled_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    totp_pending_secret: Mapped[str | None] = mapped_column(String(64), nullable=True)
    totp_last_step: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    recovery_codes: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    # The other two second factors: a code by e-mail (to ``email``) or by text message (to
    # ``phone_number``, E.164). A phone number is stored only while SMS codes are on — it is the
    # one piece of personal data beyond the e-mail, and the user opts into it.
    email_otp_enabled_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    sms_otp_enabled_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    phone_number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Where e-mail codes go when the user chose an address other than the sign-in e-mail (which
    # is also the key-derivation salt and therefore never changes). None = the account e-mail.
    otp_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    # Monotonic per-account sequence: the server is the master of op ordering.
    last_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    @property
    def two_factor_enabled(self) -> bool:
        return bool(self.totp_secret or self.email_otp_enabled_at or self.sms_otp_enabled_at)

    @property
    def has_password(self) -> bool:
        return bool(self.auth_hash)


class OAuthIdentity(Base):
    """A Google / Facebook identity that may sign in to an account (oauth.py). ``subject`` is
    the provider's stable user id; the e-mail is what the provider reported at link time (for
    display — the account's own e-mail stays the key)."""

    __tablename__ = "oauth_identities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(16), nullable=False)  # google | facebook
    subject: Mapped[str] = mapped_column(String(128), nullable=False)
    account_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    __table_args__ = (UniqueConstraint("provider", "subject", name="uq_oauth_identity"),)


class OneTimeCode(Base):
    """A code we sent by e-mail or text message and wait for (twofactor.py). One outstanding
    code per account and purpose — a new one replaces it. Only a keyed hash is kept; the row is
    deleted when the code is used and expires after TWO_FACTOR_CODE_TTL_SECONDS."""

    __tablename__ = "one_time_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    purpose: Mapped[str] = mapped_column(String(16), nullable=False)  # login | setup | verify
    channel: Mapped[str] = mapped_column(String(8), nullable=False)  # email | sms
    # Where it went; for "setup" the address/number being confirmed (becomes the account's on success).
    destination: Mapped[str] = mapped_column(String(320), nullable=False)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)

    __table_args__ = (UniqueConstraint("account_id", "purpose", name="uq_one_time_code_purpose"),)


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    account_id: Mapped[str] = mapped_column(String(36), ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    platform: Mapped[str] = mapped_column(String(40), nullable=False, default="web")
    client_version: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    name_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    cursor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)  # last seq applied
    token_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=1)  # bump = sign out
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class Subscription(Base):
    __tablename__ = "subscriptions"

    account_id: Mapped[str] = mapped_column(String(36), ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_customer_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    provider_subscription_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    plan: Mapped[str] = mapped_column(String(32), nullable=False, default="free")  # free|pro|lifetime
    # trialing | active | past_due | canceled | none
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="none")
    current_period_end: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow)


class Payment(Base):
    """One charge at the provider (and what became of it): the ledger behind the account
    screen's payment history, the refund on cancellation and the failure notices. Money is kept
    in minor units (cents) with its currency; never card data."""

    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    # Stripe payment intent / charge, PayPal capture / sale — what a refund is issued against.
    provider_payment_id: Mapped[str] = mapped_column(String(128), nullable=False)
    plan: Mapped[str] = mapped_column(String(32), nullable=False)  # pro_monthly | pro_yearly | lifetime
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    # succeeded | failed | refunded | partially_refunded
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="succeeded")
    refunded_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    refund_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    refunded_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(256), nullable=True)
    # The period this payment bought (subscriptions) — the basis of a prorated refund.
    period_start: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    period_end: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    paid_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (UniqueConstraint("provider", "provider_payment_id", name="uq_payment_provider_id"),)


class BillingEvent(Base):
    __tablename__ = "billing_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_event_id: Mapped[str] = mapped_column(String(128), nullable=False)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    received_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    __table_args__ = (UniqueConstraint("provider", "provider_event_id", name="uq_billing_event"),)


class AuditLog(Base):
    """Who did what to which account from the admin panel (membership changes, sign-outs,
    deletions, role changes). Metadata only — never user content."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    actor_account_id: Mapped[str | None] = mapped_column(String(36), nullable=True)  # None = pipeline/CLI
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target_account_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="")  # JSON, small


class Patient(Base):
    """A patient of the medical office, registered through the public intake page (patients.py).

    This is *operator* data (the practice's patient roster), not end-user app content: the
    office needs one shared roster to tell a returning patient from a new one, so it lives
    here and not in the device-local database. Only what the intake form asks for is stored;
    the ``*_norm`` columns are lower-cased/accent-stripped copies used purely for matching.
    """

    __tablename__ = "patients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    date_of_birth: Mapped[date] = mapped_column(Date, nullable=False)
    sex: Mapped[str | None] = mapped_column(String(16), nullable=True)  # female | male | other | unspecified
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    address_line: Mapped[str | None] = mapped_column(String(200), nullable=True)
    city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    state: Mapped[str | None] = mapped_column(String(100), nullable=True)
    postal_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    country: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Insurance (the intake page's "Insurance" section; all optional — self-pay patients leave
    # it blank). The card photos live in PatientInsuranceCard.
    insurance_provider: Mapped[str | None] = mapped_column(String(120), nullable=True)
    insurance_plan_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    insurance_member_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    insurance_group_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    insurance_policyholder_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # self | spouse | parent | child | other — who the policy is in the name of
    insurance_policyholder_relationship: Mapped[str | None] = mapped_column(String(16), nullable=True)
    emergency_contact_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    emergency_contact_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reason_for_visit: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The pharmacy the patient uses — prescriptions go there by default (prescriptions.py). Asked at a
    # visit by the provider (or set by the office); when and by whom it was last confirmed / changed.
    preferred_pharmacy_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("pharmacies.id", ondelete="SET NULL"), nullable=True
    )
    preferred_pharmacy_set_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    preferred_pharmacy_set_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # The patient account (role = patient) this record belongs to, once claimed: by the same
    # e-mail automatically, or by name + date of birth from the "My appointments" page.
    account_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Matching keys (patients.normalize_*): never shown, only compared.
    first_name_norm: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name_norm: Mapped[str] = mapped_column(String(100), nullable=False)
    email_norm: Mapped[str | None] = mapped_column(String(320), nullable=True, index=True)
    phone_norm: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (Index("ix_patients_identity", "last_name_norm", "first_name_norm", "date_of_birth"),)

    @property
    def mrn(self) -> str:
        """Medical record number shown to staff and patients, e.g. ``P-000042``."""
        return f"P-{self.id:06d}"


INSURANCE_CARD_SIDES = ("front", "back")
INSURANCE_RELATIONSHIPS = ("self", "spouse", "parent", "child", "other")


class PatientInsuranceCard(Base):
    """A photo of the patient's insurance card — one row per side (front / back), uploaded or
    taken with the camera on the intake page (patients.py, insurance.py).

    The image bytes are stored here rather than on disk so that the roster is one database
    (backups, the Docker volume, PostgreSQL in production) and nothing is left behind when a
    patient row is deleted (cascade). The intake page downsizes photos before upload; the
    server caps them at ``insurance.MAX_CARD_BYTES`` and only accepts JPEG / PNG / WebP.
    Only administrators can read them back (``GET /patients/{id}/insurance-card/{side}``).
    """

    __tablename__ = "patient_insurance_cards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    patient_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("patients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    side: Mapped[str] = mapped_column(String(8), nullable=False)  # front | back
    content_type: Mapped[str] = mapped_column(String(32), nullable=False)  # image/jpeg | image/png | image/webp
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)

    __table_args__ = (UniqueConstraint("patient_id", "side", name="uq_patient_insurance_card_side"),)


VERIFICATION_TRIGGERS = ("registration", "staff", "seed", "patient", "booking")


class InsuranceVerification(Base):
    """One eligibility & benefits check of a patient's insurance (insurance_verification.py):
    what the payer — or the built-in fake — answered, as a row per check so the desk can see
    the history. ``patients.latest_verification`` picks the newest. Amounts are integer cents."""

    __tablename__ = "patient_insurance_verifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    patient_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("patients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # insurance_verification.STATUSES
    message: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    triggered_by: Mapped[str] = mapped_column(String(16), nullable=False)  # registration | staff | seed | patient
    provider: Mapped[str] = mapped_column(String(32), nullable=False)  # fake | availity | …
    reference: Mapped[str | None] = mapped_column(String(120), nullable=True)
    payer_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    plan_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    plan_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    member_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    group_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    coverage_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    coverage_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    copay_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    specialist_copay_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    deductible_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    deductible_met_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    deductible_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    coinsurance_percent: Mapped[int | None] = mapped_column(Integer, nullable=True)
    out_of_pocket_max_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    out_of_pocket_met_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    referral_required: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    referral_notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    prior_auth_required: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    prior_auth_notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    raw: Mapped[str] = mapped_column(Text, nullable=False, default="{}")  # the provider's answer, JSON
    checked_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


# ==================== Patient payment methods (patient_payment_methods.py) ====================

PAYMENT_METHOD_KINDS = ("card", "stripe", "paypal", "bank")
CARD_BRANDS = ("visa", "mastercard", "amex", "discover", "diners", "jcb", "unionpay", "other")
BANK_ACCOUNT_TYPES = ("checking", "savings")
# A method is usable ("active") only after a small-amount authorization succeeded
# (patient_payment_methods.validate): pending = not checked yet (or the detail changed since),
# failed = the last authorization was declined / could not be made.
PAYMENT_METHOD_STATUSES = ("pending", "active", "failed")
PAYMENT_VALIDATION_RESULTS = ("authorized", "declined", "error")
PAYMENT_VALIDATION_TRIGGERS = ("patient", "staff", "system")


class PatientPaymentMethod(Base):
    """A way a patient wants to pay the office (copays, self-pay visits), kept on their patient
    *account* (role = patient) and managed from the patient dashboard.

    Deliberately **no payment detail lands here in full** — every kind is tokenized: the
    secret part (card number, bank account number, the e-mail address of a Stripe / PayPal
    wallet) is validated on arrival and handed to the vault in exchange for a ``provider_token``
    (the payment provider's reference — the built-in fake's outside prod). What stays is just
    enough for the patient to recognise the entry: brand + last four digits + expiry of a card,
    bank name + masked routing number + last four digits of an account, a masked e-mail
    (``pa•••@example.com``) of a wallet — plus a keyed ``fingerprint`` (HMAC) of the secret so
    the same card / account / wallet is not added twice. Rows go with the account (cascade).
    """

    __tablename__ = "patient_payment_methods"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # PAYMENT_METHOD_KINDS
    label: Mapped[str | None] = mapped_column(String(60), nullable=True)  # "My Visa", "Joint account", …
    holder_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # card
    card_brand: Mapped[str | None] = mapped_column(String(16), nullable=True)  # CARD_BRANDS
    card_last4: Mapped[str | None] = mapped_column(String(4), nullable=True)
    card_exp_month: Mapped[int | None] = mapped_column(Integer, nullable=True)
    card_exp_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    billing_postal_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # stripe | paypal: the wallet's e-mail address, masked ("pa•••@example.com") — never in full
    wallet_email_masked: Mapped[str | None] = mapped_column(String(320), nullable=True)
    # bank
    bank_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    bank_account_type: Mapped[str | None] = mapped_column(String(16), nullable=True)  # BANK_ACCOUNT_TYPES
    bank_routing_last4: Mapped[str | None] = mapped_column(String(4), nullable=True)
    bank_account_last4: Mapped[str | None] = mapped_column(String(4), nullable=True)
    # The vault's reference for the tokenized secret (fake: "pm_fake_…", "ba_fake_…", "wl_fake_…")
    # and a keyed hash of it (duplicate detection; not reversible without TOKEN_SECRET).
    provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    provider_token: Mapped[str | None] = mapped_column(String(128), nullable=True)
    fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # Validation by a small-amount authorization (PaymentValidation rows hold every attempt).
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")  # PAYMENT_METHOD_STATUSES
    validated_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (Index("ix_patient_payment_methods_account_kind", "account_id", "kind"),)


class PaymentValidation(Base):
    """One attempt to validate a payment method: a small amount (config
    PAYMENT_VALIDATION_AMOUNT_CENTS) is authorized against the vault token and released at once
    — nothing is charged. The newest row decides the method's ``status``; the history stays so
    the office can see when and why a method stopped working — and outlives the method itself,
    like every payment (PatientPayment). Triggered when the method is added or its detail
    changes, by the patient ("Validate again") or by the office."""

    __tablename__ = "patient_payment_validations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # The method is referenced *and* snapshotted (account, kind, masked display): the history of a
    # removed method stays — the row is detached (SET NULL), never deleted with it.
    method_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("patient_payment_methods.id", ondelete="SET NULL"), nullable=True, index=True
    )
    account_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    method_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    method_display: Mapped[str] = mapped_column(String(120), nullable=False)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    result: Mapped[str] = mapped_column(String(16), nullable=False)  # PAYMENT_VALIDATION_RESULTS
    message: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    reference: Mapped[str | None] = mapped_column(String(120), nullable=True)  # the vault's authorization id
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    triggered_by: Mapped[str] = mapped_column(String(16), nullable=False)  # PAYMENT_VALIDATION_TRIGGERS
    actor_account_id: Mapped[str | None] = mapped_column(String(36), nullable=True)  # the staff member, if any
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


# ==================== Patient payments (patient_payments.py) ====================

# check_in = the estimated cost not covered by insurance, pre-authorized when the patient signs
# the check-in page (check_ins.py); it replaces the copay / self-pay hold placed at booking.
PATIENT_PAYMENT_KINDS = ("copay", "self_pay", "statement", "other", "check_in")
# authorized = a hold (booking) · captured = charged · released = hold let go (cancellation) ·
# declined = the vault refused · refunded / partially_refunded = money given back
PATIENT_PAYMENT_STATUSES = ("authorized", "captured", "released", "declined", "refunded", "partially_refunded")


class PatientPayment(Base):
    """One movement of money against a patient's payment method: the copay / self-pay hold
    placed when a visit is booked (captured when the visit is completed, released when it is
    canceled), a statement charged by the office, a refund. The ledger behind the patients'
    "Payment history" tab and the office's view of it. Amounts are minor units (cents).

    The method is referenced and *snapshotted* (kind + masked display) so the history still
    reads well after the method was removed. Never any payment detail — the vault's references
    only (``reference``, ``refund_reference``)."""

    __tablename__ = "patient_payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    patient_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("patients.id", ondelete="SET NULL"), nullable=True, index=True
    )
    method_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("patient_payment_methods.id", ondelete="SET NULL"), nullable=True
    )
    method_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    method_display: Mapped[str] = mapped_column(String(120), nullable=False)  # "Visa •••• 4242 · 12/2031"
    appointment_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("appointments.id", ondelete="SET NULL"), nullable=True, index=True
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # PATIENT_PAYMENT_KINDS
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    status: Mapped[str] = mapped_column(String(24), nullable=False)  # PATIENT_PAYMENT_STATUSES
    refunded_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    description: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    message: Mapped[str] = mapped_column(String(300), nullable=False, default="")  # the vault's last word
    reference: Mapped[str | None] = mapped_column(String(120), nullable=True)
    refund_reference: Mapped[str | None] = mapped_column(String(120), nullable=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    created_by: Mapped[str] = mapped_column(String(16), nullable=False)  # patient | staff | system
    actor_account_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow)
    captured_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    refunded_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


WORKER_ROLES = (
    "doctor",
    "nurse",
    "physician_assistant",
    "medical_assistant",
    "technician",
    "receptionist",
    "administrator",
    "other",
)
# Medical assistants (MA) are not booked by patients: the office assigns one to an appointment
# (assignments.py) to prepare the visit — room the patient, take the vitals, review the
# medications / allergies and the check-in answers, tell the provider the patient is ready.
ROLE_MEDICAL_ASSISTANT = "medical_assistant"
# The office's standard week: Monday–Friday, 9 AM to 5 PM (workers.py applies it to new staff).
STANDARD_WORK_START = time(9, 0)
STANDARD_WORK_END = time(17, 0)
STANDARD_WORK_DAYS = (0, 1, 2, 3, 4)  # Monday=0 … Sunday=6 (Python's weekday())


class Worker(Base):
    """A member of staff (doctor, nurse, …) registered through the staff page (workers.py).

    Operator data like ``Patient``: the office's roster. Only what the form asks for; the
    ``*_norm`` columns are matching keys (lower-cased, accent-stripped).
    """

    __tablename__ = "workers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)  # WORKER_ROLES
    specialty: Mapped[str | None] = mapped_column(String(120), nullable=True)
    license_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    employment_type: Mapped[str | None] = mapped_column(String(16), nullable=True)  # full_time | part_time | locum
    emergency_contact_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    emergency_contact_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    first_name_norm: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name_norm: Mapped[str] = mapped_column(String(100), nullable=False)
    email_norm: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    phone_norm: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    license_norm: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # The staff dashboard account of this worker (role = staff), once they created one — proven by
    # a code sent to this record's work e-mail (staff_accounts.py). One account per worker.
    account_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True, unique=True
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (Index("ix_workers_name", "last_name_norm", "first_name_norm"),)

    @property
    def staff_number(self) -> str:
        """Shown to the worker at registration and used to identify them on the availability page."""
        return f"W-{self.id:06d}"


class WorkerAvailability(Base):
    """One row per worker and weekday: is the person in, and from when to when. New staff get
    the office's standard week (Mon–Fri 9–5); the worker changes it on the availability page."""

    __tablename__ = "worker_availability"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    worker_id: Mapped[int] = mapped_column(Integer, ForeignKey("workers.id", ondelete="CASCADE"), nullable=False)
    weekday: Mapped[int] = mapped_column(Integer, nullable=False)  # 0 = Monday … 6 = Sunday
    available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    start_time: Mapped[time] = mapped_column(Time, nullable=False, default=STANDARD_WORK_START)
    end_time: Mapped[time] = mapped_column(Time, nullable=False, default=STANDARD_WORK_END)
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)  # "afternoons at the clinic", …
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (UniqueConstraint("worker_id", "weekday", name="uq_worker_weekday"),)


# ==================== Appointments (appointments.py) ====================

APPOINTMENT_STATUSES = ("scheduled", "canceled", "completed", "no_show")
# How a patient was checked in: from their patient account, the appointment link, the lookup page
# (name + date of birth) — all the patients' app — or by the office (dashboard).
CHECK_IN_CHANNELS = ("account", "link", "identity", "staff")
# Staff a patient can book with (the others — receptionists, administrators — have no slots).
APPOINTMENT_PROVIDER_ROLES = ("doctor", "nurse", "physician_assistant")
APPOINTMENT_SLOT_MINUTES = 30  # default; config.APPOINTMENT_SLOT_MINUTES overrides at runtime
APPOINTMENT_MAX_DAYS_AHEAD = 90  # default; config.APPOINTMENT_MAX_DAYS_AHEAD overrides at runtime


class Appointment(Base):
    """A booked visit: one registered patient with one provider (a ``Worker`` in
    APPOINTMENT_PROVIDER_ROLES) at a time slot inside the provider's weekly availability.

    Operator data like ``Patient`` and ``Worker`` — the office's agenda. A slot can hold one
    *scheduled* appointment per provider (partial unique index ``uq_appointment_slot``):
    canceled rows stay for the record and free the slot. Times are stored as naive UTC
    (UTCDateTime) like every other timestamp here; the office's local time zone is applied
    when slots are computed (appointments.py).
    """

    __tablename__ = "appointments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    patient_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("patients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("workers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    start_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    end_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="scheduled")  # APPOINTMENT_STATUSES
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)  # staff-only remarks
    # Who pays (appointments.coverage_for): "insurance" when the coverage check said active for
    # the day of the visit, "self_pay" when the patient agreed to pay themselves instead.
    payment: Mapped[str] = mapped_column(String(16), nullable=False, default="insurance")
    coverage_status: Mapped[str | None] = mapped_column(String(16), nullable=True)  # the check's status
    coverage_verification_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("patient_insurance_verifications.id", ondelete="SET NULL"), nullable=True
    )
    self_pay_agreed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    canceled_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    canceled_by: Mapped[str | None] = mapped_column(String(16), nullable=True)  # patient | staff
    # The patient's link (appointments.send_link): review the visit, add it to a calendar, cancel.
    # ``token_hash`` is what the link's token is looked up by; ``link`` keeps the full address so
    # the office can hand it over by hand; the ``link_*`` columns record how it was delivered
    # (channels, masked destinations, provider/fake notes) and whether it was opened.
    token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    link: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    link_channels: Mapped[str] = mapped_column(String(64), nullable=False, default="")  # "email,sms"
    link_sent_to: Mapped[str] = mapped_column(String(400), nullable=False, default="")  # masked destinations
    link_delivery_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    link_sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    link_opens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    link_last_opened_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    # The reminder (reminders.py): sent once, REMINDER_HOURS_BEFORE the visit, to what is on file.
    reminder_sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    reminder_channels: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    reminder_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # The patient confirmed they are coming (from the reminder's link, their account, or the desk).
    confirmed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    confirmed_via: Mapped[str | None] = mapped_column(String(16), nullable=True)  # link | account | staff
    # Check-in (appointments.check_in): the patient arrived / checked in from the app within
    # CHECK_IN_WINDOW_HOURS before the visit — or the office checked them in from the dashboard.
    checked_in_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    checked_in_via: Mapped[str | None] = mapped_column(String(16), nullable=True)  # CHECK_IN_CHANNELS
    checked_in_by: Mapped[str | None] = mapped_column(String(36), nullable=True)  # the staff member's account id
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        Index("ix_appointments_start", "start_at"),
        # One live booking per provider and start time (SQLite and PostgreSQL both support partial indexes).
        Index(
            "uq_appointment_slot",
            "provider_id",
            "start_at",
            unique=True,
            sqlite_where=text("status = 'scheduled'"),
            postgresql_where=text("status = 'scheduled'"),
        ),
    )

    @property
    def number(self) -> str:
        """Confirmation number shown to the patient, e.g. ``APT-000042``."""
        return f"APT-{self.id:06d}"


# ==================== Check-in (check_ins.py) ====================

# pending = the page is open / the link was sent, waiting for the patient's answers and signatures ·
# completed = processed: signed, the estimate pre-authorized, the appointment checked in ·
# failed = could not be processed (failure_code / failure_reason say why: expired, no payment
# method, declined, link not deliverable, visit no longer scheduled, …) · canceled = replaced by a
# newer check-in, called off by the office, or undone after completion.
CHECK_IN_STATUSES = ("pending", "completed", "failed", "canceled")


class AppointmentCheckIn(Base):
    """One check-in of an appointment: the questionnaire about the visit and the patient's
    signature on it, the estimated cost not covered by insurance and the patient's signature
    accepting financial responsibility, and the pre-authorization of that estimate on the payment
    method on file. Started by the patient in the app or by the office (then the page's link is
    sent to the patient and must be completed within CHECK_IN_LINK_MINUTES). The appointment is
    checked in only when a check-in completes; every attempt stays as a row (the history)."""

    __tablename__ = "appointment_check_ins"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    appointment_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("appointments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")  # CHECK_IN_STATUSES
    via: Mapped[str] = mapped_column(String(16), nullable=False)  # CHECK_IN_CHANNELS: who started it
    started_by: Mapped[str | None] = mapped_column(String(36), nullable=True)  # the staff member's account id
    # The page's address: /check-in/<token> in the patients' app (token → hash for lookups).
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    link: Mapped[str] = mapped_column(String(2048), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    # Started by the office: how the link went to the patient.
    link_channels: Mapped[str] = mapped_column(String(64), nullable=False, default="")  # "email,sms"
    link_sent_to: Mapped[str] = mapped_column(String(400), nullable=False, default="")  # masked destinations
    link_delivery_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    link_sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    opened_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    # The questionnaire and the patient's signature on it.
    questionnaire_version: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    answers: Mapped[str] = mapped_column(Text, nullable=False, default="{}")  # JSON {question id: answer}
    answers_signer_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    answers_signature_png: Mapped[str | None] = mapped_column(Text, nullable=True)  # data:image/png;base64,…
    answers_signed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    answers_esign_reference: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # The estimate (cents, with its breakdown as shown) and the financial-responsibility signature.
    estimate_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    estimate: Mapped[str] = mapped_column(Text, nullable=False, default="{}")  # JSON: lines, coverage, notes
    financial_signer_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    financial_signature_png: Mapped[str | None] = mapped_column(Text, nullable=True)
    financial_signed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    financial_esign_reference: Mapped[str | None] = mapped_column(String(120), nullable=True)
    esign_provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # The pre-authorization of the estimate (patient_payments, kind "check_in").
    payment_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("patient_payments.id", ondelete="SET NULL"), nullable=True
    )
    failure_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Once completed, the appointment's provider (the medical staff member) is told the patient
    # checked in — by e-mail / text to the staff record; the outcome is kept here.
    staff_notified_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    staff_notify_channels: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    staff_notified_to: Mapped[str] = mapped_column(String(400), nullable=False, default="")
    staff_notify_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    failed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    canceled_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow)


# ==================== Medical assistants (assignments.py) ====================

# assigned  = the office assigned a medical assistant, who has not taken the task yet ·
# accepted  = the assistant took the task ·
# completed = done: every duty confirmed (``duties``) with a note ·
# reported  = the assistant reported they cannot do it (``report_reason`` / ``report_note``) — the
#             office assigns the visit to someone else (``resolved_at``, ``replaced_by_id``) ·
# canceled  = withdrawn: reassigned / unassigned by the office, or the visit was canceled.
ASSIGNMENT_STATUSES = ("assigned", "accepted", "completed", "reported", "canceled")
ASSIGNMENT_OPEN_STATUSES = ("assigned", "accepted")
# Why an assistant cannot do a task (the report to the office).
ASSIGNMENT_REPORT_REASONS: tuple[tuple[str, str], ...] = (
    ("schedule_conflict", "Schedule conflict / not in at that time"),
    ("sick", "Sick / unwell"),
    ("emergency", "Personal emergency"),
    ("not_qualified", "Outside my training or scope"),
    ("other", "Other (explained in the note)"),
)
# What "done properly" means (assistant_review.py): every duty is done when the assistant completes
# the task — the vital signs typed in, every medication and allergy on the list cleared (each with
# its note), the check-in answers reviewed, the provider told.
ASSISTANT_DUTIES: tuple[tuple[str, str], ...] = (
    ("room_patient", "Patient roomed — identity confirmed (name and date of birth)"),
    ("vitals", "Vital signs recorded (blood pressure, pulse, temperature, weight)"),
    ("medications", "Medications reviewed with the patient — every item cleared"),
    ("allergies", "Allergies reviewed with the patient — every item cleared"),
    ("check_in_review", "Check-in answers reviewed with the patient"),
    ("provider_ready", "Provider told the patient is ready"),
)


class AppointmentAssignment(Base):
    """A medical assistant assigned by the office to prepare one appointment — a task with a
    life cycle (ASSIGNMENT_STATUSES). The office picks an assistant who is *available* for the
    visit (in that day, within their hours, not with another patient at that time); the assistant
    takes the task, then completes it by confirming every duty (ASSISTANT_DUTIES) — or reports
    that they cannot do it, and the office assigns someone else. Every row stays (the history);
    at most one task per appointment is open at a time (``uq_assignment_open``)."""

    __tablename__ = "appointment_assignments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    appointment_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("appointments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    worker_id: Mapped[int] = mapped_column(Integer, ForeignKey("workers.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="assigned")  # ASSIGNMENT_STATUSES
    instructions: Mapped[str | None] = mapped_column(String(1000), nullable=True)  # from the office
    assigned_by: Mapped[str | None] = mapped_column(String(36), nullable=True)  # administrator account id (None = seed)
    assigned_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    accepted_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    duties: Mapped[str] = mapped_column(Text, nullable=False, default="[]")  # JSON: the duty keys confirmed
    completion_note: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    reported_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    report_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)  # ASSIGNMENT_REPORT_REASONS
    report_note: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    # The office acted on a report (assigned someone else / took the assistant off the visit).
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    replaced_by_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("appointment_assignments.id", ondelete="SET NULL"), nullable=True
    )
    canceled_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    cancel_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # The review the assistant fills in (assistant_review.py): the vital signs (JSON: bp_systolic,
    # bp_diastolic, pulse, temperature + unit, weight + unit, note), the ticked parts and notes (JSON:
    # room_patient, no_medications, no_allergies, check_in_reviewed, check_in_note, provider_ready)
    # and the check-in whose answers the medication / allergy list was last built from.
    vitals: Mapped[str | None] = mapped_column(Text, nullable=True)
    checklist: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    review_check_in_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Once completed: the visit's provider is told the patient is ready (e-mail / text to the staff record).
    provider_notified_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    provider_notify_channels: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    provider_notified_to: Mapped[str] = mapped_column(String(400), nullable=False, default="")
    provider_notify_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # The message to the assistant about the new task (e-mail / text to the staff record).
    notified_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    notify_channels: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    notified_to: Mapped[str] = mapped_column(String(400), nullable=False, default="")
    notify_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        Index("ix_appointment_assignments_worker_status", "worker_id", "status"),
        # One open task (assigned / accepted) per appointment — two clerks racing cannot both assign.
        Index(
            "uq_assignment_open",
            "appointment_id",
            unique=True,
            sqlite_where=text("status IN ('assigned', 'accepted')"),
            postgresql_where=text("status IN ('assigned', 'accepted')"),
        ),
    )


# ==================== Medications and allergies (health_records.py, assistant_review.py) ====================

# medication · allergy (reviewed by the assistant at every visit) and the medical history: condition
# (past and current problems / diagnoses) · surgery (operations and procedures) · family_history ·
# social_history (smoking, alcohol, occupation, …).
HEALTH_ITEM_KINDS = ("medication", "allergy", "condition", "surgery", "family_history", "social_history")
REVIEWED_KINDS = ("medication", "allergy")
HISTORY_KINDS = ("condition", "surgery", "family_history", "social_history")
HEALTH_ITEM_STATUSES = ("active", "inactive")
# Where an entry of the chart came from: the office, the patient's check-in answers (confirmed by an
# assistant), an assistant (heard from the patient), the dummy data.
HEALTH_ITEM_SOURCES = ("office", "check_in", "assistant", "provider", "seed")  # provider: prescribed at a visit


class PatientHealthItem(Base):
    """One entry of a patient's medication list or allergy list — the records an assistant's review
    starts from (assistant_review.py). ``detail``: dose and how often for a medication, the reaction
    (and severity) for an allergy. An entry that is no longer current becomes ``inactive`` (kept, the
    note says why). ``note`` / ``last_reviewed_*``: the latest review with the patient."""

    __tablename__ = "patient_health_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    patient_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("patients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # HEALTH_ITEM_KINDS
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    detail: Mapped[str | None] = mapped_column(String(300), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="office")
    note: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    last_reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_reviewed_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("workers.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow)


# How a review item came about: on the chart, in the check-in answers, both, or added by the assistant.
REVIEW_ITEM_ORIGINS = ("record", "check_in", "both", "assistant")
# What the assistant decided: confirmed (current — as shown, possibly corrected) or removed (not
# current / entered in error). None = not cleared yet.
REVIEW_DECISIONS = ("confirmed", "removed")


class AssignmentReviewItem(Base):
    """One medication or allergy the assistant reviews with the patient during a task: prepopulated
    from the chart and from the patient's check-in answers, cleared one by one (``decision``) with a
    note each. On completion the decisions update the chart (PatientHealthItem)."""

    __tablename__ = "assignment_review_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    assignment_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("appointment_assignments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # HEALTH_ITEM_KINDS
    health_item_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("patient_health_items.id", ondelete="SET NULL"), nullable=True
    )
    origin: Mapped[str] = mapped_column(String(16), nullable=False)  # REVIEW_ITEM_ORIGINS
    # match · changed (the check-in differs from the chart) · new (only in the check-in) ·
    # not_mentioned (on the chart, missing from the patient's list) · said_none (the patient wrote "none")
    flag: Mapped[str | None] = mapped_column(String(16), nullable=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    detail: Mapped[str | None] = mapped_column(String(300), nullable=True)
    record_detail: Mapped[str | None] = mapped_column(String(300), nullable=True)  # the chart when prepared
    check_in_text: Mapped[str | None] = mapped_column(String(300), nullable=True)  # what the patient wrote
    decision: Mapped[str | None] = mapped_column(String(16), nullable=True)  # REVIEW_DECISIONS
    note: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


# ==================== The clinical record (clinical.py) ====================

RESULT_KINDS = ("lab", "imaging")
RESULT_STATUSES = ("pending", "preliminary", "final", "amended")


class PatientResult(Base):
    """A lab result (with its values: name, value, unit, reference range, flag) or an imaging report
    (the study and the radiologist's impression) — entered by the office when it arrives, read by the
    provider in the patient's chart."""

    __tablename__ = "patient_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    patient_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("patients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # RESULT_KINDS
    name: Mapped[str] = mapped_column(String(200), nullable=False)  # "Lipid panel", "Chest X-ray (2 views)"
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="final")  # RESULT_STATUSES
    ordered_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    resulted_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    performed_by: Mapped[str | None] = mapped_column(String(200), nullable=True)  # the lab / imaging center
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)  # the comment / the impression
    lab_values: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]"
    )  # JSON: [{name, value, unit, range, flag}]
    # The visit it was ordered at, and by whom (optional).
    appointment_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("appointments.id", ondelete="SET NULL"), nullable=True
    )
    ordered_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("workers.id", ondelete="SET NULL"), nullable=True
    )
    entered_by: Mapped[str | None] = mapped_column(String(36), nullable=True)  # the administrator's account id
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow)


REFERRAL_DIRECTIONS = ("incoming", "outgoing")  # to this office · from this office to a specialist
REFERRAL_STATUSES = ("open", "scheduled", "completed", "closed", "withdrawn")  # withdrawn: by its author
REFERRAL_URGENCIES = ("routine", "urgent")


class PatientReferral(Base):
    """A referral note: a specialist the patient was sent to (outgoing), or another practice that sent
    the patient here (incoming) — who, why, until when it is valid, what came of it."""

    __tablename__ = "patient_referrals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    patient_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("patients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    direction: Mapped[str] = mapped_column(String(16), nullable=False)  # REFERRAL_DIRECTIONS
    counterpart: Mapped[str] = mapped_column(String(200), nullable=False)  # the other provider / practice
    specialty: Mapped[str | None] = mapped_column(String(120), nullable=True)
    referral_date: Mapped[date] = mapped_column(Date, nullable=False)
    valid_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)  # the referral letter / the specialist's reply
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")  # REFERRAL_STATUSES
    appointment_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("appointments.id", ondelete="SET NULL"), nullable=True
    )
    entered_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # A referral the provider wrote on the patient's chart (referrals.py): signed, with a letter (PDF)
    # for the specialist — the fields below are empty for referral notes the office entered.
    worker_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("workers.id", ondelete="SET NULL"), nullable=True)
    urgency: Mapped[str | None] = mapped_column(String(16), nullable=True)  # REFERRAL_URGENCIES
    diagnosis_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("visit_diagnoses.id", ondelete="SET NULL"), nullable=True
    )
    question: Mapped[str | None] = mapped_column(Text, nullable=True)  # what the specialist is asked
    clinical_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    include_results: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    counterpart_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    counterpart_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    counterpart_fax: Mapped[str | None] = mapped_column(String(32), nullable=True)
    counterpart_address: Mapped[str | None] = mapped_column(String(300), nullable=True)
    signed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    verification: Mapped[str | None] = mapped_column(String(40), nullable=True)  # printed on the letter
    withdrawn_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    withdraw_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow)

    @property
    def number(self) -> str:
        return f"REF-{self.id:06d}"

    @property
    def has_letter(self) -> bool:
        return self.signed_at is not None


class ReferralDelivery(Base):
    """One hand-over of a referral letter: e-mailed to the specialist / a copy to the patient (with the
    PDF), the office told to help schedule it, or the PDF downloaded."""

    __tablename__ = "referral_deliveries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    referral_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("patient_referrals.id", ondelete="CASCADE"), nullable=False, index=True
    )
    method: Mapped[str] = mapped_column(String(16), nullable=False)  # email | pdf
    recipient: Mapped[str] = mapped_column(String(16), nullable=False)  # specialist | patient | office | (pdf: -)
    destination: Mapped[str | None] = mapped_column(String(320), nullable=True)  # masked
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # sent | failed | downloaded
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    by_account: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class VisitNote(Base):
    """The provider's note of a visit: when they started seeing the patient, what they found
    (``note``), their assessment and the plan. One per appointment; shown under "previous visits"
    in the patient's chart at later visits."""

    __tablename__ = "visit_notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    appointment_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("appointments.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    worker_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("workers.id", ondelete="SET NULL"), nullable=True)
    seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)  # the provider started the visit
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    assessment: Mapped[str | None] = mapped_column(Text, nullable=True)
    plan: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow)


# ==================== Diagnoses and prescriptions (prescriptions.py) ====================

# Who may diagnose and prescribe (a registered nurse may not; a physician assistant may).
PRESCRIBER_ROLES = ("doctor", "physician_assistant")
PRESCRIPTION_STATUSES = ("issued", "voided")
DOSAGE_FORMS = (
    "tablet",
    "capsule",
    "oral solution",
    "oral suspension",
    "inhaler",
    "cream",
    "ointment",
    "drops",
    "patch",
    "injection",
    "other",
)


class Pharmacy(Base):
    """A pharmacy prescriptions are sent to. The office keeps the directory; a provider adds the pharmacy a
    patient names at a visit when it is not listed yet (``source`` provider)."""

    __tablename__ = "pharmacies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)  # where prescriptions are e-mailed
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    fax: Mapped[str | None] = mapped_column(String(32), nullable=True)
    address: Mapped[str | None] = mapped_column(String(300), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="office")  # office | provider | seed
    added_by: Mapped[str | None] = mapped_column(String(200), nullable=True)  # "Dr. Alex Example (APT-000003)"
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow)


class VisitDiagnosis(Base):
    """A diagnosis the provider made at a visit: what (and the ICD-10 code, when given), primary or
    secondary, a note — and, when it is a lasting condition, the entry it added to the medical history."""

    __tablename__ = "visit_diagnoses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    appointment_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("appointments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    patient_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("patients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    worker_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("workers.id", ondelete="SET NULL"), nullable=True)
    description: Mapped[str] = mapped_column(String(300), nullable=False)
    code: Mapped[str | None] = mapped_column(String(10), nullable=True)  # ICD-10-CM, e.g. I10
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    note: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    health_item_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("patient_health_items.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class Prescription(Base):
    """A prescription issued (signed) by a prescriber at a visit: one or more medications
    (PrescriptionItem). Once issued it is never edited — a mistake is voided (with the reason) and a new
    one written. It is handed to the patient as a PDF and / or e-mailed to a pharmacy
    (PrescriptionDelivery keeps every hand-over)."""

    __tablename__ = "prescriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    patient_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("patients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    appointment_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("appointments.id", ondelete="SET NULL"), nullable=True, index=True
    )
    prescriber_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("workers.id", ondelete="SET NULL"), nullable=True
    )
    diagnosis_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("visit_diagnoses.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="issued")  # PRESCRIPTION_STATUSES
    note_to_pharmacist: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    allergy_override: Mapped[str | None] = mapped_column(String(500), nullable=True)  # why despite an allergy
    verification: Mapped[str] = mapped_column(String(40), nullable=False, default="")  # printed on the PDF
    issued_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    voided_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    void_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow)

    @property
    def number(self) -> str:
        return f"RX-{self.id:06d}"


class PrescriptionItem(Base):
    """One medication of a prescription."""

    __tablename__ = "prescription_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    prescription_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("prescriptions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    drug: Mapped[str] = mapped_column(String(200), nullable=False)
    strength: Mapped[str | None] = mapped_column(String(60), nullable=True)  # "20 mg"
    form: Mapped[str] = mapped_column(String(40), nullable=False, default="tablet")  # DOSAGE_FORMS
    sig: Mapped[str] = mapped_column(String(500), nullable=False)  # the directions
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    quantity_unit: Mapped[str] = mapped_column(String(30), nullable=False, default="tablets")
    refills: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    substitution_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Plain-language advice for the patient ("Take it with food …") — shown on the patient dashboard.
    patient_instructions: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    # The medication list entry this prescription updated / added (and whether it added it).
    health_item_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("patient_health_items.id", ondelete="SET NULL"), nullable=True
    )
    added_health_item: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class PrescriptionDelivery(Base):
    """One hand-over of a prescription: e-mailed to a pharmacy (with the PDF) or downloaded as a PDF."""

    __tablename__ = "prescription_deliveries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    prescription_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("prescriptions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    method: Mapped[str] = mapped_column(String(16), nullable=False)  # email | pdf
    pharmacy_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("pharmacies.id", ondelete="SET NULL"), nullable=True
    )
    destination: Mapped[str | None] = mapped_column(String(320), nullable=True)  # masked e-mail address
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # sent | failed | downloaded
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    by_account: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


# ==================== Visit summaries (visit_summaries.py) ====================

# draft = prepared from the chart, the provider may still edit it · signed = signed by the doctor and
# handed to the patient (never edited again) · superseded = an amendment replaced this signed version.
VISIT_SUMMARY_STATUSES = ("draft", "signed", "superseded")
# Who prepared the draft: the office marked the visit completed · the background job found a visit the
# provider saw that is over · the provider opened it on the chart · the dummy data.
VISIT_SUMMARY_SOURCES = ("office", "job", "provider", "seed")
# Who may sign a visit summary (the doctor — or a physician assistant, like prescriptions).
VISIT_SUMMARY_SIGNER_ROLES = ("doctor", "physician_assistant")


class VisitSummary(Base):
    """The after-visit summary the patient gets (visit_summaries.py): what happened at the visit in plain
    words — the reason, the vital signs, the diagnoses, the assessment and plan, the medications prescribed,
    the referrals and tests, the current medication and allergy lists, instructions and the follow-up —
    **signed by the doctor** (typed name + drawn signature through esign.py, a verification code printed on
    the PDF).

    Prepared automatically as a *draft* once the visit is over (the office marks it completed, or the
    background job finds a visit the provider saw that has ended) or when the provider opens it on the
    chart. Until it is signed the generated part (``content``, JSON) follows the chart; the texts
    (``assessment``, ``plan`` — taken from the visit note unless the provider rewrote them for the patient —
    ``instructions``, ``follow_up``) are the provider's. Signing freezes everything. A signed summary is
    never edited: a mistake is corrected by an *amendment* — a new version that supersedes it (every
    version stays on record). At most one draft per visit (``uq_visit_summary_draft``)."""

    __tablename__ = "visit_summaries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    appointment_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("appointments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    patient_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("patients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The visit's provider — who prepares and signs it.
    worker_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("workers.id", ondelete="SET NULL"), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")  # VISIT_SUMMARY_STATUSES
    content: Mapped[str] = mapped_column(Text, nullable=False, default="{}")  # JSON: schemas.VisitSummaryContent
    generated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    generated_by: Mapped[str] = mapped_column(String(16), nullable=False, default="provider")  # SOURCES
    # The provider's texts. None = not written for the patient (assessment / plan: the visit note's then).
    assessment: Mapped[str | None] = mapped_column(Text, nullable=True)
    plan: Mapped[str | None] = mapped_column(Text, nullable=True)
    instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    follow_up: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The signature (esign.py evidence) and the code printed on the PDF.
    signer_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    signature_png: Mapped[str | None] = mapped_column(Text, nullable=True)  # data:image/png;base64,…
    signed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    signed_by: Mapped[str | None] = mapped_column(String(36), nullable=True)  # the provider's account id
    esign_reference: Mapped[str | None] = mapped_column(String(120), nullable=True)
    esign_provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    verification: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # An amendment: why this version was written, and (on the old one) when / by which version it was replaced.
    amendment_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    superseded_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    superseded_by_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("visit_summaries.id", ondelete="SET NULL"), nullable=True
    )
    # The provider was told a draft is waiting for their signature (e-mail / text to the staff record).
    provider_notified_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    provider_notify_channels: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    provider_notified_to: Mapped[str] = mapped_column(String(400), nullable=False, default="")
    provider_notify_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Handed to the patient: e-mail (the PDF attached) and / or a text that it is ready.
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    sent_channels: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    sent_to: Mapped[str] = mapped_column(String(400), nullable=False, default="")
    delivery_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # The patient opened it in the app (first time).
    patient_viewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint("appointment_id", "version", name="uq_visit_summary_version"),
        # One draft per visit at a time (the current version being prepared / amended).
        Index(
            "uq_visit_summary_draft",
            "appointment_id",
            unique=True,
            sqlite_where=text("status = 'draft'"),
            postgresql_where=text("status = 'draft'"),
        ),
    )

    @property
    def number(self) -> str:
        """``VS-000042`` (the version is shown next to it: ``VS-000042 v2``)."""
        return f"VS-{self.id:06d}"


# ==================== Immunizations (immunizations.py) ====================

# Where an immunization entry came from: given at this office · reported (another clinic, the patient's own
# card) and entered by the office · the dummy data.
IMMUNIZATION_SOURCES = ("office", "external", "seed")
# active = part of the record · entered_in_error = kept for the history, left out of every report.
IMMUNIZATION_STATUSES = ("active", "entered_in_error")
IMMUNIZATION_ROUTES = ("IM", "SC", "ID", "oral", "intranasal", "other")


class PatientImmunization(Base):
    """One dose of a vaccine in the patient's record: what (vaccine, optional CVX code), when, which dose of
    the series, the product (manufacturer, lot), where and how (site, route) and who gave it — a member of
    staff here, or another clinic. Entered by the office; read by the provider on the chart; listed on the
    immunization record and the annual health examination report (patient_reports.py)."""

    __tablename__ = "patient_immunizations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    patient_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("patients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    vaccine: Mapped[str] = mapped_column(String(200), nullable=False)  # "Influenza, inactivated (flu shot)"
    cvx_code: Mapped[str | None] = mapped_column(String(8), nullable=True)  # CDC CVX code, e.g. "150"
    date_given: Mapped[date] = mapped_column(Date, nullable=False)
    dose_number: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 2 …
    series_doses: Mapped[int | None] = mapped_column(Integer, nullable=True)  # … of 3
    manufacturer: Mapped[str | None] = mapped_column(String(120), nullable=True)
    lot_number: Mapped[str | None] = mapped_column(String(60), nullable=True)
    site: Mapped[str | None] = mapped_column(String(60), nullable=True)  # "left deltoid"
    route: Mapped[str | None] = mapped_column(String(16), nullable=True)  # IMMUNIZATION_ROUTES
    administered_by: Mapped[str | None] = mapped_column(String(200), nullable=True)  # a person / another clinic
    worker_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("workers.id", ondelete="SET NULL"), nullable=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="office")  # IMMUNIZATION_SOURCES
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")  # IMMUNIZATION_STATUSES
    note: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    entered_by: Mapped[str | None] = mapped_column(String(36), nullable=True)  # the administrator's account id
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow)


# ==================== Patient reports (patient_reports.py) ====================

# immunization = the patient's immunization record · annual_exam = the annual health examination report
PATIENT_REPORT_KINDS = ("immunization", "annual_exam")
# requested = asked for (by the patient in the app, or at the desk) — the office has not prepared it yet ·
# draft = prepared by the office from the record, waiting for the assigned doctor · declined = the doctor
# sent it back (with the reason) — the office prepares it again or cancels it · signed = signed by the
# doctor and handed to the patient · canceled = withdrawn (office / patient) · superseded = replaced by an
# amended version.
PATIENT_REPORT_STATUSES = ("requested", "draft", "declined", "signed", "canceled", "superseded")
PATIENT_REPORT_OPEN = ("requested", "draft", "declined")
PATIENT_REPORT_PURPOSES = ("school", "work", "sports", "travel", "camp", "personal", "other")
# Who may sign these reports: a doctor.
PATIENT_REPORT_SIGNER_ROLES = ("doctor",)


class PatientReport(Base):
    """A report about the patient the office prepares **on request** and a **doctor signs**: the immunization
    record or the annual health examination report, for a stated purpose (school, work, sports, …).

    The generated part (``content``, JSON — schemas.ReportContent) is built from the record when the office
    prepares it and follows the record until it is signed; the doctor's part (``findings``, JSON —
    schemas.ReportFindings: the examination by system, the fitness decision, restrictions, recommendations;
    for an immunization record remarks on contraindications and vaccines due) and ``doctor_note`` are the
    doctor's. Signing freezes everything (typed name + drawn signature through esign.py, a verification code
    printed on the PDF). A signed report is corrected by an amendment — a new version (``replaces_id``) that
    supersedes it once signed."""

    __tablename__ = "patient_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    patient_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("patients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # PATIENT_REPORT_KINDS
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="requested")  # STATUSES
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    replaces_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("patient_reports.id", ondelete="SET NULL"), nullable=True
    )
    # The request: what for, for whom, by when, and who asked.
    purpose: Mapped[str] = mapped_column(String(16), nullable=False, default="personal")  # PURPOSES
    purpose_detail: Mapped[str | None] = mapped_column(String(200), nullable=True)  # "Exampleville High School"
    needed_by: Mapped[date | None] = mapped_column(Date, nullable=True)
    request_note: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    requested_via: Mapped[str] = mapped_column(String(16), nullable=False, default="office")  # patient | office
    requested_by: Mapped[str | None] = mapped_column(String(36), nullable=True)  # account id
    requested_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    office_notify_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Prepared by the office: the doctor who signs, the exam visit an annual report is based on.
    worker_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("workers.id", ondelete="SET NULL"), nullable=True)
    appointment_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("appointments.id", ondelete="SET NULL"), nullable=True
    )
    content: Mapped[str] = mapped_column(Text, nullable=False, default="{}")  # JSON: schemas.ReportContent
    generated_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    generated_by: Mapped[str | None] = mapped_column(String(36), nullable=True)  # the administrator's account id
    # The doctor's part.
    findings: Mapped[str] = mapped_column(Text, nullable=False, default="{}")  # JSON: schemas.ReportFindings
    doctor_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The signature.
    signer_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    signature_png: Mapped[str | None] = mapped_column(Text, nullable=True)
    signed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    signed_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    esign_reference: Mapped[str | None] = mapped_column(String(120), nullable=True)
    esign_provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    verification: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # Declined by the doctor / canceled / amended / superseded.
    decline_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    declined_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    cancel_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    canceled_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    canceled_by: Mapped[str | None] = mapped_column(String(16), nullable=True)  # office | patient
    amendment_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    superseded_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    superseded_by_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("patient_reports.id", ondelete="SET NULL"), nullable=True
    )
    # The doctor was told it waits for their signature; the patient got it.
    doctor_notified_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    doctor_notify_channels: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    doctor_notify_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    sent_channels: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    sent_to: Mapped[str] = mapped_column(String(400), nullable=False, default="")
    delivery_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    patient_viewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (Index("ix_patient_reports_status", "status"),)

    @property
    def number(self) -> str:
        """``RPT-000042`` (an amendment shows its version next to it)."""
        return f"RPT-{self.id:06d}"


# ==================== Hiring: job applications and onboarding (hiring.py) ====================

MIN_HIRING_AGE = 18  # default; config.MIN_HIRING_AGE overrides at runtime (hiring.py)
APPLICATION_STATUSES = ("submitted", "screening", "hired", "rejected", "withdrawn")
CHECK_STATUSES = ("pending", "in_progress", "passed", "failed", "not_applicable")
# Roles that must hold a professional licence / registration to be hired.
LICENSED_ROLES = ("doctor", "nurse", "physician_assistant", "technician")


class JobApplication(Base):
    """A candidate's application for a position. Three checks gate the hire: the **age** check
    (automatic, from the date of birth: at least MIN_HIRING_AGE), the **licence** check
    (automatic format/expiry validation, then a manager confirms it with the issuing board) and
    the **background screening** (recorded by a manager). Hiring creates the ``Worker`` and an
    ``Onboarding`` checklist."""

    __tablename__ = "job_applications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    date_of_birth: Mapped[date] = mapped_column(Date, nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)  # WORKER_ROLES
    specialty: Mapped[str | None] = mapped_column(String(120), nullable=True)
    employment_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    available_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    years_experience: Mapped[int | None] = mapped_column(Integer, nullable=True)
    license_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    license_authority: Mapped[str | None] = mapped_column(String(120), nullable=True)  # issuing board / state
    license_expires: Mapped[date | None] = mapped_column(Date, nullable=True)
    certifications: Mapped[str | None] = mapped_column(Text, nullable=True)  # BLS, ACLS, …
    cover_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    emergency_contact_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    emergency_contact_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    background_consent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="submitted")
    # The three checks: CHECK_STATUSES + a note each; who/when for the manual ones.
    age_check: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    age_note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    license_check: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    license_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    license_checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    license_checked_by: Mapped[str | None] = mapped_column(String(36), nullable=True)  # account id
    background_check: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    background_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    background_reference: Mapped[str | None] = mapped_column(String(120), nullable=True)  # screening provider ref
    background_checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    background_checked_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    worker_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("workers.id", ondelete="SET NULL"), nullable=True)
    email_norm: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (Index("ix_job_applications_status", "status"),)

    @property
    def number(self) -> str:
        return f"A-{self.id:06d}"


# The onboarding checklist every hire goes through, in order (key, label).
ONBOARDING_STEPS: tuple[tuple[str, str], ...] = (
    ("employment_application", "Employment application completed"),
    ("offer_letter", "Offer letter signed"),
    ("background_screening", "Background screening"),
    ("reference_check", "Reference check"),
    ("i9", "I-9 Employment Eligibility Verification"),
    ("w4", "W-4 completed"),
    ("payroll", "Payroll registration"),
    ("handbook", "Employee Handbook acknowledged"),
    ("hipaa", "HIPAA agreement signed"),
    ("osha", "OSHA training / acknowledgement signed"),
    ("emergency_contact", "Emergency contact on file"),
)
ONBOARDING_ITEM_STATUSES = ("pending", "done", "not_applicable")


class Onboarding(Base):
    """The onboarding process of one hired worker: one row per ONBOARDING_STEPS item. It is
    **complete** (``completed_at``) only when every step is done *and* a manager has approved it
    with an electronic signature (``approved_*``; hiring.approve)."""

    __tablename__ = "onboardings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    worker_id: Mapped[int] = mapped_column(Integer, ForeignKey("workers.id", ondelete="CASCADE"), nullable=False)
    application_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("job_applications.id", ondelete="SET NULL"), nullable=True
    )
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    steps_completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)  # last step ticked off
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(36), nullable=True)  # administrator account id
    approval_signer_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    approval_signature_png: Mapped[str | None] = mapped_column(Text, nullable=True)
    approval_esign_reference: Mapped[str | None] = mapped_column(String(120), nullable=True)
    approval_esign_provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)  # = approved
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (UniqueConstraint("worker_id", name="uq_onboarding_worker"),)


class OnboardingItem(Base):
    __tablename__ = "onboarding_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    onboarding_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("onboardings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    key: Mapped[str] = mapped_column(String(40), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    completed_by: Mapped[str | None] = mapped_column(String(36), nullable=True)  # account id

    __table_args__ = (UniqueConstraint("onboarding_id", "key", name="uq_onboarding_item"),)


class OnboardingInvite(Base):
    """A time-limited link sent to a new hire so they can finish their onboarding paperwork online
    (portal.py). The token travels in the link; we keep its hash for lookups **and** the full link
    in this log so an administrator can hand it over manually when the e-mail/text did not arrive
    (the staff dashboard shows this log; treat it like a password list)."""

    __tablename__ = "onboarding_invites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    onboarding_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("onboardings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    link: Mapped[str] = mapped_column(String(2048), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # hire | start | remind | manual
    channels: Mapped[str] = mapped_column(String(64), nullable=False, default="")  # "email,sms" actually delivered
    sent_to: Mapped[str] = mapped_column(String(400), nullable=False, default="")  # masked destinations
    delivery_note: Mapped[str | None] = mapped_column(String(500), nullable=True)  # provider errors, "fake outbox"
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)  # administrator account id
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    first_opened_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_opened_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    opens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class OnboardingSubmission(Base):
    """What the new hire submitted online for one checklist step: the form fields (JSON, never
    bank account numbers or government ids — see portal.py) and, for documents, the electronic
    signature: typed name, drawn signature (PNG data URL), time, the document version that was
    shown, and the e-sign provider's reference (esign.py; the built-in fake in development)."""

    __tablename__ = "onboarding_submissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    onboarding_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("onboardings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    key: Mapped[str] = mapped_column(String(40), nullable=False)
    data: Mapped[str] = mapped_column(Text, nullable=False, default="{}")  # JSON
    document_version: Mapped[str | None] = mapped_column(String(16), nullable=True)
    signer_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    signature_png: Mapped[str | None] = mapped_column(Text, nullable=True)  # data:image/png;base64,…
    signed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    esign_reference: Mapped[str | None] = mapped_column(String(120), nullable=True)
    esign_provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    submitted_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (UniqueConstraint("onboarding_id", "key", name="uq_onboarding_submission"),)


class SyncOp(Base):
    """One encrypted operation relayed between a user's devices. Deleted once every active
    device has acknowledged it, or after SYNC_OP_TTL_DAYS — whichever comes first."""

    __tablename__ = "sync_ops"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    account_id: Mapped[str] = mapped_column(String(36), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    device_id: Mapped[str] = mapped_column(String(36), nullable=False)
    op_id: Mapped[str] = mapped_column(String(64), nullable=False)  # client UUID → idempotent pushes
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)

    __table_args__ = (
        UniqueConstraint("account_id", "seq", name="uq_sync_seq"),
        UniqueConstraint("account_id", "op_id", name="uq_sync_op"),
        Index("ix_sync_ops_account_seq", "account_id", "seq"),
    )


class SyncSnapshot(Base):
    """Latest encrypted full snapshot per account (vault). Lets a new device — or a device that
    resets its local cache — rebuild from the server, which is the master copy."""

    __tablename__ = "sync_snapshots"

    account_id: Mapped[str] = mapped_column(String(36), ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True)
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)  # ops with seq > this still apply
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    device_id: Mapped[str] = mapped_column(String(36), nullable=False)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow)


class Release(Base):
    """Version-controlled release manifest: what the deploy pipeline published last."""

    __tablename__ = "releases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel: Mapped[str] = mapped_column(String(32), nullable=False, default="stable")
    version: Mapped[str] = mapped_column(String(40), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    min_supported_version: Mapped[str] = mapped_column(String(40), nullable=False)
    min_supported_schema: Mapped[int] = mapped_column(Integer, nullable=False)
    critical: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    published_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)

    __table_args__ = (UniqueConstraint("channel", "version", name="uq_release_channel_version"),)


# ==================== Sponsored content (docs/ADVERTISING.md) ====================

AD_STATUSES = ("draft", "active", "paused", "archived")
# First-party creative kinds (ad_sources.py): house promotions, paid sponsored messages, affiliate links.
AD_KINDS = ("internal", "sponsored", "affiliate")


class AppSetting(Base):
    """Small key/value store for operator settings changed at runtime from the admin panel
    (today: the active ad source). Operator data, never user data."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow)


class AdCampaign(Base):
    """One creative we show to free memberships. Text-first; the optional image is hosted by us.
    Targeting is *contextual only*: a list of regions (ISO 3166-1 alpha-2 codes or region
    groups, empty = everywhere) and languages (primary subtags, empty = any)."""

    __tablename__ = "ad_campaigns"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="sponsored")  # AD_KINDS
    sponsor: Mapped[str] = mapped_column(String(80), nullable=False)
    headline: Mapped[str] = mapped_column(String(90), nullable=False)
    blurb: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    cta_label: Mapped[str] = mapped_column(String(30), nullable=False, default="Learn more")
    target_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    image_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    regions: Mapped[str] = mapped_column(String(1000), nullable=False, default="")  # "DE,AT,CH" / "EU" / ""
    languages: Mapped[str] = mapped_column(String(200), nullable=False, default="")  # "de,en" / ""
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    weight: Mapped[int] = mapped_column(Integer, nullable=False, default=10)  # relative share in rotation
    starts_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    max_impressions: Mapped[int | None] = mapped_column(Integer, nullable=True)  # budget cap, None = unlimited
    impressions_total: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    clicks_total: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)  # administrator's account id
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow)


class AdDailyStat(Base):
    """Anonymous aggregate: how often a creative was seen/clicked per day and coarse region.
    There is deliberately nothing else — no who, no when within the day, no from-where-exactly."""

    __tablename__ = "ad_daily_stats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ad_id: Mapped[str] = mapped_column(String(36), ForeignKey("ad_campaigns.id", ondelete="CASCADE"), nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    region: Mapped[str] = mapped_column(String(2), nullable=False, default="ZZ")  # ISO alpha-2; ZZ = unknown/none
    impressions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    clicks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint("ad_id", "day", "region", name="uq_ad_daily_stat"),
        Index("ix_ad_daily_stats_day", "day"),
    )
