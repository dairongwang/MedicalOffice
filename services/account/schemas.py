"""Request/response models. Note what is absent: nothing here carries user content."""

from __future__ import annotations

import re
from datetime import date, datetime, time
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

from versioning import is_valid

# ==================== Auth ====================


class DeviceInfo(BaseModel):
    platform: str = Field(default="web", max_length=40)
    client_version: str = Field(default="", max_length=40)
    schema_version: int = Field(default=0, ge=0)
    name_ciphertext: str | None = Field(default=None, max_length=2048)


# Which front-end is asking (auth.check_audience): the patients' web app ("patient") only creates
# and signs in patient accounts; the staff / office dashboards ("office") never sign in patients.
Audience = Literal["patient", "office"]


class RegisterRequest(BaseModel):
    email: EmailStr
    auth_key: str = Field(min_length=32, max_length=256, description="Client-derived, never the password")
    wrapped_data_key: str = Field(min_length=16, max_length=4096)
    kdf_params: dict = Field(default_factory=dict)
    device: DeviceInfo = Field(default_factory=DeviceInfo)
    audience: Audience = "office"


class LoginRequest(BaseModel):
    email: EmailStr
    auth_key: str = Field(min_length=32, max_length=256)
    device: DeviceInfo = Field(default_factory=DeviceInfo)
    audience: Audience = "office"


class Session(BaseModel):
    token: str
    account_id: str
    device_id: str
    email: str
    # "" (with has_password False) for an account created by "Sign in with Google/Facebook" that
    # has not chosen its encryption passphrase yet — the client asks for one (POST /me/passphrase).
    wrapped_data_key: str
    kdf_params: dict
    vault_enabled: bool
    has_password: bool = True


# ---- Sign in with Google / Facebook (oauth.py) ----


class OAuthProviderInfo(BaseModel):
    name: str  # google | facebook
    label: str
    available: bool  # credentials configured, or the built-in fake stands in
    fake: bool  # the built-in fake answers (dev/test): the consent screen is served by this API


class OAuthProviders(BaseModel):
    providers: list[OAuthProviderInfo]


class OAuthCompleteRequest(BaseModel):
    """The ticket the callback put into the return URL's fragment (#oauth=…) → a session."""

    ticket: str = Field(min_length=16, max_length=2048)
    device: DeviceInfo = Field(default_factory=DeviceInfo)
    audience: Audience = "office"


class OAuthLinkRequest(BaseModel):
    return_to: str | None = Field(default=None, max_length=2048)


class OAuthLinkResponse(BaseModel):
    url: str  # send the browser here; it comes back to return_to#oauth_linked=<provider>


class OAuthIdentityOut(BaseModel):
    provider: str
    email: str
    linked_at: datetime
    last_login_at: datetime | None


class SetPassphraseRequest(BaseModel):
    """A sign-in-with-provider account chooses its encryption passphrase: the device derived the
    auth key from it and wrapped a fresh data key with it — same shape as registering."""

    auth_key: str = Field(min_length=32, max_length=256)
    wrapped_data_key: str = Field(min_length=16, max_length=4096)
    kdf_params: dict = Field(default_factory=dict)


# ---- Two-factor authentication (twofactor.py) ----
# Methods: totp (authenticator app) · sms (code by text message) · email (code by e-mail), plus
# one-time recovery codes ("recovery") once any method is on.

TwoFactorMethod = Literal["totp", "sms", "email"]
CodeChannel = Literal["sms", "email"]


class CodeDelivery(BaseModel):
    """A code was sent by e-mail or text message."""

    method: str  # email | sms
    destination: str  # masked: "a•••@example.com" · "+•••••••4567"
    expires_in: int  # seconds the code stays valid
    resend_after: int  # seconds before another code may be requested
    # The built-in fake delivered it (no provider configured, never in prod): where to read it.
    fake_outbox_url: str | None = None


class TwoFactorChallenge(BaseModel):
    """``202`` answer of /auth/login when the account has a second factor: the password was
    right, now POST /auth/login/2fa with this challenge and a code. No session, no key yet."""

    two_factor_required: bool = True
    challenge: str
    expires_in: int  # seconds
    methods: list[str]  # enabled methods, + "recovery" while recovery codes are left
    default_method: str  # what the sign-in screen offers first (totp > sms > email)
    destinations: dict[str, str] = Field(default_factory=dict)  # method → masked address/number
    sent: CodeDelivery | None = None  # the default method was sms/email: a code is on its way
    send_error: str | None = None  # … or could not be sent (pick another method / a recovery code)


class TwoFactorLoginRequest(BaseModel):
    challenge: str = Field(min_length=16, max_length=2048)
    code: str = Field(
        min_length=6, max_length=32, description="6-digit code (authenticator, text, e-mail) or a recovery code"
    )
    device: DeviceInfo = Field(default_factory=DeviceInfo)
    audience: Audience = "office"


class TwoFactorSendLogin(BaseModel):
    """Sign-in: send (or resend) a code by text message or e-mail."""

    challenge: str = Field(min_length=16, max_length=2048)
    method: CodeChannel


class TwoFactorSend(BaseModel):
    """Signed in: send a code to confirm a sensitive change (turn off, new recovery codes)."""

    method: CodeChannel


class TwoFactorStatus(BaseModel):
    enabled: bool
    enabled_at: datetime | None = None
    recovery_codes_left: int = 0
    pending: bool = False  # authenticator set-up started (QR shown), first code not confirmed yet
    methods: list[str] = Field(default_factory=list)  # the enabled ones, in preference order
    phone: str | None = None  # masked, while text-message codes are on
    email: str | None = None  # masked account e-mail
    # Methods this server can set up right now (sms/email need a provider — or the built-in fake).
    available: list[str] = Field(default_factory=lambda: ["totp"])
    fake_channels: list[str] = Field(default_factory=list)  # answered by the built-in fake


class TwoFactorPassword(BaseModel):
    """Sensitive changes re-prove the password: the client-derived auth key, never the password."""

    auth_key: str = Field(min_length=32, max_length=256)


class TwoFactorSetupRequest(TwoFactorPassword):
    method: TwoFactorMethod = "totp"
    phone: str | None = Field(default=None, max_length=40, description="sms: number in international format")
    email: str | None = Field(
        default=None, max_length=320, description="email: address for the codes (default: account)"
    )
    # Changing a method that is already on: a current code (any method, or a recovery code).
    code: str | None = Field(default=None, min_length=6, max_length=32)


class TwoFactorCode(BaseModel):
    code: str = Field(min_length=6, max_length=32)
    method: TwoFactorMethod = "totp"


class TwoFactorPasswordAndCode(TwoFactorPassword):
    code: str = Field(
        min_length=6, max_length=32, description="Current code from any enabled method, or a recovery code"
    )


class TwoFactorDisable(TwoFactorPasswordAndCode):
    method: TwoFactorMethod | None = Field(default=None, description="Remove one method; omitted = turn everything off")


class TwoFactorSetup(BaseModel):
    """totp: what the authenticator app needs (the client renders ``otpauth_uri`` as a QR code
    and shows ``secret`` for manual entry). sms/email: ``delivery`` — a code is on its way."""

    method: str = "totp"
    secret: str = ""
    otpauth_uri: str = ""
    issuer: str
    account: str
    digits: int = 6
    period: int = 30
    algorithm: str = "SHA1"
    delivery: CodeDelivery | None = None
    replacing: bool = False  # the method was already on: confirming the code swaps it over


class TwoFactorRecoveryCodes(BaseModel):
    """Shown once — the server keeps hashes only. Empty when a further method was added
    (the codes issued with the first method stay valid)."""

    recovery_codes: list[str]
    status: TwoFactorStatus


class DeviceOut(BaseModel):
    id: str
    platform: str
    client_version: str
    schema_version: int
    cursor: int
    name_ciphertext: str | None
    last_seen_at: datetime
    is_current: bool = False


class Entitlements(BaseModel):
    plan: str  # billing plan: free | pro | lifetime
    status: str  # subscription status: none | trialing | active | past_due | canceled | paused
    features: list[str]
    current_period_end: datetime | None
    cancel_at_period_end: bool
    trial_available: bool
    # Which payment strategy holds the subscription (stripe | paypal | seed | admin | …).
    provider: str = ""
    # Until when the paid membership lasts as things stand: trial end, next renewal, the end of
    # a canceled period, or the end of the dunning grace. None = Lifetime / nothing paid.
    access_until: datetime | None = None
    # Renews automatically at access_until (active Pro without a pending cancellation).
    renews: bool = False
    # POST /billing/cancel ends the membership now and refunds (GET /billing/cancel/preview says how much).
    can_cancel: bool = False
    # POST /billing/manage opens the provider's self-service page (payment method, invoices).
    can_manage: bool = False
    # The most recent charge (paid, failed or refunded) — what the account screen reports.
    last_payment: PaymentOut | None = None
    # Membership type the strategy resolved to (membership.py): free | paid | admin.
    membership: str = "free"
    membership_label: str = "Free"
    max_active_tasks: int | None = None  # None = unlimited


class PaymentOut(BaseModel):
    """One charge from the ledger (models.Payment) as the account screen lists it."""

    id: int
    provider: str
    plan: str  # pro_monthly | pro_yearly | lifetime
    amount: str  # decimal string, e.g. "10.00"
    currency: str
    status: str  # succeeded | failed | refunded | partially_refunded
    refunded: str  # decimal string
    refund_id: str | None
    refunded_at: datetime | None
    failure_reason: str | None
    period_start: datetime | None
    period_end: datetime | None
    paid_at: datetime


class Me(BaseModel):
    account_id: str
    email: str
    role: str = "user"
    created_at: datetime
    vault_enabled: bool
    devices: list[DeviceOut]
    entitlements: Entitlements
    two_factor: TwoFactorStatus = Field(default_factory=lambda: TwoFactorStatus(enabled=False))
    # Ways to sign in: the password (False until a sign-in-with-provider account sets its
    # passphrase) and the linked Google / Facebook identities.
    has_password: bool = True
    identities: list[OAuthIdentityOut] = Field(default_factory=list)


class AccountSettings(BaseModel):
    vault_enabled: bool | None = None


# ==================== Membership ====================


class MembershipInfo(BaseModel):
    """Public description of one membership type (what the pricing/account screens show)."""

    type: str  # anonymous | free | paid | admin
    label: str
    description: str
    requires_account: bool
    max_active_tasks: int | None  # None = unlimited
    features: list[str]
    can_upgrade: bool


class MembershipCatalog(BaseModel):
    memberships: list[MembershipInfo]


# ==================== Admin ====================


class AdminStats(BaseModel):
    accounts_total: int
    accounts_deleted: int
    memberships: dict[str, int]  # free | paid | admin → count (live accounts)
    subscriptions: dict[str, int]  # status → count
    devices_active: int
    sync_ops: int
    sync_bytes: int
    snapshots: int
    snapshot_bytes: int
    latest_release: str | None
    service_version: str
    database: str
    # Advertising is managed in the separate ads admin console (apps/ads-admin); "" = not configured.
    ads_admin_url: str = ""


class AdminAccountSummary(BaseModel):
    id: str
    email: str
    role: str
    membership: str
    plan: str
    status: str
    current_period_end: datetime | None
    created_at: datetime
    deleted_at: datetime | None
    devices_active: int
    last_seen_at: datetime | None
    two_factor_enabled: bool = False
    two_factor_methods: list[str] = Field(default_factory=list)  # totp | sms | email
    two_factor_phone: str | None = None  # masked — lets support confirm "the number ending in 4567"


class AdminAccountPage(BaseModel):
    items: list[AdminAccountSummary]
    total: int
    offset: int
    limit: int


class AdminAuditEntry(BaseModel):
    at: datetime
    actor_account_id: str | None
    actor_email: str | None
    action: str
    detail: dict


class AdminDeviceOut(BaseModel):
    """Device metadata for support — without the (encrypted) device name."""

    id: str
    platform: str
    client_version: str
    schema_version: int
    cursor: int
    created_at: datetime
    last_seen_at: datetime


class AdminAccountDetail(AdminAccountSummary):
    vault_enabled: bool
    has_password: bool = True
    oauth_providers: list[str] = Field(default_factory=list)  # google | facebook linked for sign-in
    provider: str
    cancel_at_period_end: bool
    features: list[str]
    max_active_tasks: int | None
    devices: list[AdminDeviceOut]
    sync_ops: int
    sync_bytes: int
    snapshot_bytes: int | None
    audit: list[AdminAuditEntry]


class AdminMembershipUpdate(BaseModel):
    """Grant/revoke a paid membership by hand (comps, refunds, support). Writes the same
    subscription row a billing webhook would, with provider "admin", and an audit entry."""

    plan: str = Field(pattern="^(free|pro|lifetime)$")
    # Defaults: pro/lifetime → active, free → canceled. "none" makes the trial available again.
    status: str | None = Field(default=None, pattern="^(none|trialing|active|past_due|canceled)$")
    days: int | None = Field(default=None, ge=1, le=3660, description="Period length for pro (default 30)")
    note: str = Field(default="", max_length=500)


class AdminRoleUpdate(BaseModel):
    role: str = Field(pattern="^(user|admin|patient|staff)$")


# ==================== Sync ====================


class PushOp(BaseModel):
    op_id: str = Field(min_length=1, max_length=64)
    schema_version: int = Field(ge=1)
    ciphertext: str = Field(min_length=4, description="base64; opaque to the server")


class PushRequest(BaseModel):
    ops: list[PushOp] = Field(max_length=1000)


class PushedOp(BaseModel):
    op_id: str
    seq: int


class PushResponse(BaseModel):
    accepted: list[PushedOp]
    last_seq: int


class PulledOp(BaseModel):
    seq: int
    op_id: str
    device_id: str
    schema_version: int
    ciphertext: str


class PullResponse(BaseModel):
    ops: list[PulledOp]
    last_seq: int
    cursor: int
    has_more: bool
    snapshot_seq: int | None


class AckRequest(BaseModel):
    cursor: int = Field(ge=0)


class SnapshotIn(BaseModel):
    seq: int = Field(ge=0, description="Ops with seq > this must be applied on top of the snapshot")
    schema_version: int = Field(ge=1)
    ciphertext: str = Field(min_length=4)


class SnapshotOut(BaseModel):
    seq: int
    schema_version: int
    device_id: str
    ciphertext: str
    updated_at: datetime


class SyncStatus(BaseModel):
    last_seq: int
    cursor: int
    pending_for_device: int
    snapshot_seq: int | None
    vault_enabled: bool
    active_devices: int


# ==================== Billing ====================


class CheckoutRequest(BaseModel):
    plan: str = Field(pattern="^(pro_monthly|pro_yearly|lifetime)$")
    return_url: str = Field(default="/", max_length=2048)
    # Payment method (payments.py strategy key): stripe | paypal. None = the default one.
    method: str | None = Field(default=None, pattern="^[a-z_]{1,32}$")


class CheckoutResponse(BaseModel):
    checkout_url: str
    provider: str


class PaymentMethodInfo(BaseModel):
    """One payment method as the client's "Subscribe" UI shows it (payments.py strategy)."""

    name: str  # stripe | paypal
    label: str
    description: str
    plans: list[str]  # which of pro_monthly | pro_yearly | lifetime this method sells
    sandbox: bool = False  # nothing is charged (the provider's test mode or the built-in fake)
    fake: bool = False  # the built-in fake of this provider (fakes.py) answers instead of the real one
    available: bool = True  # False = credentials missing on this server; checkout answers 503


class ManageRequest(BaseModel):
    return_url: str = Field(default="/", max_length=2048)


class ManageResponse(BaseModel):
    url: str
    provider: str


class PlanInfo(BaseModel):
    """One paid membership as sold: price (config PRICE_*) and refund rule."""

    id: str  # pro_monthly | pro_yearly | lifetime
    title: str
    amount: str  # decimal string in `currency`, e.g. "1.00"
    amount_cents: int
    currency: str
    interval: str | None  # month | year | None = one-off
    period_days: int | None  # nominal length of one paid period (None = Lifetime)
    refund_rule: str  # human-readable: what a cancellation gives back


class PaymentMethods(BaseModel):
    methods: list[PaymentMethodInfo]
    default: str | None  # pre-selected method, None when no method is configured yet
    plans: list[PlanInfo] = []
    currency: str = "USD"


class RefundPreview(BaseModel):
    """What cancelling right now gives back (GET /billing/cancel/preview)."""

    amount: str  # decimal string
    amount_cents: int
    currency: str
    basis: str  # prorated | flat | none
    days_left: float | None = None  # prorated: unused days …
    days_total: float | None = None  # … of the paid period
    paid: str | None = None  # the payment the refund comes out of
    note: str = ""


class CancelResult(BaseModel):
    entitlements: Entitlements
    refund: RefundPreview
    refund_id: str | None = None  # the provider's refund id (None = nothing to refund)
    refunded_via: str = "none"  # provider | recorded (no usable provider: ledger only) | none


class PaymentDetails(BaseModel):
    """The money side of a provider event: one charge (or its failure / refund)."""

    provider_payment_id: str = Field(max_length=128)  # Stripe payment intent/charge, PayPal capture/sale
    amount_cents: int = 0
    currency: str = "USD"
    plan: str | None = None  # pro_monthly | pro_yearly | lifetime (None = work it out)
    period_start: datetime | None = None
    period_end: datetime | None = None
    paid_at: datetime | None = None
    failure_reason: str | None = None
    refunded_cents: int | None = None  # payment.refunded: the amount (see refund_is_total)
    refund_is_total: bool = True  # True = cumulative total refunded (Stripe); False = this refund only (PayPal)
    refund_id: str | None = None


class PaymentEvent(BaseModel):
    """Provider-neutral billing event: what every payment strategy turns its webhook into
    (billing.apply_webhook only ever sees this shape)."""

    event_id: str = Field(max_length=128)
    # subscription.created | subscription.updated | transaction.completed | subscription.canceled
    # | payment_failed | payment.succeeded | payment.refunded
    type: str = Field(max_length=64)
    # A charge / failure / refund this event reports (recorded in the payments ledger).
    payment: PaymentDetails | None = None
    # None = unknown to the provider's payload (renewals, failures): resolved via subscription_id.
    account_id: str | None = None
    plan: str = "pro"  # pro | lifetime
    status: str = "active"  # trialing | active | past_due | canceled | paused
    # None = the provider did not say (keeps the stored period; Lifetime never has one).
    current_period_end: datetime | None = None
    cancel_at_period_end: bool = False
    customer_id: str | None = Field(default=None, max_length=128)
    subscription_id: str | None = Field(default=None, max_length=128)


# ==================== Sponsored content ====================


def https_url(value: str | None) -> str | None:
    """Creative links must be https (no javascript:/data:/http: — they open in a new tab)."""
    if value is None or not value.strip():
        return None
    value = value.strip()
    if not value.lower().startswith("https://") or any(c.isspace() for c in value):
        raise ValueError("must be an https:// URL")
    return value


class AdCreative(BaseModel):
    """What the client renders. Note what is absent: nothing about the viewer."""

    id: str
    sponsor: str
    headline: str
    blurb: str
    cta_label: str
    target_url: str
    image_url: str | None = None


class AdSource(BaseModel):
    """The active ad source, as the client needs it to render (ad_sources.py)."""

    kind: str  # internal | sponsored | affiliate | adsense | microsoft | network
    label: str  # badge text, e.g. "Sponsored", "Ad · Google AdSense"
    title: str  # human name of the source
    mode: str  # fake | live | unavailable
    render: str  # creatives (first-party cards) | embed (provider script tag)
    third_party: bool
    consent_required: bool
    provider_name: str = ""
    privacy_url: str = ""
    disclosure: str = ""
    embed: dict | None = None  # script_url, container_*, push_global — only for render = embed


class AdServeResponse(BaseModel):
    ads: list[AdCreative]
    region: str  # the region the selection used ("ZZ" = none)
    rotation_seconds: int
    enabled: bool
    source: AdSource


class AdEventIn(BaseModel):
    ad_id: str = Field(min_length=1, max_length=36)
    kind: str = Field(pattern="^(impression|click)$")
    region: str | None = Field(default=None, max_length=2)


class AdEventsRequest(BaseModel):
    events: list[AdEventIn] = Field(max_length=20)


class AdEventsResponse(BaseModel):
    accepted: int


def https_or_app_url(value: str | None) -> str | None:
    """Creative links are https; internal (house) ads may also use ``app:upgrade`` (opens the account screen)."""
    if value is not None and value.strip() in APP_LINKS:
        return value.strip()
    return https_url(value)


APP_LINKS = ("app:upgrade",)


class AdCampaignIn(BaseModel):
    kind: str = Field(default="sponsored", pattern="^(internal|sponsored|affiliate)$")
    sponsor: str = Field(min_length=1, max_length=80)
    headline: str = Field(min_length=1, max_length=90)
    blurb: str = Field(default="", max_length=200)
    cta_label: str = Field(default="Learn more", min_length=1, max_length=30)
    target_url: str = Field(min_length=1, max_length=2048)
    image_url: str | None = Field(default=None, max_length=2048)
    regions: str = Field(
        default="", max_length=1000, description="Country codes / groups, comma-separated; empty = everywhere"
    )
    languages: str = Field(default="", max_length=200, description="Language codes, comma-separated; empty = any")
    status: str = Field(default="draft", pattern="^(draft|active|paused|archived)$")
    weight: int = Field(default=10, ge=1, le=100)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    max_impressions: int | None = Field(default=None, ge=1)

    @field_validator("target_url")
    @classmethod
    def _link(cls, v: str) -> str:
        return https_or_app_url(v) or v

    @field_validator("image_url")
    @classmethod
    def _https_only(cls, v: str | None) -> str | None:
        return https_url(v)

    @field_validator("sponsor", "headline", "blurb", "cta_label")
    @classmethod
    def _single_line(cls, v: str) -> str:
        return " ".join(v.split())

    @model_validator(mode="after")
    def _schedule(self):
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be after starts_at")
        return self


class AdCampaignPatch(BaseModel):
    kind: str | None = Field(default=None, pattern="^(internal|sponsored|affiliate)$")
    sponsor: str | None = Field(default=None, min_length=1, max_length=80)
    headline: str | None = Field(default=None, min_length=1, max_length=90)
    blurb: str | None = Field(default=None, max_length=200)
    cta_label: str | None = Field(default=None, min_length=1, max_length=30)
    target_url: str | None = Field(default=None, min_length=1, max_length=2048)
    image_url: str | None = Field(default=None, max_length=2048)
    regions: str | None = Field(default=None, max_length=1000)
    languages: str | None = Field(default=None, max_length=200)
    status: str | None = Field(default=None, pattern="^(draft|active|paused|archived)$")
    weight: int | None = Field(default=None, ge=1, le=100)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    max_impressions: int | None = Field(default=None, ge=1)

    @field_validator("target_url")
    @classmethod
    def _link(cls, v: str | None) -> str | None:
        return https_or_app_url(v)

    @field_validator("image_url")
    @classmethod
    def _https_only(cls, v: str | None) -> str | None:
        return https_url(v)

    @field_validator("sponsor", "headline", "blurb", "cta_label")
    @classmethod
    def _single_line(cls, v: str | None) -> str | None:
        return None if v is None else " ".join(v.split())


class AdCampaignOut(AdCampaignIn):
    id: str
    impressions_total: int
    clicks_total: int
    live: bool
    created_at: datetime
    updated_at: datetime


class AdStatPoint(BaseModel):
    key: str  # ISO day or region code
    impressions: int
    clicks: int


class AdStats(BaseModel):
    ad_id: str
    days: int
    impressions: int
    clicks: int
    ctr: float
    by_day: list[AdStatPoint]
    by_region: list[AdStatPoint]


class AdSourceAdmin(AdSource):
    active: bool
    reason: str = ""  # why "unavailable"
    reporting: str = ""
    settings: dict[str, str] = {}  # ids/urls an administrator sees (no secrets exist here)


class AdSourcesOut(BaseModel):
    active: str
    fakes_enabled: bool
    sources: list[AdSourceAdmin]


class AdSourceUpdate(BaseModel):
    kind: str = Field(pattern="^(internal|sponsored|affiliate|adsense|microsoft|network)$")


# ---- analytics & compliance (ad-admin dashboard) ----


class AdKindPerformance(BaseModel):
    kind: str
    impressions: int
    clicks: int
    ctr: float
    campaigns: int


class AdCampaignPerformance(BaseModel):
    id: str
    kind: str
    sponsor: str
    headline: str
    status: str
    live: bool
    weight: int
    regions: str
    impressions: int
    clicks: int
    ctr: float
    share: float  # share of all impressions in the window
    impressions_total: int
    clicks_total: int


class AdAnalytics(BaseModel):
    days: int
    since: str
    until: str
    kind: str | None
    impressions: int
    clicks: int
    ctr: float
    previous_impressions: int
    previous_clicks: int
    impressions_change: float | None  # vs. the previous window (None = no baseline)
    clicks_change: float | None
    by_day: list[AdStatPoint]
    by_kind: list[AdKindPerformance]
    by_region: list[AdStatPoint]  # small counts folded into "other"
    regions_folded: int
    min_count: int
    campaigns: list[AdCampaignPerformance]
    active_source: AdSource
    third_party_note: str


# ==================== Payment analytics (administrators' console) ====================


class PaymentDayPoint(BaseModel):
    key: str  # YYYY-MM-DD
    transactions: int = 0
    succeeded: int = 0
    failed: int = 0
    refunds: int = 0  # refunds that happened that day (refunded_at)
    revenue_cents: int = 0  # gross, charges paid that day
    refunded_cents: int = 0
    net_cents: int = 0
    revenue: str = "0.00"
    refunded: str = "0.00"


class PaymentStatusShare(BaseModel):
    status: str
    count: int
    amount_cents: int  # paid amount; for refunded statuses the refunded amount
    amount: str
    share: float = 0.0  # of transactions


class PaymentMethodShare(BaseModel):
    provider: str
    label: str
    count: int = 0
    succeeded: int = 0
    failed: int = 0
    amount_cents: int = 0
    refunded_cents: int = 0
    amount: str = "0.00"
    refunded: str = "0.00"
    share: float = 0.0  # of gross revenue


class PaymentPlanShare(BaseModel):
    plan: str
    title: str
    count: int = 0
    amount_cents: int = 0
    refunded_cents: int = 0
    amount: str = "0.00"
    refunded: str = "0.00"
    share: float = 0.0


class TopCustomer(BaseModel):
    account_id: str
    email: str
    membership: str
    payments: int
    amount: str
    amount_cents: int
    refunded: str
    refunded_cents: int
    net_cents: int
    share: float
    last_paid_at: datetime


class AdminRefund(BaseModel):
    id: int
    account_id: str
    email: str
    provider: str
    plan: str
    amount: str
    refunded: str
    refunded_cents: int
    refund_id: str | None
    refunded_at: datetime | None
    paid_at: datetime
    full: bool


class KpiAlert(BaseModel):
    key: str
    level: str  # ok | warn | fail
    title: str
    detail: str
    value: float | None = None
    threshold: float | None = None


class PaymentAnalytics(BaseModel):
    since: str
    until: str
    days: int
    method: str | None
    status: str | None
    currency: str
    mixed_currencies: bool
    revenue: str  # gross
    revenue_cents: int
    refunded: str
    refunded_cents: int
    net_revenue: str
    net_revenue_cents: int
    transactions: int
    succeeded: int
    failed: int
    refund_count: int
    success_rate: float
    refund_rate: float
    average_transaction: str
    average_transaction_cents: int
    previous_revenue_cents: int
    previous_net_revenue_cents: int
    previous_transactions: int
    previous_succeeded: int
    previous_failed: int
    previous_refunded_cents: int
    revenue_change: float | None
    transactions_change: float | None
    failed_change: float | None
    by_day: list[PaymentDayPoint]
    by_status: list[PaymentStatusShare]
    by_method: list[PaymentMethodShare]
    by_plan: list[PaymentPlanShare]
    top_customers: list[TopCustomer]
    refunds: list[AdminRefund]
    alerts: list[KpiAlert]
    methods: list[str]  # every provider seen in the ledger (filter options)


class AdminTransaction(BaseModel):
    id: int
    account_id: str
    email: str
    provider: str
    provider_payment_id: str
    plan: str
    amount: str
    amount_cents: int
    currency: str
    status: str
    refunded: str
    refunded_cents: int
    refund_id: str | None
    refunded_at: datetime | None
    failure_reason: str | None
    period_start: datetime | None
    period_end: datetime | None
    paid_at: datetime


class AdminTransactionPage(BaseModel):
    items: list[AdminTransaction]
    total: int
    limit: int
    offset: int


class AdminTransactionAccount(BaseModel):
    id: str
    email: str
    role: str
    membership: str
    created_at: datetime
    deleted_at: datetime | None
    plan: str
    status: str
    provider: str
    provider_customer_id: str | None
    provider_subscription_id: str | None
    current_period_end: datetime | None
    cancel_at_period_end: bool
    lifetime_value_cents: int  # everything this account paid minus refunds


class AdminTransactionDetail(BaseModel):
    transaction: AdminTransaction
    account: AdminTransactionAccount
    history: list[AdminTransaction]  # the account's other charges, newest first


class ComplianceCheck(BaseModel):
    key: str
    title: str
    status: str  # ok | warn | fail
    detail: str


class ComplianceRegime(BaseModel):
    code: str
    name: str
    jurisdiction: str
    obligations: list[str]
    how_met: list[str]
    status: str  # ok | attention
    attention: list[str]


class DataInventoryItem(BaseModel):
    data: str
    location: str
    personal: bool
    retention: str
    rows: int


class AdCompliance(BaseModel):
    generated_at: datetime
    environment: str
    active_source: AdSource
    checks: list[ComplianceCheck]
    regimes: list[ComplianceRegime]
    inventory: list[DataInventoryItem]
    disclaimer: str


class AdOverview(BaseModel):
    source: AdSource
    enabled: bool
    campaigns: dict[str, int]  # status → count
    live: int
    impressions_7d: int
    clicks_7d: int
    rotation_seconds: int
    retention_days: int
    image_hosts: list[str]  # first-party hosts creative images may come from (empty = text only)
    region_groups: dict[str, list[str]]


# ==================== Releases ====================


class ReleaseManifest(BaseModel):
    channel: str = Field(default="stable", max_length=32)
    version: str = Field(max_length=40)
    schema_version: int = Field(ge=1)
    min_supported_version: str = Field(max_length=40)
    min_supported_schema: int = Field(ge=1)
    critical: bool = False
    notes: str = Field(default="", max_length=20000)
    published_at: datetime | None = None

    @model_validator(mode="before")
    @classmethod
    def _accept_camel_case(cls, data):
        """release.json (apps/web) uses camelCase; the API speaks snake_case. Accept both."""
        if isinstance(data, dict):
            import re

            data = {re.sub(r"(?<!^)(?=[A-Z])", "_", k).lower(): v for k, v in data.items() if k != "$schema"}
        return data

    @field_validator("version", "min_supported_version")
    @classmethod
    def _semver(cls, value: str) -> str:
        if not is_valid(value):
            raise ValueError("must be a semantic version like 1.2.3")
        return value.strip().lstrip("v")


class ClientCheck(BaseModel):
    """What a client gets when it asks 'am I still allowed?'."""

    latest: ReleaseManifest | None
    client_version: str
    client_schema: int
    update_available: bool
    update_required: bool
    reason: str | None


# ==================== Patients (patients.py) ====================
# The public intake page: a patient enters their details; the office learns whether they are
# new (→ a record is created) or already known (→ nothing is stored twice).

PatientSex = Literal["female", "male", "other", "unspecified"]
# Whose name the insurance policy is in (models.INSURANCE_RELATIONSHIPS).
InsuranceRelationship = Literal["self", "spouse", "parent", "child", "other"]
InsuranceCardSide = Literal["front", "back"]
# A card photo as a base64 data URL: insurance.MAX_CARD_BYTES decoded → 4/3 of that in characters,
# plus the "data:image/jpeg;base64," prefix.
INSURANCE_CARD_MAX_CHARS = 5 * 1024 * 1024 * 4 // 3 + 64


def _clean(value: str | None) -> str | None:
    """Trim, collapse inner whitespace; empty → None."""
    if value is None:
        return None
    text = " ".join(value.split())
    return text or None


def _name_value(v):
    """A person's name: trimmed, must contain letters."""
    if isinstance(v, str):
        v = _clean(v)
        if v and not any(ch.isalpha() for ch in v):
            raise ValueError("must contain letters")
    return v


def _phone_value(v):
    """A phone number as typed, with 7–15 digits in it; blank → None."""
    if not isinstance(v, str):
        return v
    v = _clean(v)
    if v is None:
        return None
    digits = "".join(ch for ch in v if ch.isdigit())
    if not 7 <= len(digits) <= 15:
        raise ValueError("must be a phone number with 7 to 15 digits")
    return v


class PatientIdentity(BaseModel):
    """What identifies a patient for the "are you new?" check: name and date of birth, plus
    the optional contact details that also count as a match when the date of birth agrees."""

    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    date_of_birth: date
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=32)

    @field_validator("first_name", "last_name", mode="before")
    @classmethod
    def _name(cls, v):
        return _name_value(v)

    @field_validator("email", mode="before")
    @classmethod
    def _blank_email(cls, v):
        return _clean(v) if isinstance(v, str) else v

    @field_validator("phone", mode="before")
    @classmethod
    def _phone(cls, v):
        return _phone_value(v)

    @field_validator("date_of_birth")
    @classmethod
    def _dob(cls, v: date) -> date:
        today = date.today()
        if v > today:
            raise ValueError("cannot be in the future")
        if v < today.replace(year=today.year - 130):
            raise ValueError("is not a plausible date of birth")
        return v


class PatientDetails(BaseModel):
    """The optional part of the intake form — everything but who the patient is and the consent.
    Shared by the registration (``PatientIn``) and the patient's own update (``PatientUpdate``)."""

    sex: PatientSex | None = None
    address_line: str | None = Field(default=None, max_length=200)
    city: str | None = Field(default=None, max_length=100)
    state: str | None = Field(default=None, max_length=100)
    postal_code: str | None = Field(default=None, max_length=20)
    country: str | None = Field(default=None, max_length=100)
    # Insurance section — all optional (self-pay patients, or "I'll bring the card"). The card
    # photos are data URLs (data:image/jpeg;base64,…), checked by insurance.decode_card_image;
    # the length cap here only stops absurd payloads before decoding.
    insurance_provider: str | None = Field(default=None, max_length=120, description="Insurance company")
    insurance_plan_name: str | None = Field(default=None, max_length=120, description="Plan name / type")
    insurance_member_id: str | None = Field(default=None, max_length=64)
    insurance_group_number: str | None = Field(default=None, max_length=64)
    insurance_policyholder_name: str | None = Field(
        default=None, max_length=120, description="Whose name the policy is in, when not the patient's"
    )
    insurance_policyholder_relationship: InsuranceRelationship | None = None
    insurance_card_front: str | None = Field(
        default=None, max_length=INSURANCE_CARD_MAX_CHARS, description="Photo of the card's front (image data URL)"
    )
    insurance_card_back: str | None = Field(
        default=None, max_length=INSURANCE_CARD_MAX_CHARS, description="Photo of the card's back (image data URL)"
    )
    emergency_contact_name: str | None = Field(default=None, max_length=120)
    emergency_contact_phone: str | None = Field(default=None, max_length=32)
    reason_for_visit: str | None = Field(default=None, max_length=2000)

    @field_validator(
        "address_line",
        "city",
        "state",
        "postal_code",
        "country",
        "insurance_provider",
        "insurance_plan_name",
        "insurance_member_id",
        "insurance_group_number",
        "insurance_policyholder_name",
        "emergency_contact_name",
        "emergency_contact_phone",
        "reason_for_visit",
        mode="before",
    )
    @classmethod
    def _optional_text(cls, v):
        return _clean(v) if isinstance(v, str) else v

    @field_validator("insurance_policyholder_relationship", mode="before")
    @classmethod
    def _blank_relationship(cls, v):
        return (v.strip().lower() or None) if isinstance(v, str) else v

    @field_validator("insurance_card_front", "insurance_card_back", mode="before")
    @classmethod
    def _blank_card(cls, v):
        """Empty string → None (the form sends "" for a side that was not photographed)."""
        if isinstance(v, str):
            v = v.strip()
            return v or None
        return v


class PatientIn(PatientIdentity, PatientDetails):
    """The full intake form."""

    # The patient confirms the details are theirs and may be stored by the office.
    consent: bool = Field(description="Must be true: the patient agrees to the office storing these details")

    @field_validator("consent")
    @classmethod
    def _consent(cls, v: bool) -> bool:
        if not v:
            raise ValueError("please confirm that the details may be stored")
        return v


class PatientUpdate(PatientDetails):
    """What a patient may change themselves (patient_updates.py): name, contact details and
    everything in ``PatientDetails`` — **not** the date of birth (the identity anchor; corrected
    at the front desk). Sent in full: a blank field clears it. The card photos are tri-state:
    a data URL replaces the side, ``remove_insurance_card_*`` drops it, otherwise it is kept."""

    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=32)
    remove_insurance_card_front: bool = False
    remove_insurance_card_back: bool = False

    @field_validator("first_name", "last_name", mode="before")
    @classmethod
    def _name(cls, v):
        return _name_value(v)

    @field_validator("email", mode="before")
    @classmethod
    def _blank_email(cls, v):
        return _clean(v) if isinstance(v, str) else v

    @field_validator("phone", mode="before")
    @classmethod
    def _phone(cls, v):
        return _phone_value(v)


class PatientAdminUpdate(PatientUpdate):
    """The office edits a record (PUT /patients/{id}, administrators): everything a patient may
    change, plus the date of birth — corrections at the desk are the one place it changes."""

    date_of_birth: date

    @field_validator("date_of_birth")
    @classmethod
    def _dob(cls, v: date) -> date:
        today = date.today()
        if v > today:
            raise ValueError("cannot be in the future")
        if v < today.replace(year=today.year - 130):
            raise ValueError("is not a plausible date of birth")
        return v


class PatientUpdateChallenge(BaseModel):
    """Answer of POST /patients/update/request: a code went to the contact details *on the record*."""

    challenge: str
    patient: PatientSummary
    # Masked destinations the code went to, e.g. "email:p•••@example.com", "sms:+•••••••0301".
    sent_to: list[str]
    channels: list[str]
    expires_in: int  # seconds the code stays valid
    # The built-in fake delivered it (development): where to read it.
    fake: bool = False
    outbox_url: str | None = None


class PatientUpdateConfirm(BaseModel):
    challenge: str = Field(min_length=10, max_length=2000)
    code: str = Field(min_length=4, max_length=12)


class PatientUpdateSession(BaseModel):
    """The code was right: a short-lived token to read and save the record with (header ``X-Edit-Token``)."""

    edit_token: str
    expires_in: int
    patient: PatientOut


class PatientSummary(BaseModel):
    """What the public check reveals about an existing record: enough to say "welcome back,
    your record number is …", not the record itself."""

    id: int
    mrn: str
    first_name: str
    last_name: str
    date_of_birth: date
    created_at: datetime


class InsuranceCardOut(BaseModel):
    """One side of the insurance card on file — what there is, not the image itself."""

    side: InsuranceCardSide
    content_type: str
    size_bytes: int
    uploaded_at: datetime
    # GET (administrators): the image bytes. Relative to the API prefix, like the calendar links.
    url: str


VerificationStatus = Literal["active", "inactive", "pending", "not_found", "incomplete", "unavailable"]
DeductibleStatus = Literal["met", "partially_met", "not_met", "none"]


class InsuranceVerificationOut(BaseModel):
    """One eligibility & benefits check (insurance_verification.py): coverage and effective
    dates, copay, deductible status, coinsurance, referral and prior-authorization requirements.
    Amounts are integer cents in ``currency``; None = the payer did not say."""

    id: int
    status: VerificationStatus
    message: str
    triggered_by: str  # registration | staff | seed
    provider: str  # fake | availity | …
    reference: str | None
    payer_name: str | None
    plan_name: str | None
    plan_type: str | None
    member_id: str | None
    group_number: str | None
    coverage_start: date | None
    coverage_end: date | None
    currency: str
    copay_cents: int | None
    specialist_copay_cents: int | None
    deductible_cents: int | None
    deductible_met_cents: int | None
    deductible_remaining_cents: int | None
    deductible_status: DeductibleStatus | None
    coinsurance_percent: int | None
    out_of_pocket_max_cents: int | None
    out_of_pocket_met_cents: int | None
    referral_required: bool | None
    referral_notes: str | None
    prior_auth_required: bool | None
    prior_auth_notes: str | None
    checked_at: datetime


class PatientOut(PatientSummary):
    """The full record (administrators, and the patient right after creating it)."""

    sex: str | None
    email: str | None
    phone: str | None
    address_line: str | None
    city: str | None
    state: str | None
    postal_code: str | None
    country: str | None
    insurance_provider: str | None
    insurance_plan_name: str | None
    insurance_member_id: str | None
    insurance_group_number: str | None
    insurance_policyholder_name: str | None
    insurance_policyholder_relationship: str | None
    # The card photos on file (metadata only; administrators fetch the image from ``url``).
    insurance_cards: list[InsuranceCardOut] = Field(default_factory=list)
    # The newest eligibility & benefits check, if any (run at registration and by the desk).
    insurance_verification: InsuranceVerificationOut | None = None
    emergency_contact_name: str | None
    emergency_contact_phone: str | None
    reason_for_visit: str | None
    updated_at: datetime
    # The medication and allergy lists (health_records.py) — current entries first.
    medications: list[HealthItemOut] = Field(default_factory=list)
    allergies: list[HealthItemOut] = Field(default_factory=list)
    medical_history: list[HealthItemOut] = Field(default_factory=list)  # conditions, surgeries, family / social


class PatientCheck(BaseModel):
    """Answer of POST /patients/check."""

    is_new: bool
    # Which rules matched the existing record: name_dob · email_dob · phone_dob
    matched_on: list[str] = Field(default_factory=list)
    patient: PatientSummary | None = None


class PatientRegistration(BaseModel):
    """Answer of POST /patients: ``new`` (201, the record was created — returned in full) or
    ``existing`` (200, nothing stored; the summary of the record that matched)."""

    status: Literal["new", "existing"]
    matched_on: list[str] = Field(default_factory=list)
    patient: PatientOut | PatientSummary


class PatientPage(BaseModel):
    items: list[PatientSummary]
    total: int
    offset: int
    limit: int


# ==================== Patient payment methods (patient_payment_methods.py) ====================
# What a signed-in patient keeps on file to pay the office: a credit/debit card, a Stripe or
# PayPal wallet, or a bank account. The payment detail itself (card number, bank account and
# routing number, the wallet's e-mail address) is accepted on the way in, validated, tokenized
# and *never* stored or echoed back — only a masked, recognisable form comes back.

PaymentMethodKind = Literal["card", "stripe", "paypal", "bank"]
BankAccountType = Literal["checking", "savings"]
# pending = not validated yet · active = the small-amount authorization succeeded · failed = declined
PaymentMethodStatus = Literal["pending", "active", "failed"]
PaymentValidationResult = Literal["authorized", "declined", "error"]


def _digits(value: str | None) -> str:
    return "".join(ch for ch in (value or "") if ch.isdigit())


class PatientPaymentMethodIn(BaseModel):
    """Create / update one payment method. Which fields matter depends on ``kind``:

    * ``card``   — ``holder_name``, ``card_number`` (+ ``card_cvc``, checked and dropped),
                   ``card_exp_month``, ``card_exp_year``, optional ``billing_postal_code``.
                   On update the number may be left out to keep the card as it is.
    * ``stripe`` / ``paypal`` — ``wallet_email`` (the account at that provider; may be left out
                   on update to keep the one on file), optional name.
    * ``bank``   — ``holder_name``, ``bank_name``, ``bank_account_type``, ``bank_routing_number``
                   and ``bank_account_number`` (both may be left out on update).
    """

    kind: PaymentMethodKind
    label: str | None = Field(default=None, max_length=60)
    holder_name: str | None = Field(default=None, max_length=120)
    is_default: bool = False
    # card
    card_number: str | None = Field(default=None, max_length=32)
    card_cvc: str | None = Field(default=None, max_length=4)
    card_exp_month: int | None = Field(default=None, ge=1, le=12)
    card_exp_year: int | None = Field(default=None, ge=2000, le=2100)
    billing_postal_code: str | None = Field(default=None, max_length=20)
    # stripe | paypal
    wallet_email: EmailStr | None = None
    # bank
    bank_name: str | None = Field(default=None, max_length=120)
    bank_account_type: BankAccountType | None = None
    bank_routing_number: str | None = Field(default=None, max_length=34)
    bank_account_number: str | None = Field(default=None, max_length=34)

    @field_validator("label", "holder_name", "billing_postal_code", "bank_name", "bank_routing_number", mode="before")
    @classmethod
    def _trim(cls, v):
        return _clean(v) if isinstance(v, str) else v

    @field_validator("wallet_email", mode="before")
    @classmethod
    def _blank_email(cls, v):
        return _clean(v) if isinstance(v, str) else v

    @field_validator("card_number", "card_cvc", "bank_account_number", mode="before")
    @classmethod
    def _secret_digits(cls, v):
        """Spaces and dashes are fine ("4242 4242 4242 4242"); blank → None."""
        if not isinstance(v, str):
            return v
        digits = _digits(v)
        return digits or None

    @field_validator("card_exp_year", mode="before")
    @classmethod
    def _two_digit_year(cls, v):
        """ "27" → 2027 (what is embossed on the card)."""
        if isinstance(v, str) and v.strip().isdigit():
            v = int(v.strip())
        if isinstance(v, int) and 0 <= v < 100:
            return 2000 + v
        return v

    @model_validator(mode="after")
    def _by_kind(self):
        if self.kind == "card":
            if self.card_exp_month is None or self.card_exp_year is None:
                raise ValueError("card_exp_month, card_exp_year: the card's expiry date is required")
            if self.card_number is not None and not 12 <= len(self.card_number) <= 19:
                raise ValueError("card_number: must have 12 to 19 digits")
            if self.card_cvc is not None and not 3 <= len(self.card_cvc) <= 4:
                raise ValueError("card_cvc: must have 3 or 4 digits")
        elif self.kind == "bank":
            if self.bank_account_number is not None and not 4 <= len(self.bank_account_number) <= 17:
                raise ValueError("bank_account_number: must have 4 to 17 digits")
            if self.bank_account_type is None:
                raise ValueError("bank_account_type: checking or savings")
        return self


class PaymentValidationOut(BaseModel):
    """One validation attempt: a small amount authorized against the vault token and released."""

    id: int
    result: PaymentValidationResult
    amount_cents: int
    currency: str
    message: str
    reference: str | None
    provider: str  # fake | stripe | paypal | … — "fake" = the built-in vault, nothing left the machine
    triggered_by: str  # patient | staff | system
    created_at: datetime


class PatientPaymentMethodOut(BaseModel):
    """One payment method as the dashboards show it — masked, recognisable, never the detail
    itself (which this service does not have: only the vault's token)."""

    id: int
    kind: PaymentMethodKind
    label: str | None
    holder_name: str | None
    is_default: bool
    # Usable only when "active": the last small-amount authorization succeeded.
    status: PaymentMethodStatus
    validated_at: datetime | None
    validation: PaymentValidationOut | None  # the newest attempt
    # A one-line description: "Visa •••• 4242 · 12/2030", "PayPal · pa•••@example.com", …
    display: str
    # Whether the entry can still be charged (a card past its expiry cannot).
    expired: bool = False
    card_brand: str | None = None
    card_last4: str | None = None
    card_exp_month: int | None = None
    card_exp_year: int | None = None
    billing_postal_code: str | None = None
    wallet_email_masked: str | None = None  # "pa•••@example.com"
    bank_name: str | None = None
    bank_account_type: BankAccountType | None = None
    bank_routing_masked: str | None = None  # "•••••0021"
    bank_account_last4: str | None = None
    provider: str | None = None
    created_at: datetime
    updated_at: datetime


# ---- the ledger (patient_payments.py) ----

PatientPaymentKind = Literal["copay", "self_pay", "statement", "other", "check_in"]
PatientPaymentStatus = Literal["authorized", "captured", "released", "declined", "refunded", "partially_refunded"]


class PaymentMethodRef(BaseModel):
    """The method a payment went against — as it was then. ``removed``: the method has since
    been deleted from the account (the history keeps its description all the same)."""

    id: int | None
    kind: PaymentMethodKind
    display: str
    removed: bool = False


class PaymentAppointmentRef(BaseModel):
    id: int
    number: str
    start_local: str
    provider: str  # "Dr. Alex Example"
    status: str  # AppointmentStatus (defined further down)


class PatientPaymentOut(BaseModel):
    """One charge / hold / refund (server: models.PatientPayment)."""

    id: int
    kind: PatientPaymentKind
    status: PatientPaymentStatus
    amount_cents: int
    refunded_cents: int
    currency: str
    description: str
    message: str
    method: PaymentMethodRef
    appointment: PaymentAppointmentRef | None
    patient: PatientSummary | None
    reference: str | None
    refund_reference: str | None
    provider: str
    created_by: str  # patient | staff | system
    created_at: datetime
    captured_at: datetime | None
    released_at: datetime | None
    refunded_at: datetime | None


class PaymentHistoryEntry(BaseModel):
    """One line of the payment history: a payment (hold, charge, refund) or a validation (the
    small authorization that is released at once)."""

    id: str  # "payment-12" | "validation-3"
    at: datetime
    entry: Literal["payment", "validation"]
    # payment: PatientPaymentKind · validation: "validation"
    kind: str
    # payment: PatientPaymentStatus · validation: PaymentValidationResult
    status: str
    amount_cents: int
    refunded_cents: int = 0
    currency: str
    description: str
    message: str
    method: PaymentMethodRef
    appointment: PaymentAppointmentRef | None = None
    patient: PatientSummary | None = None
    reference: str | None = None
    provider: str
    by: str  # patient | staff | system
    payment: PatientPaymentOut | None = None  # the full row for a payment line


class PaymentTotals(BaseModel):
    currency: str
    charged_cents: int  # captured, net of refunds
    refunded_cents: int
    held_cents: int  # authorized, not captured yet
    count: int


class PaymentHistory(BaseModel):
    """Answer of GET /me/payments and GET /patients/{id}/payments: newest first, every method."""

    entries: list[PaymentHistoryEntry]
    totals: PaymentTotals


# What the office-wide ledger can be narrowed to (patient_payments.office_router): which statuses
# each group covers is defined in patient_payments.SHOW_STATUSES.
OfficePaymentShow = Literal["all", "charges", "holds", "refunds", "released", "declined"]


class OfficePaymentCounts(BaseModel):
    """How many payments each ``show`` group holds for the current search (the filter chips)."""

    all: int
    charges: int
    holds: int
    refunds: int
    released: int
    declined: int


class OfficePaymentPage(BaseModel):
    """Answer of GET /patient-payments (administrators): one page of the office-wide ledger —
    every hold, charge, release and refund on every patient's payment methods, newest first.
    ``totals`` and ``counts`` cover the whole search (text, kind, dates), not just this page
    and not just the ``show`` group."""

    items: list[PaymentHistoryEntry]
    total: int  # rows in the ``show`` group (what the pager pages through)
    offset: int
    limit: int
    totals: PaymentTotals
    counts: OfficePaymentCounts


class PatientChargeIn(BaseModel):
    """The office charges a patient (a statement, a no-show fee …) against one of the methods."""

    method_id: int
    amount_cents: int = Field(gt=0, le=1_000_000)
    description: str = Field(min_length=1, max_length=200)
    kind: Literal["statement", "other"] = "statement"

    @field_validator("description", mode="before")
    @classmethod
    def _desc(cls, v):
        return _clean(v) if isinstance(v, str) else v


class PatientRefundIn(BaseModel):
    """Give (part of) a captured payment back; no amount = the rest of it."""

    amount_cents: int | None = Field(default=None, gt=0)
    reason: str | None = Field(default=None, max_length=200)


class PatientPaymentMethodsAdmin(BaseModel):
    """Answer of GET /patients/{id}/payment-methods (administrators): the methods on the
    patient account the record is linked to — none when the record has no account yet."""

    patient_id: int
    has_account: bool
    methods: list[PatientPaymentMethodOut]


# ==================== Appointments (appointments.py) ====================
# A registered patient books a slot with a provider. The patient proves who they are the same
# way the intake page does (name + date of birth, optional e-mail/phone) — no account needed.

AppointmentStatus = Literal["scheduled", "canceled", "completed", "no_show"]


class ProviderOut(BaseModel):
    """A member of staff patients can book with, and the weekdays they are in."""

    id: int
    staff_number: str
    first_name: str
    last_name: str
    role: str
    specialty: str | None
    # Weekdays (0 = Monday … 6 = Sunday) with at least one bookable slot.
    weekdays: list[int]


class ProviderList(BaseModel):
    providers: list[ProviderOut]
    slot_minutes: int
    max_days_ahead: int
    timezone: str  # IANA name of the office's time zone (slot labels are in it)
    # Booking needs a signed-in patient account with a validated payment method (the copay /
    # self-pay fee is put on hold when the visit is booked) — config.APPOINTMENT_REQUIRE_PAYMENT_METHOD.
    payment_method_required: bool = True
    self_pay_visit_cents: int = 0
    currency: str = "USD"


class SlotOut(BaseModel):
    start_at: datetime
    end_at: datetime
    label: str  # "09:30" in the office's time zone
    available: bool


class DaySlots(BaseModel):
    provider_id: int
    date: date
    timezone: str
    slots: list[SlotOut]
    # Why there are no slots at all (the provider is out that day, the date is in the past, …).
    note: str | None = None


class AppointmentRequest(PatientIdentity):
    """Book: who the patient is, with whom, when, and why."""

    provider_id: int
    start_at: datetime
    reason: str | None = Field(default=None, max_length=500)
    # Without active coverage for the day of the visit the patient has to agree to pay themselves
    # (POST /appointments/coverage tells beforehand); otherwise the booking is refused (409).
    self_pay_agreed: bool = False

    @field_validator("reason", mode="before")
    @classmethod
    def _reason(cls, v):
        return _clean(v) if isinstance(v, str) else v


class AppointmentLink(BaseModel):
    """The patient's link to one appointment (review · add to calendar · cancel) and how it was
    delivered. ``url`` is the link itself: the patient gets it right after booking (and on the
    lookup of their own appointments); the office sees it on the agenda to hand it over by hand."""

    url: str
    channels: list[str]  # e-mail / sms it actually went out on
    sent_to: list[str]  # masked destinations, e.g. "email:p•••@example.com"
    sent_at: datetime | None
    note: str | None  # provider errors, "built-in fake — read it at …"
    fake: bool  # the built-in fake delivered it: nothing left the machine
    outbox_url: str | None  # where to read a fake delivery (dev/test)
    opens: int
    last_opened_at: datetime | None


class CalendarLinks(BaseModel):
    """Add the appointment to a calendar: a downloadable .ics (phones open it in the calendar
    app) and pre-filled web links for Google Calendar and Outlook.com."""

    ics_url: str
    google_url: str
    outlook_url: str


class CoverageCheckRequest(PatientIdentity):
    """Before booking: is the patient's coverage good for the day of the visit?"""

    start_at: datetime | None = None  # the slot; today when omitted


class CoverageCheck(BaseModel):
    """Answer of POST /appointments/coverage — and what the booking rule decides."""

    covered: bool  # active coverage for the service date
    self_pay_required: bool  # not covered and the office requires coverage → agreement needed
    has_insurance: bool  # the record names an insurer / member id at all
    status: str  # the check's status (insurance_verification.STATUSES), or "none" without insurance
    message: str  # one line for the patient
    service_date: date
    verification: InsuranceVerificationOut | None = None


CheckInStatus = Literal["pending", "completed", "failed", "canceled"]


class CheckInSummary(BaseModel):
    """The latest check-in of an appointment (check_ins.py) — what the patient's board and the
    agenda show: pending (the page is open / the link was sent and expires at ``expires_at``),
    completed, failed (``failure_reason`` says why) or canceled."""

    id: int
    status: CheckInStatus
    via: str  # account · link · identity (the patient, in the app) · staff (the office sent the link)
    started_at: datetime
    expires_at: datetime
    link_channels: list[str] = Field(default_factory=list)
    link_sent_to: list[str] = Field(default_factory=list)  # masked destinations
    link_sent_at: datetime | None = None
    link_delivery_note: str | None = None
    failure_code: str | None = None
    failure_reason: str | None = None
    completed_at: datetime | None = None
    estimate_cents: int | None = None
    currency: str = "USD"
    payment_id: int | None = None
    # Completed: the appointment's provider was told the patient checked in (check_ins.notify_staff).
    staff_name: str | None = None  # "Dr. Alex Example"
    staff_notified_at: datetime | None = None
    staff_notify_channels: list[str] = Field(default_factory=list)
    staff_notified_to: list[str] = Field(default_factory=list)  # masked destinations
    staff_notify_note: str | None = None


ChartItemKind = Literal["medication", "allergy", "condition", "surgery", "family_history", "social_history"]


class HealthItemOut(BaseModel):
    """One entry of a patient's chart (health_records.py): a medication, an allergy or an item of the
    medical history (condition, surgery, family history, social history)."""

    id: int
    kind: ChartItemKind
    name: str
    detail: str | None
    status: Literal["active", "inactive"]
    source: str  # office · check_in · assistant · seed
    note: str | None
    last_reviewed_at: datetime | None
    last_reviewed_by: str | None  # the assistant's name
    created_at: datetime
    updated_at: datetime


class HealthItemIn(BaseModel):
    """The office adds an entry to a patient's list."""

    kind: ChartItemKind
    name: str = Field(min_length=1, max_length=200)
    detail: str | None = Field(default=None, max_length=300)
    note: str | None = Field(default=None, max_length=1000)

    @field_validator("name", "detail", "note", mode="before")
    @classmethod
    def _text(cls, v):
        return _clean(v) if isinstance(v, str) else v


class HealthItemPatch(BaseModel):
    """The office corrects an entry, or marks it no longer current (``status: inactive`` — with a note)."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    detail: str | None = Field(default=None, max_length=300)
    status: Literal["active", "inactive"] | None = None
    note: str | None = Field(default=None, max_length=1000)

    @field_validator("name", "detail", "note", mode="before")
    @classmethod
    def _text(cls, v):
        return _clean(v) if isinstance(v, str) else v


class VitalsOut(BaseModel):
    """The vital signs an assistant typed in (assistant_review.py) — ``text`` is the one-line summary
    ("BP 120/80 mmHg · pulse 72/min · 98.6 °F · 154 lb")."""

    bp_systolic: int | None = None
    bp_diastolic: int | None = None
    pulse: int | None = None
    temperature: float | None = None
    temperature_unit: Literal["F", "C"] = "F"
    weight: float | None = None
    weight_unit: Literal["lb", "kg"] = "lb"
    note: str | None = None
    text: str = ""


AssignmentStatus = Literal["assigned", "accepted", "completed", "reported", "canceled"]
AssignmentReportReason = Literal["schedule_conflict", "sick", "emergency", "not_qualified", "other"]


class AssignmentSummary(BaseModel):
    """The medical assistant on an appointment — its current task (assignments.py): what the agenda,
    the provider and the assistant see. ``needs_reassignment``: the assistant reported they cannot do
    it and the office has not assigned anybody else yet."""

    id: int
    status: AssignmentStatus
    worker_id: int
    staff_number: str
    name: str  # "Jamie Helper"
    instructions: str | None = None
    assigned_at: datetime
    accepted_at: datetime | None = None
    completed_at: datetime | None = None
    reported_at: datetime | None = None
    report_reason: str | None = None
    report_reason_label: str | None = None
    report_note: str | None = None
    needs_reassignment: bool = False
    # What the assistant recorded (once typed in): the vital signs — for the provider and the office.
    vitals: VitalsOut | None = None
    # Completed: the visit's provider was told the patient is ready (or why not).
    provider_notified_at: datetime | None = None
    provider_notify_channels: list[str] = Field(default_factory=list)
    provider_notify_note: str | None = None


class AppointmentOut(BaseModel):
    id: int
    number: str
    status: AppointmentStatus
    start_at: datetime
    end_at: datetime
    # The same moment in the office's time zone, ready to display ("Monday, June 7, 2027 · 09:30").
    start_local: str
    timezone: str
    reason: str | None
    provider: ProviderOut
    patient: PatientSummary
    created_at: datetime
    canceled_at: datetime | None = None
    office_name: str
    office_address: str | None = None
    link: AppointmentLink | None = None
    calendar: CalendarLinks | None = None
    # The patient confirmed they are coming (from the reminder's link / their account / the desk).
    confirmed_at: datetime | None = None
    confirmed_via: str | None = None
    # The reminder the day before: when it went out and on which channels (reminders.py).
    reminder_sent_at: datetime | None = None
    reminder_channels: list[str] = Field(default_factory=list)
    reminder_note: str | None = None
    # Who pays: "insurance" (coverage was active for the day) or "self_pay" (the patient agreed).
    payment: str = "insurance"
    coverage_status: str | None = None
    self_pay_agreed_at: datetime | None = None
    # The copay / self-pay hold placed at booking (patient_payments.py): charged after the visit.
    charge: PatientPaymentOut | None = None
    # Check-in (appointments.check_in): when and how (account · link · identity = the patients' app,
    # staff = the office). The patient's window opens CHECK_IN_WINDOW_HOURS before the visit and
    # closes when it ends; ``can_check_in`` = the patient may check in from the app right now.
    checked_in_at: datetime | None = None
    checked_in_via: str | None = None
    check_in_opens_at: datetime | None = None
    check_in_closes_at: datetime | None = None
    can_check_in: bool = False
    # The latest check-in attempt (the page, the office's link) — why it failed, when it expires.
    check_in: CheckInSummary | None = None
    # The medical assistant the office assigned to prepare the visit (assignments.py) — shown to the
    # office and to staff, never to the patient.
    assistant: AssignmentSummary | None = None
    # Staff-only remarks on the visit (the office administration dashboard only).
    notes: str | None = None
    # The provider started seeing the patient (their visit note — clinical.py); staff and office only.
    provider_seen_at: datetime | None = None
    # The after-visit summary (visit_summaries.py): the patient sees it once the doctor signed it; staff
    # and the office also see a draft that waits for the signature.
    visit_summary: VisitSummaryBrief | None = None


class ReminderRun(BaseModel):
    """Answer of POST /appointments/reminders/run (administrators)."""

    as_of: datetime
    due: int  # appointments inside the window without a reminder yet
    sent: int  # … for which at least one channel delivered
    numbers: list[str]
    simulated: bool  # as_of was supplied (dev/test only)


class AppointmentLookup(BaseModel):
    """Answer of POST /appointments/lookup: the patient and their upcoming appointments."""

    patient: PatientSummary
    appointments: list[AppointmentOut]


class AppointmentCancel(PatientIdentity):
    """The patient cancels one of their own appointments (identity checked again)."""


class AppointmentCheckIn(PatientIdentity):
    """The patient checks in for one of their own appointments from the lookup page (identity
    checked again, like a cancellation)."""


class AppointmentPage(BaseModel):
    items: list[AppointmentOut]
    total: int
    offset: int
    limit: int


class MyPatientRecord(PatientSummary):
    """A patient record that belongs to the signed-in patient account."""

    email: str | None
    phone: str | None
    # How it was attached: "email" (same address as the account) or "identity" (name + date of birth).
    linked_by: str


class MyAppointments(BaseModel):
    """Answer of GET /me/appointments: the account's records and every appointment on them."""

    patients: list[MyPatientRecord]
    upcoming: list[AppointmentOut]
    past: list[AppointmentOut]


class PatientLinkRequest(PatientIdentity):
    """Attach a patient record to the signed-in patient account (name + date of birth, like the
    intake page; e-mail / phone count too)."""


class AppointmentStaffUpdate(BaseModel):
    """A manager updates the status / notes of an appointment, records the patient's confirmation,
    or starts the check-in (``checked_in: true`` — the check-in page's link goes to the patient, any
    time while scheduled; ``false`` calls a pending check-in off or undoes a completed one)."""

    status: AppointmentStatus | None = None
    notes: str | None = Field(default=None, max_length=2000)
    confirmed: bool | None = None
    checked_in: bool | None = None

    @field_validator("notes", mode="before")
    @classmethod
    def _notes(cls, v):
        return _clean(v) if isinstance(v, str) else v


# ==================== Check-in page (check_ins.py) ====================


class CheckInOption(BaseModel):
    value: str
    label: str


class CheckInQuestion(BaseModel):
    """One question of the check-in questionnaire. ``kind``: text · textarea · choice (one of
    ``options``) · yesno · scale (0–10) · consent (must be ticked)."""

    id: str
    label: str
    kind: str
    required: bool
    options: list[CheckInOption] = Field(default_factory=list)
    hint: str | None = None
    max_length: int | None = None


class CheckInQuestionnaire(BaseModel):
    version: str
    title: str
    intro: str
    questions: list[CheckInQuestion]


class EstimateLine(BaseModel):
    label: str
    amount_cents: int


class CheckInEstimate(BaseModel):
    """The estimated cost of the visit not covered by insurance (check_ins.estimate): what the
    patient signs to be responsible for, and what is pre-authorized on the payment method."""

    currency: str
    total_cents: int
    lines: list[EstimateLine]
    covered: bool  # the coverage was active for the day of the visit
    coverage_status: str
    coverage_message: str
    notes: list[str] = Field(default_factory=list)
    # Where the pre-authorization goes: the default validated method on the patient's account.
    payment_method: str | None = None
    # The booking's copay / self-pay hold that the pre-authorization replaces (released then).
    replaces_hold_cents: int = 0


class CheckInSignature(BaseModel):
    """Typed full name + the drawn signature (PNG data URL, like the onboarding portal)."""

    signer_name: str = Field(min_length=2, max_length=200)
    image_png: str = Field(min_length=100, max_length=300_000, description="data:image/png;base64,…")

    @field_validator("signer_name", mode="before")
    @classmethod
    def _name(cls, v):
        return _name_value(v)


class CheckInSubmit(BaseModel):
    """The check-in page: the answers, the signature on them, the accepted estimate (the amount the
    patient was shown — a different current estimate is refused) and the financial signature."""

    answers: dict[str, str | int | bool | None]
    answers_signature: CheckInSignature
    estimate_cents: int = Field(ge=0)
    accept_financial_responsibility: bool
    financial_signature: CheckInSignature


class CheckInStart(BaseModel):
    """A check-in page was opened for the patient: go to ``url`` (the patients' app, /check-in/<token>)."""

    token: str
    url: str
    check_in: CheckInSummary


class CheckInSession(CheckInSummary):
    """The check-in page (GET /check-ins/{token}): the appointment, the questionnaire, the estimate
    — and, once processed, the result (completed: the pre-authorization; failed: why)."""

    appointment: AppointmentOut
    questionnaire: CheckInQuestionnaire
    estimate: CheckInEstimate | None = None
    answers: dict = Field(default_factory=dict)
    answers_signer_name: str | None = None
    answers_signed_at: datetime | None = None
    financial_signer_name: str | None = None
    financial_signed_at: datetime | None = None
    payment: PatientPaymentOut | None = None
    # The patient can start a new check-in from the app right now (after a failure, an expiry).
    can_restart: bool = False


class CheckInRecord(CheckInSummary):
    """Administrators: one check-in with everything the patient submitted (GET
    /appointments/{id}/check-ins) — answers with their questions, both signatures, the estimate."""

    questionnaire_version: str
    questions: list[CheckInQuestion]
    answers: dict
    answers_signer_name: str | None
    answers_signed_at: datetime | None
    answers_signature_png: str | None
    answers_esign_reference: str | None
    estimate: CheckInEstimate | None
    financial_signer_name: str | None
    financial_signed_at: datetime | None
    financial_signature_png: str | None
    financial_esign_reference: str | None
    esign_provider: str | None
    opened_at: datetime | None
    payment: PatientPaymentOut | None
    started_by: str | None  # e-mail of the staff member who sent the link


# ==================== Workers (workers.py) ====================
# Staff registration (doctor, nurse, …) and each worker's weekly availability. The office's
# standard week is Monday–Friday, 9 AM to 5 PM; new staff start with it.

WorkerRole = Literal[
    "doctor",
    "nurse",
    "physician_assistant",
    "medical_assistant",
    "technician",
    "receptionist",
    "administrator",
    "other",
]
EmploymentType = Literal["full_time", "part_time", "locum"]


class WorkerIdentity(BaseModel):
    """What identifies a member of staff for the "are you new?" check: name and work e-mail,
    plus the optional phone / licence number that also count as a match."""

    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    phone: str | None = Field(default=None, max_length=32)
    license_number: str | None = Field(default=None, max_length=64)

    @field_validator("first_name", "last_name", mode="before")
    @classmethod
    def _name(cls, v):
        return _name_value(v)

    @field_validator("phone", "emergency_contact_phone", mode="before", check_fields=False)
    @classmethod
    def _phone(cls, v):
        return _phone_value(v)

    @field_validator("license_number", mode="before")
    @classmethod
    def _license(cls, v):
        return _clean(v) if isinstance(v, str) else v


class WorkerIn(WorkerIdentity):
    """The full staff registration form."""

    role: WorkerRole
    specialty: str | None = Field(default=None, max_length=120)
    start_date: date | None = None
    employment_type: EmploymentType | None = None
    emergency_contact_name: str | None = Field(default=None, max_length=120)
    emergency_contact_phone: str | None = Field(default=None, max_length=32)
    notes: str | None = Field(default=None, max_length=2000)
    consent: bool = Field(description="Must be true: the worker agrees to the office keeping these details")

    @field_validator("specialty", "emergency_contact_name", "emergency_contact_phone", "notes", mode="before")
    @classmethod
    def _optional_text(cls, v):
        return _clean(v) if isinstance(v, str) else v

    @field_validator("consent")
    @classmethod
    def _consent(cls, v: bool) -> bool:
        if not v:
            raise ValueError("please confirm that the details may be stored")
        return v


class WorkerSummary(BaseModel):
    """What the public check reveals about an existing worker."""

    id: int
    staff_number: str
    first_name: str
    last_name: str
    role: str
    created_at: datetime


class DayAvailability(BaseModel):
    """One weekday of a worker's week. ``weekday``: 0 = Monday … 6 = Sunday."""

    weekday: int = Field(ge=0, le=6)
    available: bool
    start_time: time
    end_time: time
    note: str | None = Field(default=None, max_length=200)

    @field_validator("note", mode="before")
    @classmethod
    def _note(cls, v):
        return _clean(v) if isinstance(v, str) else v

    @model_validator(mode="after")
    def _order(self):
        if self.available and self.end_time <= self.start_time:
            raise ValueError(f"{WEEKDAY_NAMES[self.weekday]}: the end time must be after the start time")
        return self


WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


class WorkerOut(WorkerSummary):
    """The full record (administrators, and the worker right after registering)."""

    specialty: str | None
    license_number: str | None
    email: str
    phone: str | None
    start_date: date | None
    employment_type: str | None
    emergency_contact_name: str | None
    emergency_contact_phone: str | None
    notes: str | None
    active: bool
    updated_at: datetime
    availability: list[DayAvailability]
    onboarding: WorkerOnboardingState | None = None


class WorkerCheck(BaseModel):
    is_new: bool
    matched_on: list[str] = Field(default_factory=list)  # email · license · name_phone
    worker: WorkerSummary | None = None


class WorkerRegistration(BaseModel):
    status: Literal["new", "existing"]
    matched_on: list[str] = Field(default_factory=list)
    worker: WorkerOut | WorkerSummary


class WorkerCredentials(BaseModel):
    """How a worker identifies themself on the availability page: the staff number they got at
    registration plus their work e-mail (both must match)."""

    staff_number: str = Field(min_length=1, max_length=16, description="e.g. W-000042")
    email: EmailStr


class AvailabilityOut(BaseModel):
    worker: WorkerSummary
    standard_week: list[DayAvailability]  # the office's default, for "reset"
    availability: list[DayAvailability]


class StaffEnrollmentStart(WorkerCredentials):
    """Create a staff dashboard account, step 1: who you are (staff number + work e-mail). A code
    goes to the work e-mail on the staff record."""


class StaffEnrollmentSent(BaseModel):
    enrollment: str  # signed, short-lived; sent back with the code in step 2
    sent_to: str  # masked: "d•••@example.com"
    expires_in: int  # seconds
    fake: bool = False  # the built-in fake delivered it (development): read it in the test inbox
    outbox_url: str | None = None


class StaffAccountCreate(BaseModel):
    """Step 2: the code from the e-mail and the new password's client-derived keys (like
    /auth/register — the password itself never reaches the server)."""

    enrollment: str = Field(min_length=16, max_length=2048)
    code: str = Field(min_length=4, max_length=16)
    auth_key: str = Field(min_length=32, max_length=256, description="Client-derived, never the password")
    wrapped_data_key: str = Field(min_length=16, max_length=4096)
    kdf_params: dict = Field(default_factory=dict)
    device: DeviceInfo = Field(default_factory=DeviceInfo)


class StaffMe(BaseModel):
    """The signed-in member of staff: the account and the staff record it is linked to."""

    account_id: str
    email: str
    role: str
    worker: WorkerOut
    timezone: str  # the office's time zone — every time on the staff pages is in it


class StaffAppointmentPage(BaseModel):
    """GET /staff/me/appointments: the visits where the signed-in worker is the provider."""

    worker: WorkerSummary
    timezone: str
    date_from: date
    date_to: date
    items: list[AppointmentOut]
    total: int


class StaffCheckInAnswers(BaseModel):
    """The questionnaire of a completed check-in, for the provider of the visit (no financial part,
    no signature image — those stay with the office)."""

    appointment_id: int
    completed_at: datetime | None
    via: str
    questions: list[CheckInQuestion]
    answers: dict
    signer_name: str | None
    signed_at: datetime | None


class StaffAvailabilityUpdate(BaseModel):
    availability: list[DayAvailability] = Field(min_length=1, max_length=7)

    @field_validator("availability")
    @classmethod
    def _unique_days(cls, v: list[DayAvailability]) -> list[DayAvailability]:
        days = [d.weekday for d in v]
        if len(days) != len(set(days)):
            raise ValueError("each weekday may appear only once")
        return sorted(v, key=lambda d: d.weekday)


class AvailabilityUpdate(WorkerCredentials):
    availability: list[DayAvailability] = Field(min_length=1, max_length=7)

    @field_validator("availability")
    @classmethod
    def _unique_days(cls, v: list[DayAvailability]) -> list[DayAvailability]:
        days = [d.weekday for d in v]
        if len(days) != len(set(days)):
            raise ValueError("each weekday may appear only once")
        return sorted(v, key=lambda d: d.weekday)


class WorkerPage(BaseModel):
    items: list[WorkerSummary]
    total: int
    offset: int
    limit: int


# ==================== Medical assistants (assignments.py) ====================


class KeyLabel(BaseModel):
    key: str
    label: str


class AssignmentOut(AssignmentSummary):
    """One task in an appointment's history (the office): who assigned it, what became of it."""

    appointment_id: int
    appointment_number: str
    duties: list[str] = Field(default_factory=list)  # the duty keys confirmed at completion
    completion_note: str | None = None
    assigned_by: str | None = None  # e-mail of the administrator (None = the seed / system)
    resolved_at: datetime | None = None  # a report the office acted on
    resolved_by: str | None = None
    replaced_by_id: int | None = None
    canceled_at: datetime | None = None
    cancel_reason: str | None = None
    notified_at: datetime | None = None  # the message to the assistant about the task
    notify_channels: list[str] = Field(default_factory=list)
    notified_to: list[str] = Field(default_factory=list)  # masked destinations
    notify_note: str | None = None
    # The review (once the assistant worked on it): each medication / allergy with its decision and note.
    review_items: list[ReviewItemOut] = Field(default_factory=list)
    check_in_note: str | None = None


class AssistantCandidate(BaseModel):
    """A medical assistant the office could assign to the visit — and whether they are available:
    in on that weekday, the visit inside their hours, not with another patient at that time, and
    not someone who already reported they cannot take this visit."""

    worker_id: int
    staff_number: str
    first_name: str
    last_name: str
    hours: str | None  # their hours on the visit's weekday ("09:00–17:00"); None = not in that day
    available: bool
    reason: str | None = None  # why not
    current: bool = False  # the assistant on the visit right now


class AssistantOptions(BaseModel):
    """GET /appointments/{id}/assistants: who can be assigned (available ones first)."""

    appointment_id: int
    number: str
    status: AppointmentStatus
    start_local: str
    timezone: str
    current: AssignmentSummary | None
    candidates: list[AssistantCandidate]


class AssignmentRequest(BaseModel):
    """The office assigns (or reassigns) a medical assistant to an appointment."""

    worker_id: int
    instructions: str | None = Field(default=None, max_length=1000)

    @field_validator("instructions", mode="before")
    @classmethod
    def _text(cls, v):
        return _clean(v) if isinstance(v, str) else v


class AssignmentPage(BaseModel):
    """GET /assignments: the office's task board."""

    items: list[AssignmentOut]
    total: int
    open: int  # assigned / accepted, across the office
    needs_reassignment: int  # reported, nobody assigned instead yet


class TaskAppointment(BaseModel):
    """The appointment behind a task, as the assistant sees it (no payment or insurance details)."""

    id: int
    number: str
    status: AppointmentStatus
    start_at: datetime
    end_at: datetime
    start_local: str
    timezone: str
    reason: str | None
    provider_name: str  # "Dr. Alex Example"
    provider_role: str
    patient: PatientSummary
    confirmed_at: datetime | None = None
    checked_in_at: datetime | None = None
    check_in: CheckInSummary | None = None


class AssistantTaskOut(BaseModel):
    """One task of the signed-in medical assistant, with what they may do next."""

    id: int
    status: AssignmentStatus
    instructions: str | None
    assigned_at: datetime
    accepted_at: datetime | None
    completed_at: datetime | None
    duties: list[str]
    completion_note: str | None
    reported_at: datetime | None
    report_reason: str | None
    report_reason_label: str | None
    report_note: str | None
    canceled_at: datetime | None
    cancel_reason: str | None
    appointment: TaskAppointment
    can_accept: bool
    can_complete: bool  # taken, the visit is on and its time has come (complete_opens_at)
    can_report: bool
    complete_opens_at: datetime
    has_check_in_answers: bool
    vitals: VitalsOut | None = None


class AssistantTaskPage(BaseModel):
    """GET /staff/me/tasks: the signed-in medical assistant's tasks, in time order."""

    worker: WorkerSummary
    timezone: str
    date_from: date
    date_to: date
    items: list[AssistantTaskOut]
    total: int
    duties: list[KeyLabel]  # what completing a task confirms
    report_reasons: list[KeyLabel]


class TaskCompletion(BaseModel):
    """Complete a task: the review (saved with it — see TaskReviewIn) must be complete: every duty done.
    ``note``: anything for the provider / the office."""

    review: TaskReviewIn | None = None
    note: str | None = Field(default=None, max_length=2000)

    @field_validator("note", mode="before")
    @classmethod
    def _text(cls, v):
        return _clean(v) if isinstance(v, str) else v


class TaskReport(BaseModel):
    """The assistant cannot do the task: why — the office assigns it to someone else."""

    reason: AssignmentReportReason
    note: str | None = Field(default=None, max_length=1000)

    @field_validator("note", mode="before")
    @classmethod
    def _text(cls, v):
        return _clean(v) if isinstance(v, str) else v

    @model_validator(mode="after")
    def _explained(self):
        if self.reason == "other" and not self.note:
            raise ValueError("note: please say why you cannot do this task")
        return self


HealthItemKind = Literal["medication", "allergy"]


class VitalsIn(BaseModel):
    """The vital signs as typed in. Every field is optional while the review is saved as a draft;
    completing the task needs all four measurements (blood pressure, pulse, temperature, weight).
    Implausible values are refused (a typo, or the wrong unit)."""

    bp_systolic: int | None = Field(default=None, ge=50, le=260, description="mmHg")
    bp_diastolic: int | None = Field(default=None, ge=30, le=160, description="mmHg")
    pulse: int | None = Field(default=None, ge=20, le=250, description="beats per minute")
    temperature: float | None = Field(default=None, gt=0, le=120)
    temperature_unit: Literal["F", "C"] = "F"
    weight: float | None = Field(default=None, gt=0, le=1000)
    weight_unit: Literal["lb", "kg"] = "lb"
    note: str | None = Field(default=None, max_length=500, description="e.g. taken twice, patient seated")

    @field_validator("note", mode="before")
    @classmethod
    def _text(cls, v):
        return _clean(v) if isinstance(v, str) else v

    @model_validator(mode="after")
    def _plausible(self):
        if self.bp_systolic is not None and self.bp_diastolic is not None and self.bp_systolic <= self.bp_diastolic:
            raise ValueError("blood pressure: the systolic (upper) value must be above the diastolic (lower) one")
        if self.temperature is not None:
            low, high = (90.0, 110.0) if self.temperature_unit == "F" else (32.0, 43.5)
            if not low <= self.temperature <= high:
                raise ValueError(
                    f"temperature: {self.temperature:g} °{self.temperature_unit} is not plausible "
                    f"({low:g}–{high:g} °{self.temperature_unit}) — check the value and the unit"
                )
        if self.weight is not None:
            low, high = (1.0, 1000.0) if self.weight_unit == "lb" else (0.5, 450.0)
            if not low <= self.weight <= high:
                raise ValueError(
                    f"weight: {self.weight:g} {self.weight_unit} is not plausible — check the value and the unit"
                )
        return self


class ReviewItemIn(BaseModel):
    """One medication / allergy of the review. With ``id``: an item of the list (name and detail may be
    corrected); without: an item the assistant adds (``kind`` and ``name`` required). ``decision``:
    confirmed (current, as shown) · removed (not current / entered in error — a note is required for an
    entry of the chart) · pending (not cleared — undo); None = keep the decision."""

    id: int | None = None
    kind: HealthItemKind | None = None
    name: str | None = Field(default=None, min_length=1, max_length=200)
    detail: str | None = Field(default=None, max_length=300)
    decision: Literal["confirmed", "removed", "pending"] | None = None
    note: str | None = Field(default=None, max_length=1000)

    @field_validator("name", "detail", "note", mode="before")
    @classmethod
    def _text(cls, v):
        return _clean(v) if isinstance(v, str) else v


class TaskReviewIn(BaseModel):
    """The assistant's review of the visit — saved as a draft (PUT …/review) or with the completion.
    Every field is optional: what is sent is changed, the rest kept."""

    room_patient: bool | None = None
    vitals: VitalsIn | None = None
    items: list[ReviewItemIn] = Field(default_factory=list, max_length=100)
    no_medications: bool | None = None  # the patient takes none (the list is empty after the review)
    no_allergies: bool | None = None  # no known allergies
    check_in_reviewed: bool | None = None
    check_in_note: str | None = Field(default=None, max_length=1000)
    provider_ready: bool | None = None

    @field_validator("check_in_note", mode="before")
    @classmethod
    def _text(cls, v):
        return _clean(v) if isinstance(v, str) else v


class ReviewItemOut(BaseModel):
    id: int
    kind: HealthItemKind
    name: str
    detail: str | None
    origin: str  # record · check_in · both · assistant
    flag: str | None  # match · changed · new · not_mentioned · said_none
    flag_text: str | None  # the hint for the assistant, e.g. "The check-in says: 20 mg once daily"
    record_detail: str | None
    check_in_text: str | None
    decision: Literal["confirmed", "removed"] | None
    note: str | None
    reviewed_at: datetime | None
    health_item_id: int | None


class TaskReviewOut(BaseModel):
    """GET /staff/me/tasks/{id}/review: the form of the visit — prepopulated medication and allergy
    lists (the chart + the check-in answers), the vital signs, the ticked parts — and what is still
    missing before the task can be completed (``problems``)."""

    task_id: int
    status: AssignmentStatus
    editable: bool  # taken, the visit is on and its time has come
    complete_opens_at: datetime
    room_patient: bool
    vitals: VitalsOut | None
    medications: list[ReviewItemOut]
    allergies: list[ReviewItemOut]
    no_medications: bool
    no_allergies: bool
    patient_said_no_medications: bool  # the check-in answer was "none"
    patient_said_no_allergies: bool
    check_in: StaffCheckInAnswers | None  # the patient's answers (None: no completed check-in)
    check_in_reviewed: bool
    check_in_note: str | None
    provider_ready: bool
    duties: list[KeyLabel]
    done: list[str]  # the duty keys that are done
    problems: list[str]  # what is missing before completing (empty = ready)


# ==================== Hiring: applications & onboarding (hiring.py) ====================

ApplicationStatus = Literal["submitted", "screening", "hired", "rejected", "withdrawn"]
CheckStatus = Literal["pending", "in_progress", "passed", "failed", "not_applicable"]
OnboardingItemStatus = Literal["pending", "done", "not_applicable"]


class ApplicationIn(BaseModel):
    """The job application form (public). The age check runs on submission; licence details
    are validated for format and expiry — a manager then confirms them with the issuing board."""

    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    phone: str | None = Field(default=None, max_length=32)
    date_of_birth: date
    role: WorkerRole
    specialty: str | None = Field(default=None, max_length=120)
    employment_type: EmploymentType | None = None
    available_from: date | None = None
    years_experience: int | None = Field(default=None, ge=0, le=60)
    license_number: str | None = Field(default=None, max_length=64)
    license_authority: str | None = Field(default=None, max_length=120)
    license_expires: date | None = None
    certifications: str | None = Field(default=None, max_length=2000)
    cover_note: str | None = Field(default=None, max_length=4000)
    emergency_contact_name: str | None = Field(default=None, max_length=120)
    emergency_contact_phone: str | None = Field(default=None, max_length=32)
    # The candidate agrees to a background screening and to the office keeping the application.
    background_consent: bool = Field(description="Must be true")
    consent: bool = Field(description="Must be true")

    @field_validator("first_name", "last_name", mode="before")
    @classmethod
    def _name(cls, v):
        return _name_value(v)

    @field_validator("phone", "emergency_contact_phone", mode="before")
    @classmethod
    def _phone(cls, v):
        return _phone_value(v)

    @field_validator(
        "specialty",
        "license_number",
        "license_authority",
        "certifications",
        "cover_note",
        "emergency_contact_name",
        mode="before",
    )
    @classmethod
    def _optional_text(cls, v):
        return _clean(v) if isinstance(v, str) else v

    @field_validator("date_of_birth")
    @classmethod
    def _dob(cls, v: date) -> date:
        today = date.today()
        if v > today:
            raise ValueError("cannot be in the future")
        if v < today.replace(year=today.year - 100):
            raise ValueError("is not a plausible date of birth")
        return v

    @field_validator("background_consent", "consent")
    @classmethod
    def _must_agree(cls, v: bool) -> bool:
        if not v:
            raise ValueError("please tick the box to continue")
        return v


class CheckResult(BaseModel):
    """One of the three pre-hire checks."""

    status: CheckStatus
    note: str | None = None
    reference: str | None = None  # background screening: provider reference
    checked_at: datetime | None = None
    checked_by: str | None = None  # administrator's e-mail


class ApplicationSummary(BaseModel):
    id: int
    number: str
    first_name: str
    last_name: str
    role: str
    specialty: str | None
    status: ApplicationStatus | str
    age_check: CheckStatus | str
    license_check: CheckStatus | str
    background_check: CheckStatus | str
    worker_id: int | None
    created_at: datetime
    updated_at: datetime


class ApplicationOut(ApplicationSummary):
    email: str
    phone: str | None
    date_of_birth: date
    age: int
    employment_type: str | None
    available_from: date | None
    years_experience: int | None
    license_number: str | None
    license_authority: str | None
    license_expires: date | None
    certifications: str | None
    cover_note: str | None
    emergency_contact_name: str | None
    emergency_contact_phone: str | None
    background_consent: bool
    checks: dict[str, CheckResult]  # age · license · background
    can_hire: bool
    blockers: list[str]  # why not (human-readable)
    decision_note: str | None
    decided_at: datetime | None
    decided_by: str | None
    staff_number: str | None = None
    onboarding_id: int | None = None
    last_invite: InviteOut | None = None  # after hiring: the onboarding link that was sent


class ApplicationReceipt(BaseModel):
    """What the candidate sees after submitting: the number and the automatic results."""

    number: str
    status: str
    age_check: CheckStatus | str
    age_note: str | None
    license_check: CheckStatus | str
    license_note: str | None
    background_check: CheckStatus | str
    message: str


class ApplicationStatusLookup(BaseModel):
    number: str = Field(min_length=1, max_length=16)
    email: EmailStr


class ApplicationStatusOut(BaseModel):
    number: str
    first_name: str
    role: str
    status: str
    age_check: str
    license_check: str
    background_check: str
    submitted_at: datetime
    updated_at: datetime
    staff_number: str | None = None
    message: str


class CheckUpdate(BaseModel):
    """A manager records the outcome of the licence or background check."""

    status: CheckStatus
    note: str | None = Field(default=None, max_length=500)
    reference: str | None = Field(default=None, max_length=120)

    @field_validator("note", "reference", mode="before")
    @classmethod
    def _text(cls, v):
        return _clean(v) if isinstance(v, str) else v


class DecisionIn(BaseModel):
    note: str | None = Field(default=None, max_length=1000)
    # hire: override the start date / employment type from the application
    start_date: date | None = None
    employment_type: EmploymentType | None = None

    @field_validator("note", mode="before")
    @classmethod
    def _text(cls, v):
        return _clean(v) if isinstance(v, str) else v


class ApplicationPage(BaseModel):
    items: list[ApplicationSummary]
    total: int
    offset: int
    limit: int
    counts: dict[str, int]  # status → count (all applications)


class OnboardingItemOut(BaseModel):
    key: str
    label: str
    position: int
    status: OnboardingItemStatus | str
    note: str | None
    completed_at: datetime | None
    completed_by: str | None  # administrator's e-mail
    who: str = "office"  # hire = the new hire completes it in the portal · office = a manager ticks it off
    submission: SubmissionOut | None = None


class OnboardingSummary(BaseModel):
    id: int
    worker: WorkerSummary
    application_number: str | None
    started_at: datetime
    # Every step done (the manager can approve) — not yet "complete".
    all_steps_done: bool = False
    steps_completed_at: datetime | None = None
    # The manager's electronic approval ("onboarding is finished") makes it complete.
    approved_at: datetime | None = None
    approved_by: str | None = None  # administrator's e-mail
    approval_signer_name: str | None = None
    approval_esign_reference: str | None = None
    completed_at: datetime | None
    done: int
    total: int
    percent: int
    next_step: str | None  # label of the first pending item


class OnboardingOut(OnboardingSummary):
    notes: str | None
    items: list[OnboardingItemOut]
    invites: list[InviteOut] = Field(default_factory=list)  # newest first; links included (administrators)


class OnboardingItemUpdate(BaseModel):
    status: OnboardingItemStatus
    note: str | None = Field(default=None, max_length=500)

    @field_validator("note", mode="before")
    @classmethod
    def _text(cls, v):
        return _clean(v) if isinstance(v, str) else v


class OnboardingNotes(BaseModel):
    notes: str | None = Field(default=None, max_length=4000)


class OnboardingApproval(BaseModel):
    """The manager signs off: every step is done, onboarding is finished."""

    signature: PortalSignature
    note: str | None = Field(default=None, max_length=1000)


class OnboardingPage(BaseModel):
    items: list[OnboardingSummary]
    total: int
    in_progress: int
    completed: int


class AdminAvailabilityUpdate(BaseModel):
    """A manager edits a worker's week (no staff-number/e-mail needed)."""

    availability: list[DayAvailability] = Field(min_length=1, max_length=7)


# ==================== Onboarding portal (invites.py, portal.py) ====================

InviteChannel = Literal["email", "sms"]


class InviteRequest(BaseModel):
    channels: list[InviteChannel] | None = Field(default=None, description="Default: every channel on file")
    kind: Literal["remind", "start", "hire", "manual"] = "remind"


class InviteOut(BaseModel):
    """One invite link as logged — including the link itself (administrators only)."""

    id: int
    kind: str
    link: str
    # Where the person / the administrator finds the link (fake delivery → API console + test inbox).
    where_to_find: str = ""
    fake_delivery: bool = False
    channels: list[str]
    sent_to: str  # masked destinations, e.g. "email:j•••@example.com, sms:+•••••••0123"
    delivery_note: str | None
    created_at: datetime
    created_by: str | None
    expires_at: datetime
    expired: bool
    revoked_at: datetime | None
    first_opened_at: datetime | None
    last_opened_at: datetime | None
    opens: int
    valid: bool


class SubmissionOut(BaseModel):
    """What the new hire submitted for one step (the signature image is left out of lists)."""

    data: dict
    document_version: str | None
    signer_name: str | None
    signed_at: datetime | None
    esign_reference: str | None
    esign_provider: str | None
    submitted_at: datetime
    has_signature: bool


class PortalSignature(BaseModel):
    signer_name: str = Field(min_length=2, max_length=200)
    image_png: str = Field(min_length=100, max_length=300_000, description="data:image/png;base64,…")
    # E-SIGN Act style consent: the signer agrees to sign electronically.
    consent: bool

    @field_validator("signer_name", mode="before")
    @classmethod
    def _name(cls, v):
        return _name_value(v)

    @field_validator("consent")
    @classmethod
    def _agree(cls, v: bool) -> bool:
        if not v:
            raise ValueError("please agree to sign electronically")
        return v


class PortalSubmit(BaseModel):
    data: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    signature: PortalSignature | None = None


class PortalWorker(BaseModel):
    first_name: str
    last_name: str
    role: str
    staff_number: str
    start_date: date | None
    email: str | None  # masked


class PortalStep(BaseModel):
    key: str
    who: Literal["hire", "office"] | str
    title: str
    intro: str
    document: str
    version: str
    signature: bool
    fields: list[dict]
    status: str
    note: str | None
    office_note: str = ""
    submission: SubmissionOut | None = None


class PortalState(BaseModel):
    worker: PortalWorker
    expires_at: datetime
    done: int
    total: int
    percent: int
    completed_at: datetime | None  # approved by the office
    all_steps_done: bool = False
    approved_at: datetime | None = None
    steps: list[PortalStep]
    esign_provider: str
    esign_fake: bool


class WorkerOnboardingState(BaseModel):
    id: int
    started_at: datetime
    all_steps_done: bool = False
    approved_at: datetime | None = None
    completed_at: datetime | None
    done: int
    total: int
    percent: int


# ==================== The clinical record & the provider's chart (clinical.py) ====================


class ResultValue(BaseModel):
    """One value of a lab result. ``flag``: H (high) · L (low) · A (abnormal) · "" (normal / none)."""

    name: str = Field(min_length=1, max_length=120)
    value: str = Field(min_length=1, max_length=60)
    unit: str | None = Field(default=None, max_length=30)
    range: str | None = Field(default=None, max_length=60, description="The reference range, e.g. 70–99")
    flag: Literal["", "H", "L", "A"] = ""

    @field_validator("name", "value", "unit", "range", mode="before")
    @classmethod
    def _text(cls, v):
        return _clean(v) if isinstance(v, str) else v


class ResultIn(BaseModel):
    """The office enters a lab result (with its values) or an imaging report (the impression)."""

    kind: Literal["lab", "imaging"]
    name: str = Field(min_length=1, max_length=200)
    status: Literal["pending", "preliminary", "final", "amended"] = "final"
    ordered_on: date | None = None
    resulted_on: date | None = None
    performed_by: str | None = Field(default=None, max_length=200)
    summary: str | None = Field(default=None, max_length=5000)
    values: list[ResultValue] = Field(default_factory=list, max_length=60)
    appointment_id: int | None = None

    @field_validator("name", "performed_by", "summary", mode="before")
    @classmethod
    def _text(cls, v):
        return _clean(v) if isinstance(v, str) else v

    @model_validator(mode="after")
    def _dates(self):
        if self.ordered_on and self.resulted_on and self.resulted_on < self.ordered_on:
            raise ValueError("resulted_on: the result cannot come before the order")
        if self.kind == "imaging" and self.values:
            raise ValueError("values: an imaging report has an impression (summary), not values")
        return self


class ResultPatch(BaseModel):
    """Correct / amend a result (what is sent is changed)."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    status: Literal["pending", "preliminary", "final", "amended"] | None = None
    resulted_on: date | None = None
    performed_by: str | None = Field(default=None, max_length=200)
    summary: str | None = Field(default=None, max_length=5000)
    values: list[ResultValue] | None = Field(default=None, max_length=60)


class ResultOut(BaseModel):
    id: int
    kind: Literal["lab", "imaging"]
    name: str
    status: str
    ordered_on: date | None
    resulted_on: date | None
    performed_by: str | None
    summary: str | None
    values: list[ResultValue]
    abnormal: bool  # any value flagged
    appointment_id: int | None
    appointment_number: str | None
    ordered_by: str | None  # the provider's name
    created_at: datetime
    updated_at: datetime


class ReferralIn(BaseModel):
    """A referral note: to a specialist (outgoing) or from another practice (incoming)."""

    direction: Literal["incoming", "outgoing"]
    counterpart: str = Field(min_length=1, max_length=200, description="The other provider / practice")
    specialty: str | None = Field(default=None, max_length=120)
    referral_date: date
    valid_until: date | None = None
    reason: str = Field(min_length=1, max_length=500)
    note: str | None = Field(default=None, max_length=5000)
    status: Literal["open", "scheduled", "completed", "closed"] = "open"
    appointment_id: int | None = None

    @field_validator("counterpart", "specialty", "reason", "note", mode="before")
    @classmethod
    def _text(cls, v):
        return _clean(v) if isinstance(v, str) else v

    @model_validator(mode="after")
    def _dates(self):
        if self.valid_until and self.valid_until < self.referral_date:
            raise ValueError("valid_until: must not be before the referral date")
        return self


class ReferralPatch(BaseModel):
    status: Literal["open", "scheduled", "completed", "closed"] | None = None
    valid_until: date | None = None
    note: str | None = Field(default=None, max_length=5000)


class ReferralDeliveryOut(BaseModel):
    method: Literal["email", "pdf"]
    recipient: str  # specialist · patient · office · "-" (a PDF download)
    destination: str | None  # masked
    status: str  # sent · failed · downloaded
    note: str | None
    by: str | None  # the account's e-mail
    created_at: datetime


class ReferralOut(BaseModel):
    id: int
    number: str  # REF-000001
    direction: Literal["incoming", "outgoing"]
    counterpart: str
    specialty: str | None
    referral_date: date
    valid_until: date | None
    expired: bool  # past valid_until and still open / scheduled
    reason: str
    note: str | None
    status: str
    appointment_id: int | None
    appointment_number: str | None = None
    created_at: datetime
    updated_at: datetime
    # Written by a provider on the chart (referrals.py) — a signed letter; empty for office-entered notes.
    letter: bool = False
    provider_id: int | None = None
    provider_name: str | None = None
    urgency: str | None = None
    diagnosis: DiagnosisOut | None = None
    question: str | None = None
    clinical_summary: str | None = None
    include_results: bool = False
    counterpart_email: str | None = None
    counterpart_phone: str | None = None
    counterpart_fax: str | None = None
    counterpart_address: str | None = None
    signed_at: datetime | None = None
    verification: str | None = None  # "REF-000002-1A2B3C4D5E"
    withdrawn_at: datetime | None = None
    withdraw_reason: str | None = None
    deliveries: list[ReferralDeliveryOut] = Field(default_factory=list)


def _multiline(value: str | None) -> str | None:
    """Trim every line, keep paragraphs (at most one blank line in a row); empty → None."""
    if value is None:
        return None
    lines = [" ".join(line.split()) for line in value.replace("\r\n", "\n").split("\n")]
    out: list[str] = []
    for line in lines:
        if line or (out and out[-1]):
            out.append(line)
    text = "\n".join(out).strip()
    return text or None


class ReferralWriteIn(BaseModel):
    """A referral the provider writes on the chart — signed when it is sent; ``send_to_specialist`` e-mails
    the letter (PDF) to ``counterpart_email``, ``send_to_patient`` a copy to the patient's e-mail."""

    counterpart: str = Field(min_length=2, max_length=200, description="The specialist / practice")
    specialty: str = Field(min_length=2, max_length=120)
    urgency: Literal["routine", "urgent"] = "routine"
    reason: str = Field(min_length=3, max_length=500)
    diagnosis_id: int | None = None
    question: str | None = Field(default=None, max_length=2000)
    clinical_summary: str | None = Field(default=None, max_length=5000)
    include_results: bool = False
    counterpart_email: EmailStr | None = None
    counterpart_phone: str | None = Field(default=None, max_length=32)
    counterpart_fax: str | None = Field(default=None, max_length=32)
    counterpart_address: str | None = Field(default=None, max_length=300)
    valid_until: date | None = None
    send_to_specialist: bool = False
    send_to_patient: bool = False

    @field_validator("counterpart", "specialty", "reason", "counterpart_phone", "counterpart_fax", mode="before")
    @classmethod
    def _text(cls, v):
        return _clean(v) if isinstance(v, str) else v

    @field_validator("counterpart_address", "counterpart_email", mode="before")
    @classmethod
    def _blank(cls, v):
        return _clean(v) if isinstance(v, str) else v

    @field_validator("question", "clinical_summary", mode="before")
    @classmethod
    def _paragraphs(cls, v):
        return _multiline(v) if isinstance(v, str) else v

    @model_validator(mode="after")
    def _send(self):
        if self.send_to_specialist and not self.counterpart_email:
            raise ValueError("counterpart_email: needed to e-mail the letter to the specialist")
        return self


class ReferralSend(BaseModel):
    to: Literal["specialist", "patient"]


class ReferralWithdraw(BaseModel):
    reason: str = Field(min_length=3, max_length=500)

    @field_validator("reason", mode="before")
    @classmethod
    def _text(cls, v):
        return _clean(v) if isinstance(v, str) else v


class VisitNoteIn(BaseModel):
    """The provider's note of the visit. ``seen: true`` records that the provider started seeing the
    patient (once); the texts are saved as sent (None = keep)."""

    seen: bool | None = None
    note: str | None = Field(default=None, max_length=10000)
    assessment: str | None = Field(default=None, max_length=5000)
    plan: str | None = Field(default=None, max_length=5000)


class VisitNoteOut(BaseModel):
    appointment_id: int
    provider_name: str | None
    seen_at: datetime | None
    note: str | None
    assessment: str | None
    plan: str | None
    updated_at: datetime


class PreviousVisit(BaseModel):
    """An earlier visit of the patient, as the chart lists it."""

    id: int
    number: str
    status: AppointmentStatus
    start_at: datetime
    start_local: str
    provider_name: str
    provider_role: str
    reason: str | None
    checked_in_at: datetime | None
    vitals: VitalsOut | None  # what the medical assistant recorded
    assistant_name: str | None
    assistant_note: str | None
    check_in_reason: str | None  # "What is the main reason for this visit?" as the patient answered
    visit_note: VisitNoteOut | None
    diagnoses: list[DiagnosisOut] = Field(default_factory=list)
    prescriptions: list[str] = Field(default_factory=list)  # "RX-000001 · Lisinopril 10 mg tablet"
    visit_summary: VisitSummaryBrief | None = None  # the after-visit summary (signed / a draft)


class ChartPatient(PatientSummary):
    sex: str | None
    age: int
    email_masked: str | None = None  # where a copy of a referral letter would go ("p•••@example.com")
    insurance_provider: str | None
    insurance_plan_name: str | None
    insurance_verification: InsuranceVerificationOut | None


class ProviderChart(BaseModel):
    """GET /staff/me/appointments/{id}/chart: everything the provider needs to see the patient — today's
    visit (the check-in answers, the medical assistant's work, the provider's own note), the medication
    and allergy lists, the medical history, lab and imaging results, referral notes and previous visits."""

    appointment: AppointmentOut
    patient: ChartPatient
    check_in: StaffCheckInAnswers | None
    assistant_work: AssignmentOut | None
    visit_note: VisitNoteOut | None
    medications: list[HealthItemOut]
    allergies: list[HealthItemOut]
    medical_history: list[HealthItemOut]
    results: list[ResultOut]
    referrals: list[ReferralOut]
    previous_visits: list[PreviousVisit]
    # Today: the provider's diagnoses; the patient's prescriptions (newest first); where to send them.
    diagnoses: list[DiagnosisOut] = Field(default_factory=list)
    prescriptions: list[PrescriptionOut] = Field(default_factory=list)
    pharmacies: list[PharmacyOut] = Field(default_factory=list)
    patient_pharmacy: PatientPharmacyOut | None = None  # the pharmacy the patient uses
    can_prescribe: bool = False  # the provider's role may diagnose and prescribe (doctor, physician assistant)
    can_refer: bool = False  # … and write referrals (the same roles)
    immunizations: list[ImmunizationOut] = Field(default_factory=list)  # newest dose first (immunizations.py)
    generated_at: datetime


# ==================== Diagnoses and prescriptions (prescriptions.py) ====================

# ICD-10-CM: a letter, two characters, optionally a dot and up to four more ("I10", "E11.9", "R73.03").
ICD10_PATTERN = r"^[A-Z][0-9][0-9A-Z](\.[0-9A-Z]{1,4})?$"


class DiagnosisIn(BaseModel):
    """The provider records a diagnosis of the visit. ``add_to_history``: a lasting condition goes on
    the patient's medical history too."""

    description: str = Field(min_length=2, max_length=300)
    code: str | None = Field(default=None, max_length=10, description="ICD-10-CM code, e.g. I10")
    primary: bool = False
    note: str | None = Field(default=None, max_length=1000)
    add_to_history: bool = False

    @field_validator("description", "note", mode="before")
    @classmethod
    def _text(cls, v):
        return _clean(v) if isinstance(v, str) else v

    @field_validator("code", mode="before")
    @classmethod
    def _code(cls, v):
        if isinstance(v, str):
            v = v.strip().upper() or None
            if v and not re.match(ICD10_PATTERN, v):
                raise ValueError("not an ICD-10 code (e.g. I10, E11.9, R73.03)")
        return v


class DiagnosisOut(BaseModel):
    id: int
    appointment_id: int
    appointment_number: str
    description: str
    code: str | None
    primary: bool
    note: str | None
    provider_name: str | None
    in_history: bool  # added to the medical history as a condition
    created_at: datetime


DosageForm = Literal[
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
]


class PrescriptionItemIn(BaseModel):
    """One medication: what (drug, strength, form), how to take it (sig), how much, refills."""

    drug: str = Field(min_length=2, max_length=200)
    strength: str | None = Field(default=None, max_length=60, description="e.g. 20 mg")
    form: DosageForm = "tablet"
    sig: str = Field(min_length=3, max_length=500, description="Directions, e.g. Take 1 tablet by mouth once daily")
    quantity: int = Field(ge=1, le=1000)
    quantity_unit: str | None = Field(
        default=None, max_length=30, description="tablets, mL, inhalers … (default by form)"
    )
    refills: int = Field(default=0, ge=0, le=11)
    substitution_allowed: bool = True
    patient_instructions: str | None = Field(
        default=None,
        max_length=1000,
        description="Plain-language advice for the patient (shown on the patient dashboard), "
        "e.g. Take it with food. Stand up slowly if you feel dizzy.",
    )

    @field_validator("drug", "strength", "sig", "quantity_unit", "patient_instructions", mode="before")
    @classmethod
    def _text(cls, v):
        return _clean(v) if isinstance(v, str) else v


class PrescriptionIn(BaseModel):
    """Sign and issue a prescription. ``pharmacy_id``: e-mail it to that pharmacy right away (else it is
    handed over as a PDF — it can be e-mailed later). ``allergy_override_reason``: needed when a medication
    matches one of the patient's allergies."""

    items: list[PrescriptionItemIn] = Field(min_length=1, max_length=10)
    diagnosis_id: int | None = None
    note_to_pharmacist: str | None = Field(default=None, max_length=1000)
    pharmacy_id: int | None = None
    allergy_override_reason: str | None = Field(default=None, max_length=500)

    @field_validator("note_to_pharmacist", "allergy_override_reason", mode="before")
    @classmethod
    def _text(cls, v):
        return _clean(v) if isinstance(v, str) else v


class PrescriptionItemOut(BaseModel):
    drug: str
    strength: str | None
    form: str
    sig: str
    quantity: int
    quantity_unit: str
    refills: int
    substitution_allowed: bool
    line: str  # "Lisinopril 20 mg tablet"
    patient_instructions: str | None = None


class PrescriptionDeliveryOut(BaseModel):
    method: Literal["email", "pdf"]
    pharmacy_id: int | None
    pharmacy_name: str | None
    destination: str | None  # masked
    status: str  # sent · failed · downloaded
    note: str | None
    by: str | None  # the account's e-mail
    created_at: datetime


class PrescriptionOut(BaseModel):
    id: int
    number: str  # RX-000001
    status: Literal["issued", "voided"]
    issued_at: datetime
    prescriber_id: int | None
    prescriber_name: str | None
    patient_id: int
    appointment_id: int | None
    appointment_number: str | None
    diagnosis: DiagnosisOut | None
    items: list[PrescriptionItemOut]
    note_to_pharmacist: str | None
    allergy_override: str | None
    voided_at: datetime | None
    void_reason: str | None
    verification: str
    deliveries: list[PrescriptionDeliveryOut]


class PrescriptionSend(BaseModel):
    pharmacy_id: int


class PrescriptionVoid(BaseModel):
    reason: str = Field(min_length=3, max_length=500)

    @field_validator("reason", mode="before")
    @classmethod
    def _text(cls, v):
        return _clean(v) if isinstance(v, str) else v


class PharmacyIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=32)
    fax: str | None = Field(default=None, max_length=32)
    address: str | None = Field(default=None, max_length=300)

    @field_validator("name", "phone", "fax", "address", mode="before")
    @classmethod
    def _text(cls, v):
        return _clean(v) if isinstance(v, str) else v

    @field_validator("email", mode="before")
    @classmethod
    def _blank(cls, v):
        return _clean(v) if isinstance(v, str) else v


class PharmacyPatch(PharmacyIn):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    active: bool | None = None


class PharmacyOut(BaseModel):
    id: int
    name: str
    email: str | None
    phone: str | None
    fax: str | None
    address: str | None
    active: bool
    source: str = "office"  # office · provider (added at a visit) · seed
    added_by: str | None = None


class PharmacyDetailsPatch(PharmacyIn):
    """What the patient tells the provider about their pharmacy (a new e-mail address, phone, …)."""

    name: str | None = Field(default=None, min_length=2, max_length=200)


class PatientPharmacyIn(BaseModel):
    """The patient's pharmacy: one of the directory (``pharmacy_id``), or a new one the patient named
    (``pharmacy`` — added to the directory); neither = the patient has no preferred pharmacy."""

    pharmacy_id: int | None = None
    pharmacy: PharmacyIn | None = None

    @model_validator(mode="after")
    def _one(self):
        if self.pharmacy_id is not None and self.pharmacy is not None:
            raise ValueError("give pharmacy_id or pharmacy, not both")
        return self


class PatientPharmacyOut(BaseModel):
    pharmacy: PharmacyOut | None
    set_at: datetime | None  # last confirmed / changed
    set_by: str | None  # "Dr. Alex Example at APT-000003" / the office


# ==================== Visit summaries (visit_summaries.py) ====================


class VisitSummaryContent(BaseModel):
    """What the summary says, generated from the chart (frozen when the doctor signs). Lists are ready-made
    lines in plain words — the patient's app, the staff dashboard and the PDF show them as they are."""

    visit_number: str
    visit_date: str  # "Wednesday, August 19, 2026 · 10:00" in the office's time zone
    visit_start_at: datetime
    timezone: str
    office_name: str
    office_address: str | None = None
    office_phone: str | None = None
    provider_name: str
    provider_role: str  # "Doctor", "Physician assistant", …
    provider_specialty: str | None = None
    patient_name: str
    patient_mrn: str
    patient_date_of_birth: date
    reason: str | None = None  # what the patient came for (check-in answer, else the booking)
    vitals: str | None = None  # "BP 146/92 mmHg · pulse 78/min · 98.4 °F · 158 lb"
    vitals_note: str | None = None
    diagnoses: list[str] = Field(default_factory=list)  # "Essential hypertension (I10) — primary"
    prescriptions: list[str] = Field(default_factory=list)  # medications prescribed at this visit
    medications: list[str] = Field(default_factory=list)  # the current medication list
    allergies: list[str] = Field(default_factory=list)
    referrals: list[str] = Field(default_factory=list)  # referrals written at this visit
    tests: list[str] = Field(default_factory=list)  # lab / imaging ordered at this visit
    # The visit note's assessment and plan when the draft was (re)built — the defaults of the texts below.
    note_assessment: str | None = None
    note_plan: str | None = None


class VisitSummaryBrief(BaseModel):
    """On every appointment: is there a summary, and where does it stand."""

    id: int
    number: str  # VS-000042
    version: int
    status: str  # draft · signed · superseded
    signed_at: datetime | None = None
    signer_name: str | None = None
    sent_at: datetime | None = None
    patient_viewed_at: datetime | None = None


class VisitSummaryVersion(BaseModel):
    """An earlier version (superseded by an amendment)."""

    id: int
    number: str
    version: int
    status: str
    signed_at: datetime | None = None
    signer_name: str | None = None
    superseded_at: datetime | None = None
    verification: str | None = None


class VisitSummaryOut(BaseModel):
    """The visit summary — the draft (staff / office) or the signed version (also the patient)."""

    id: int
    number: str
    appointment_id: int
    appointment_number: str
    patient_id: int
    version: int
    status: str
    content: VisitSummaryContent
    # The texts the patient reads: the assessment and the plan (the visit note's unless rewritten for the
    # patient — ``assessment_edited`` / ``plan_edited``), instructions, the follow-up.
    assessment: str | None = None
    plan: str | None = None
    assessment_edited: bool = False
    plan_edited: bool = False
    instructions: str | None = None
    follow_up: str | None = None
    generated_at: datetime
    generated_by: str
    provider_name: str | None = None
    # The signature: who, when, the drawn image, the e-signature reference and the code on the PDF.
    signer_name: str | None = None
    signed_at: datetime | None = None
    signature_png: str | None = None
    esign_reference: str | None = None
    verification: str | None = None  # shown as "VS-000042-ABCDEF1234"
    amendment_reason: str | None = None
    superseded_at: datetime | None = None
    # Drafts only: the provider was told it waits for them.
    provider_notified_at: datetime | None = None
    provider_notify_channels: list[str] = Field(default_factory=list)
    # Handed to the patient: e-mail (PDF attached) / a text that it is ready.
    sent_at: datetime | None = None
    sent_channels: list[str] = Field(default_factory=list)
    sent_to: list[str] = Field(default_factory=list)
    delivery_note: str | None = None
    patient_viewed_at: datetime | None = None
    # Staff: may the signed-in provider sign it (and if not, why).
    can_sign: bool = False
    sign_blocker: str | None = None
    previous_versions: list[VisitSummaryVersion] = Field(default_factory=list)
    updated_at: datetime


class VisitSummaryUpdate(BaseModel):
    """The provider edits the draft. A field left out stays as it is; ``null`` / "" for the assessment or
    the plan goes back to the visit note's text."""

    assessment: str | None = Field(default=None, max_length=5000)
    plan: str | None = Field(default=None, max_length=5000)
    instructions: str | None = Field(default=None, max_length=5000)
    follow_up: str | None = Field(default=None, max_length=1000)

    @field_validator("assessment", "plan", "instructions", "follow_up", mode="before")
    @classmethod
    def _text(cls, v):
        if isinstance(v, str):
            v = v.strip()
            return v or None
        return v


class VisitSummarySign(BaseModel):
    """The doctor signs the summary: typed full name + drawn signature + consent to sign electronically.
    ``send_to_patient``: e-mail it (PDF attached) and / or text that it is ready, right away."""

    signature: PortalSignature
    send_to_patient: bool = True


class VisitSummaryAmend(BaseModel):
    """A signed summary has a mistake: a new version (a draft prefilled from it) that supersedes it once
    signed."""

    reason: str = Field(min_length=3, max_length=500)

    @field_validator("reason", mode="before")
    @classmethod
    def _text(cls, v):
        return _clean(v) if isinstance(v, str) else v


class VisitSummaryRun(BaseModel):
    """Answer of POST /appointments/visit-summaries/run (administrators): drafts prepared for visits that are over."""

    as_of: datetime
    prepared: int
    appointments: list[str] = Field(default_factory=list)


# ==================== Immunizations (immunizations.py) ====================


class ImmunizationIn(BaseModel):
    """The office enters a dose of a vaccine (given here, or reported by another clinic / the patient's card)."""

    vaccine: str = Field(min_length=2, max_length=200)
    cvx_code: str | None = Field(default=None, max_length=8, description="CDC CVX code, e.g. 150")
    date_given: date
    dose_number: int | None = Field(default=None, ge=1, le=20)
    series_doses: int | None = Field(default=None, ge=1, le=20)
    manufacturer: str | None = Field(default=None, max_length=120)
    lot_number: str | None = Field(default=None, max_length=60)
    site: str | None = Field(default=None, max_length=60)
    route: Literal["IM", "SC", "ID", "oral", "intranasal", "other"] | None = None
    administered_by: str | None = Field(default=None, max_length=200, description="A person or another clinic")
    worker_id: int | None = Field(default=None, description="The member of staff who gave it here")
    source: Literal["office", "external"] = "office"
    note: str | None = Field(default=None, max_length=1000)

    @field_validator(
        "vaccine", "cvx_code", "manufacturer", "lot_number", "site", "administered_by", "note", mode="before"
    )
    @classmethod
    def _text(cls, v):
        return _clean(v) if isinstance(v, str) else v

    @field_validator("date_given")
    @classmethod
    def _not_future(cls, v: date) -> date:
        if v > date.today():
            raise ValueError("cannot be in the future")
        return v

    @model_validator(mode="after")
    def _series(self):
        if self.dose_number and self.series_doses and self.dose_number > self.series_doses:
            raise ValueError("dose_number: cannot be more than the doses of the series")
        return self


class ImmunizationPatch(BaseModel):
    """Correct an entry, or mark it entered in error (it stays in the history, off every report)."""

    vaccine: str | None = Field(default=None, min_length=2, max_length=200)
    cvx_code: str | None = Field(default=None, max_length=8)
    date_given: date | None = None
    dose_number: int | None = Field(default=None, ge=1, le=20)
    series_doses: int | None = Field(default=None, ge=1, le=20)
    manufacturer: str | None = Field(default=None, max_length=120)
    lot_number: str | None = Field(default=None, max_length=60)
    site: str | None = Field(default=None, max_length=60)
    route: Literal["IM", "SC", "ID", "oral", "intranasal", "other"] | None = None
    administered_by: str | None = Field(default=None, max_length=200)
    note: str | None = Field(default=None, max_length=1000)
    status: Literal["active", "entered_in_error"] | None = None

    @field_validator(
        "vaccine", "cvx_code", "manufacturer", "lot_number", "site", "administered_by", "note", mode="before"
    )
    @classmethod
    def _text(cls, v):
        return _clean(v) if isinstance(v, str) else v


class ImmunizationOut(BaseModel):
    id: int
    vaccine: str
    cvx_code: str | None
    date_given: date
    dose_number: int | None
    series_doses: int | None
    dose_label: str | None  # "dose 2 of 3"
    manufacturer: str | None
    lot_number: str | None
    site: str | None
    route: str | None
    administered_by: str | None  # the staff member's name when given here
    source: str
    status: str
    note: str | None
    line: str  # "Influenza — 10/01/2026 · dose 1 of 1 · Example Vaccines · lot DUMMY-LOT-0001 · left deltoid · IM"
    created_at: datetime
    updated_at: datetime


# ==================== Patient reports (patient_reports.py) ====================

PatientReportKind = Literal["immunization", "annual_exam"]
PatientReportPurpose = Literal["school", "work", "sports", "travel", "camp", "personal", "other"]


class ReportImmunization(BaseModel):
    """One dose as the report prints it."""

    vaccine: str
    date_given: date
    dose: str | None = None  # "2 of 3"
    product: str | None = None  # "Example Vaccines · lot DUMMY-LOT-0001"
    site_route: str | None = None  # "left deltoid · IM"
    given_by: str | None = None


class ReportContent(BaseModel):
    """The part of a report generated from the record (frozen when the doctor signs)."""

    kind: str
    title: str
    office_name: str
    office_address: str | None = None
    office_phone: str | None = None
    patient_name: str
    patient_mrn: str
    patient_date_of_birth: date
    patient_sex: str | None = None
    purpose: str
    purpose_label: str  # "School — Exampleville High School"
    generated_for: str | None = None  # the doctor's name
    # Both kinds.
    allergies: list[str] = Field(default_factory=list)
    immunizations: list[ReportImmunization] = Field(default_factory=list)
    # The annual health examination.
    exam_visit_number: str | None = None
    exam_date: str | None = None  # "Friday, March 5, 2027 · 10:00"
    exam_start_at: datetime | None = None
    exam_provider: str | None = None
    vitals: str | None = None
    blood_pressure: str | None = None
    pulse: str | None = None
    temperature: str | None = None
    weight: str | None = None
    visit_assessment: str | None = None
    visit_plan: str | None = None
    visit_diagnoses: list[str] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)
    surgeries: list[str] = Field(default_factory=list)
    family_history: list[str] = Field(default_factory=list)
    social_history: list[str] = Field(default_factory=list)
    medications: list[str] = Field(default_factory=list)
    recent_results: list[str] = Field(default_factory=list)  # the last 12 months
    # What the office and the doctor should know before signing ("The exam visit was 14 months ago", …).
    warnings: list[str] = Field(default_factory=list)


class ExamFinding(BaseModel):
    status: Literal["normal", "abnormal", "not_examined"] = "not_examined"
    note: str | None = Field(default=None, max_length=500)

    @field_validator("note", mode="before")
    @classmethod
    def _text(cls, v):
        return (v.strip() or None) if isinstance(v, str) else v


class ReportFindings(BaseModel):
    """The doctor's part. The annual health examination: measurements, the examination by system, the
    fitness decision (+ restrictions), recommendations, when the next exam is due. The immunization record:
    contraindications / exemptions and the vaccines that are due or recommended."""

    height: str | None = Field(default=None, max_length=30)
    weight: str | None = Field(default=None, max_length=30)
    blood_pressure: str | None = Field(default=None, max_length=30)
    vision: str | None = Field(default=None, max_length=100)
    hearing: str | None = Field(default=None, max_length=100)
    systems: dict[str, ExamFinding] = Field(default_factory=dict)
    fitness: Literal["fit", "fit_with_restrictions", "not_fit", "further_evaluation"] | None = None
    restrictions: str | None = Field(default=None, max_length=2000)
    recommendations: str | None = Field(default=None, max_length=2000)
    next_exam_due: date | None = None
    contraindications: str | None = Field(default=None, max_length=2000)
    vaccines_due: str | None = Field(default=None, max_length=2000)

    @field_validator(
        "height",
        "weight",
        "blood_pressure",
        "vision",
        "hearing",
        "restrictions",
        "recommendations",
        "contraindications",
        "vaccines_due",
        mode="before",
    )
    @classmethod
    def _text(cls, v):
        return (v.strip() or None) if isinstance(v, str) else v


class ReportRequestIn(BaseModel):
    """A patient asks for a report in the app (for one of the account's records)."""

    patient_id: int
    kind: PatientReportKind
    purpose: PatientReportPurpose = "personal"
    purpose_detail: str | None = Field(default=None, max_length=200, description="e.g. the school or employer")
    needed_by: date | None = None
    note: str | None = Field(default=None, max_length=1000)

    @field_validator("purpose_detail", "note", mode="before")
    @classmethod
    def _text(cls, v):
        return _clean(v) if isinstance(v, str) else v

    @field_validator("needed_by")
    @classmethod
    def _future(cls, v: date | None) -> date | None:
        if v is not None and v < date.today():
            raise ValueError("must not be in the past")
        return v


class OfficeReportRequestIn(BaseModel):
    """The office records a request (at the desk / on the phone) — and prepares it at once when a doctor is
    given (``worker_id``)."""

    kind: PatientReportKind
    purpose: PatientReportPurpose = "personal"
    purpose_detail: str | None = Field(default=None, max_length=200)
    needed_by: date | None = None
    note: str | None = Field(default=None, max_length=1000)
    worker_id: int | None = None
    appointment_id: int | None = None

    @field_validator("purpose_detail", "note", mode="before")
    @classmethod
    def _text(cls, v):
        return _clean(v) if isinstance(v, str) else v


class ReportGenerateIn(BaseModel):
    """The office prepares the report from the record for a doctor to sign (again, after a decline)."""

    worker_id: int
    appointment_id: int | None = Field(default=None, description="Annual exam: the exam visit it is based on")


class ReportUpdate(BaseModel):
    """The doctor edits their part of a draft (a field left out stays as it is)."""

    findings: ReportFindings | None = None
    doctor_note: str | None = Field(default=None, max_length=5000)

    @field_validator("doctor_note", mode="before")
    @classmethod
    def _text(cls, v):
        return (v.strip() or None) if isinstance(v, str) else v


class ReportSign(BaseModel):
    signature: PortalSignature
    send_to_patient: bool = True


class ReportReason(BaseModel):
    """Why: declining (the doctor), canceling (office / patient), amending (the doctor)."""

    reason: str = Field(min_length=3, max_length=500)

    @field_validator("reason", mode="before")
    @classmethod
    def _text(cls, v):
        return _clean(v) if isinstance(v, str) else v


class ReportDoctor(BaseModel):
    id: int
    staff_number: str
    name: str  # "Dr. Alex Example"
    specialty: str | None = None


class ReportVisit(BaseModel):
    id: int
    number: str
    start_at: datetime
    start_local: str
    provider_name: str
    reason: str | None = None
    status: str


class ReportOptions(BaseModel):
    """What the office chooses from when it prepares a report: the doctors, the patient's past visits."""

    doctors: list[ReportDoctor]
    visits: list[ReportVisit]
    suggested_worker_id: int | None = None
    suggested_appointment_id: int | None = None  # the latest annual / physical exam, else the latest visit


class PatientReportOut(BaseModel):
    """A report — everything for the office and the doctor; the patient gets the signed content only."""

    id: int
    number: str  # RPT-000042
    kind: str
    kind_label: str
    status: str
    status_label: str
    version: int
    patient_id: int
    patient_name: str
    patient_mrn: str
    purpose: str
    purpose_label: str
    purpose_detail: str | None = None
    needed_by: date | None = None
    request_note: str | None = None
    requested_via: str
    requested_at: datetime
    worker_id: int | None = None
    doctor_name: str | None = None
    appointment_id: int | None = None
    appointment_number: str | None = None
    content: ReportContent | None = None
    generated_at: datetime | None = None
    findings: ReportFindings = Field(default_factory=ReportFindings)
    doctor_note: str | None = None
    signer_name: str | None = None
    signed_at: datetime | None = None
    signature_png: str | None = None
    esign_reference: str | None = None
    verification: str | None = None  # "RPT-000042-ABCDEF1234"
    decline_reason: str | None = None
    declined_at: datetime | None = None
    cancel_reason: str | None = None
    canceled_at: datetime | None = None
    amendment_reason: str | None = None
    superseded_at: datetime | None = None
    replaces_id: int | None = None
    doctor_notified_at: datetime | None = None
    doctor_notify_channels: list[str] = Field(default_factory=list)
    sent_at: datetime | None = None
    sent_channels: list[str] = Field(default_factory=list)
    sent_to: list[str] = Field(default_factory=list)
    delivery_note: str | None = None
    patient_viewed_at: datetime | None = None
    # The doctor (staff): may they sign it (state / role) — if not, why; and what their part still lacks.
    can_sign: bool = False
    sign_blocker: str | None = None
    missing: list[str] = Field(default_factory=list)
    updated_at: datetime


class PatientReportPage(BaseModel):
    items: list[PatientReportOut]
    total: int
    counts: dict[str, int] = Field(default_factory=dict)  # per status, for the queue's filters


# ==================== My medicines (patient_medications.py) ====================


class MedicationEntry(BaseModel):
    """One medicine on the patient dashboard: what it is, how to take it, what for, who prescribed it."""

    name: str  # "Lisinopril"
    strength: str | None = None  # "10 mg"
    form: str | None = None  # "tablet"
    line: str  # "Lisinopril 10 mg tablet" (or the list entry's name)
    directions: str | None = None  # the sig: "Take 1 tablet by mouth once daily"
    patient_instructions: str | None = None  # the prescriber's plain-language advice
    indication: str | None = None  # what it is for (the prescription's diagnosis)
    status: Literal["current", "stopped"]
    prescribed: bool  # prescribed at this office (else: from the medication list, e.g. another doctor)
    prescription_id: int | None = None
    prescription_number: str | None = None
    prescribed_on: datetime | None = None
    prescriber_name: str | None = None
    quantity: int | None = None
    quantity_unit: str | None = None
    refills: int | None = None
    sent_to_pharmacy: str | None = None  # the pharmacy it was e-mailed to
    note: str | None = None  # why it was stopped / the list's note
    updated_at: datetime | None = None


class MedicationHistoryEntry(BaseModel):
    """One line of a prescription issued at the office (voided prescriptions left out)."""

    prescription_id: int
    prescription_number: str
    prescribed_on: datetime
    prescriber_name: str | None
    line: str
    directions: str
    patient_instructions: str | None = None
    quantity: int
    quantity_unit: str
    refills: int
    indication: str | None = None


class MyMedicationAllergy(BaseModel):
    name: str
    detail: str | None = None


class PatientMedications(BaseModel):
    """The medicines of one of the account's patient records — one tab on the patient dashboard."""

    patient_id: int
    patient_name: str
    first_name: str
    mrn: str
    date_of_birth: date
    allergies: list[MyMedicationAllergy] = Field(default_factory=list)
    current: list[MedicationEntry] = Field(default_factory=list)
    stopped: list[MedicationEntry] = Field(default_factory=list)
    history: list[MedicationHistoryEntry] = Field(default_factory=list)
    pharmacy_name: str | None = None  # the patient's pharmacy (set at a visit)
    pharmacy_phone: str | None = None
    last_reviewed_at: datetime | None = None  # the latest review of the list with the patient


class MyMedications(BaseModel):
    """Answer of GET /me/medications: one entry per patient record on the account."""

    records: list[PatientMedications]
    office_name: str
    office_phone: str | None = None
    generated_at: datetime


# Models that refer to schemas defined further down (as strings) are completed now that everything exists.
for _model in [
    m for m in list(globals().values()) if isinstance(m, type) and issubclass(m, BaseModel) and m is not BaseModel
]:
    if not _model.__pydantic_complete__:
        _model.model_rebuild()
