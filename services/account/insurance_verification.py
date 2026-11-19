"""Insurance eligibility and benefits verification — a strategy with a built-in fake and stubs.

    provider   config.INSURANCE_VERIFIER
    fake       (default outside prod) answers from the member id alone, deterministically, so
               every scenario can be exercised on any machine without a clearinghouse account —
               nothing leaves the machine. What registration, the office dashboard and the
               tests use in development.
    availity / change_healthcare / waystar / …   stubs: "not set up" until credentials and an
               adapter exist (the real ones speak X12 270/271 or the clearinghouse's JSON API).

What a check answers (``VerificationResult``), whoever answers it:

    status                 active · inactive · pending (coverage starts later) · not_found ·
                           incomplete (no member id / payer to ask about) · unavailable (the
                           service could not be reached or is not configured)
    coverage dates         coverage_start / coverage_end (effective dates)
    copay                  copay_cents for an office visit (+ specialist_copay_cents)
    deductible             deductible_cents, deductible_met_cents → deductible_status
                           (met · partially_met · not_met · none)
    coinsurance            coinsurance_percent — the patient's share after the deductible
    referral               referral_required (+ referral_notes)
    prior authorization    prior_auth_required (+ prior_auth_notes)

Amounts are integer cents in ``currency`` (USD); None = the payer did not say.

The built-in fake — how to get each scenario (case-insensitive substrings of the member id):

    INACTIVE / TERM        coverage ended last month              → inactive
    PENDING / FUTURE       coverage starts next month             → pending
    NOTFOUND / UNKNOWN     the payer has no such member           → not_found
    ERROR / DOWN           the service failed                     → unavailable
    HMO                    HMO plan: referral + prior auth required
    REFERRAL               referral required
    AUTH                   prior authorization required (imaging, surgery, specialist visits)
    MET                    deductible fully met
    HIGH                   high-deductible plan, nothing met yet
    anything else          active PPO; copay / deductible / coinsurance derived from a hash of
                           the member id (stable for the same id)

The dummy patient Pat Dummy (member id DUMMY-INS-0301) is therefore active with a partially
met deductible; try "DUMMY-HMO-REFERRAL" or "DUMMY-INACTIVE" on the registration page.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone

import config

log = logging.getLogger("account.insurance_verification")

STATUSES = ("active", "inactive", "pending", "not_found", "incomplete", "unavailable")
DEDUCTIBLE_STATUSES = ("met", "partially_met", "not_met", "none")


class VerificationError(Exception):
    """The verification could not be carried out (provider not configured / refused / down)."""


class NotConfigured(VerificationError):
    pass


@dataclass(frozen=True)
class VerificationRequest:
    """What the office knows — what it entered on the intake page, plus the patient's identity."""

    payer_name: str | None
    plan_name: str | None
    member_id: str | None
    group_number: str | None
    policyholder_name: str | None
    relationship: str | None
    first_name: str
    last_name: str
    date_of_birth: date
    service_date: date


@dataclass
class VerificationResult:
    status: str  # STATUSES
    message: str  # one line for a human, e.g. "Active PPO coverage · $25 copay"
    payer_name: str | None = None
    plan_name: str | None = None
    plan_type: str | None = None  # PPO | HMO | EPO | POS | … as the payer calls it
    member_id: str | None = None
    group_number: str | None = None
    coverage_start: date | None = None
    coverage_end: date | None = None
    currency: str = "USD"
    copay_cents: int | None = None
    specialist_copay_cents: int | None = None
    deductible_cents: int | None = None
    deductible_met_cents: int | None = None
    deductible_status: str | None = None  # DEDUCTIBLE_STATUSES
    coinsurance_percent: int | None = None
    out_of_pocket_max_cents: int | None = None
    out_of_pocket_met_cents: int | None = None
    referral_required: bool | None = None
    referral_notes: str | None = None
    prior_auth_required: bool | None = None
    prior_auth_notes: str | None = None
    provider: str = "fake"
    reference: str | None = None
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    raw: dict = field(default_factory=dict)  # the provider's answer as received (for support)

    @property
    def deductible_remaining_cents(self) -> int | None:
        if self.deductible_cents is None:
            return None
        return max(0, self.deductible_cents - (self.deductible_met_cents or 0))

    def as_dict(self) -> dict:
        out = asdict(self)
        out["deductible_remaining_cents"] = self.deductible_remaining_cents
        return out


def deductible_status(total: int | None, met: int | None) -> str | None:
    if total is None:
        return None
    if total == 0:
        return "none"
    met = met or 0
    if met >= total:
        return "met"
    return "partially_met" if met > 0 else "not_met"


def money(cents: int | None) -> str:
    """``2500`` → ``$25``, ``2550`` → ``$25.50`` (USD; the result carries ``currency`` for the client)."""
    if cents is None:
        return "—"
    return f"${cents / 100:,.0f}" if cents % 100 == 0 else f"${cents / 100:,.2f}"


def summarize(r: VerificationResult) -> str:
    """The one-line message the patient and the desk see."""
    if r.status == "active":
        parts = [f"Active {r.plan_type or ''} coverage".replace("  ", " ").strip()]
        if r.copay_cents is not None:
            parts.append(f"{money(r.copay_cents)} copay")
        if r.deductible_cents is not None:
            label = {"met": "deductible met", "none": "no deductible"}.get(r.deductible_status or "")
            parts.append(
                label or f"{money(r.deductible_remaining_cents)} of {money(r.deductible_cents)} deductible left"
            )
        if r.coinsurance_percent is not None:
            parts.append(f"{r.coinsurance_percent}% coinsurance")
        flags = []
        if r.referral_required:
            flags.append("referral required")
        if r.prior_auth_required:
            flags.append("prior authorization required")
        return " · ".join(parts + flags)
    if r.status == "inactive":
        when = f" (ended {r.coverage_end.isoformat()})" if r.coverage_end else ""
        return f"Coverage is not active{when}"
    if r.status == "pending":
        when = f" on {r.coverage_start.isoformat()}" if r.coverage_start else " later"
        return f"Coverage starts{when}"
    if r.status == "not_found":
        return "The insurer has no record of this member id"
    if r.status == "incomplete":
        return "Not enough details to check — an insurance company and a member id are needed"
    return r.message or "The eligibility service could not be reached"


# ---------------------------------------------------------------------------- providers


class Verifier:
    name = "base"

    def verify(self, request: VerificationRequest) -> VerificationResult:
        raise NotImplementedError


class FakeVerifier(Verifier):
    """Development/test stand-in: deterministic answers keyed on the member id (see the module
    docstring for the magic words). Stable for the same id, so the UI and the tests can rely on it."""

    name = "fake"

    def verify(self, request: VerificationRequest) -> VerificationResult:
        member = (request.member_id or "").strip()
        key = member.upper()
        today = request.service_date
        if any(w in key for w in ("ERROR", "DOWN")):
            raise VerificationError("The eligibility service returned an error (simulated by the built-in fake).")
        digest = hashlib.sha256(key.encode()).digest()
        reference = f"FAKE-ELIG-{digest[:4].hex().upper()}"
        base = dict(
            payer_name=request.payer_name or "Example Health (dummy)",
            plan_name=request.plan_name,
            member_id=member,
            group_number=request.group_number,
            provider=self.name,
            reference=reference,
            raw={"fake": True, "member_id": member, "service_date": today.isoformat()},
        )
        year_start = date(today.year, 1, 1)
        if any(w in key for w in ("NOTFOUND", "UNKNOWN")):
            r = VerificationResult(status="not_found", message="", **base)
        elif any(w in key for w in ("INACTIVE", "TERM")):
            ended = (today.replace(day=1) - timedelta(days=1)).replace(day=1) - timedelta(days=1)
            r = VerificationResult(
                status="inactive", message="", coverage_start=date(ended.year - 1, 1, 1), coverage_end=ended, **base
            )
        elif any(w in key for w in ("PENDING", "FUTURE")):
            starts = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
            r = VerificationResult(status="pending", message="", coverage_start=starts, **base)
        else:
            hmo = "HMO" in key
            plan_type = "HMO" if hmo else "PPO"
            copay = 1500 + (digest[4] % 8) * 500  # $15 … $50
            specialist = copay + 2000
            deductible = 50000 + (digest[5] % 6) * 50000  # $500 … $3,000
            if "HIGH" in key:
                deductible, met = 300000, 0
            elif "MET" in key:
                met = deductible
            else:
                met = round(deductible * (digest[6] % 90) / 100 / 100) * 100
            coinsurance = (1 + digest[7] % 3) * 10  # 10 / 20 / 30 %
            oop_max = deductible * 2 + 200000
            r = VerificationResult(
                status="active",
                message="",
                plan_type=plan_type,
                coverage_start=year_start,
                coverage_end=date(today.year, 12, 31),
                copay_cents=copay,
                specialist_copay_cents=specialist,
                deductible_cents=deductible,
                deductible_met_cents=met,
                deductible_status=deductible_status(deductible, met),
                coinsurance_percent=coinsurance,
                out_of_pocket_max_cents=oop_max,
                out_of_pocket_met_cents=min(oop_max, met),
                referral_required=hmo or "REFERRAL" in key,
                referral_notes=(
                    "A referral from the primary care provider is needed for specialist visits."
                    if hmo or "REFERRAL" in key
                    else "No referral needed for office visits."
                ),
                prior_auth_required=hmo or "AUTH" in key,
                prior_auth_notes=(
                    "Prior authorization required for imaging (MRI / CT), outpatient surgery and specialist visits."
                    if hmo or "AUTH" in key
                    else "Not required for office visits; required for inpatient admissions."
                ),
                **base,
            )
            if not r.plan_name:
                r.plan_name = f"{plan_type} Standard (dummy)"
        r.message = summarize(r)
        log.info("[fake eligibility] %s %s → %s (%s)", request.first_name, request.last_name, r.status, r.reference)
        return r


class StubVerifier(Verifier):
    """Placeholder for a real clearinghouse integration (Availity, Change Healthcare, Waystar …)."""

    def __init__(self, name: str):
        self.name = name

    def verify(self, request: VerificationRequest) -> VerificationResult:
        raise NotConfigured(
            f"The insurance eligibility provider '{self.name}' is not set up on this server (no credentials/adapter). "
            "Use INSURANCE_VERIFIER=fake outside production."
        )


def verifier() -> Verifier:
    name = (config.INSURANCE_VERIFIER or "").lower()
    if not name:
        return StubVerifier("none")
    if name == "fake":
        if config.IS_PROD:
            return StubVerifier("fake (not allowed in prod)")
        return FakeVerifier()
    return StubVerifier(name)


def verify(request: VerificationRequest) -> VerificationResult:
    """Run the configured verifier. Never raises: a failure becomes ``status = unavailable`` so
    registration and the desk always get an answer to store and show."""
    if not (request.member_id or "").strip() or not (request.payer_name or "").strip():
        r = VerificationResult(
            status="incomplete",
            message="",
            payer_name=request.payer_name,
            plan_name=request.plan_name,
            member_id=request.member_id,
            group_number=request.group_number,
            provider=verifier().name,
        )
        r.message = summarize(r)
        return r
    v = verifier()
    try:
        return v.verify(request)
    except VerificationError as exc:
        log.warning("insurance verification unavailable (%s): %s", v.name, exc)
        return VerificationResult(
            status="unavailable",
            message=str(exc),
            payer_name=request.payer_name,
            plan_name=request.plan_name,
            member_id=request.member_id,
            group_number=request.group_number,
            provider=v.name,
        )
    except Exception as exc:  # noqa: BLE001 — a provider bug must not break registration
        log.exception("insurance verification failed (%s)", v.name)
        return VerificationResult(
            status="unavailable",
            message=f"The eligibility service failed: {exc}",
            payer_name=request.payer_name,
            plan_name=request.plan_name,
            member_id=request.member_id,
            group_number=request.group_number,
            provider=v.name,
        )
