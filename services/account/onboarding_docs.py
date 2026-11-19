"""The documents and forms of the onboarding portal (portal.py) — DUMMY TEXTS.

Every text below is a placeholder written for this demo. Before real use the office replaces
them with its own policies and the official forms (the real I-9 and W-4 are government forms
with their own instructions; what is collected here is the subset a small office needs to
prepare them, and explicitly **not** government id numbers or full bank account numbers — those
are completed in person, see the notes in each form).

Each step of models.ONBOARDING_STEPS maps to one of:

    who = "hire"     the new hire completes it in the portal (form and/or signature)
    who = "office"   a manager ticks it off in the staff dashboard (background screening; the
                     reference *check* — the hire only supplies references)

``version`` is derived from the text, so a later edit produces a new version and the stored
signatures keep pointing at what was actually shown.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass(frozen=True)
class Field:
    name: str
    label: str
    type: str = "text"  # text | tel | email | date | select | checkbox | textarea | number
    required: bool = True
    options: tuple[tuple[str, str], ...] = ()
    hint: str = ""
    maxlength: int = 200


@dataclass(frozen=True)
class Step:
    key: str
    who: str  # hire | office
    title: str
    intro: str
    document: str = ""  # text the hire must read (documents)
    fields: tuple[Field, ...] = ()
    signature: bool = False  # requires an electronic signature
    office_note: str = ""  # what the office does after the submission

    @property
    def version(self) -> str:
        return hashlib.sha256(
            (self.title + self.document + "|".join(f.name for f in self.fields)).encode()
        ).hexdigest()[:8]


_DUMMY = "(DUMMY TEXT — replace with the office's own document before real use.)"

STEPS: dict[str, Step] = {
    "employment_application": Step(
        key="employment_application",
        who="hire",
        title="Employment application",
        intro="Please check the details we have from your job application and complete what is missing.",
        fields=(
            Field("address_line", "Street address"),
            Field("city", "City"),
            Field("postal_code", "Postal code", maxlength=20),
            Field("preferred_name", "Preferred name (how we should address you)", required=False, maxlength=100),
            Field(
                "work_authorization",
                "I am legally authorised to work in this country",
                type="select",
                options=(("yes", "Yes"), ("yes_with_sponsorship", "Yes, with sponsorship"), ("no", "No")),
            ),
            Field("confirm", "I confirm the information in my job application is complete and true", type="checkbox"),
        ),
    ),
    "offer_letter": Step(
        key="offer_letter",
        who="hire",
        title="Offer letter",
        intro="Read your offer and sign it electronically.",
        document=(
            f"OFFER OF EMPLOYMENT {_DUMMY}\n\n"
            "Dear {first_name} {last_name},\n\n"
            "We are pleased to offer you the position of {role}{specialty} at Medic Office, on a {employment_type} "
            "basis, starting on {start_date}. Your standard working hours are Monday to Friday, 9:00 AM to 5:00 PM, "
            "unless otherwise agreed with the practice manager.\n\n"
            "Compensation and benefits: as discussed with the practice manager and confirmed in writing separately. "
            "This offer is contingent on the satisfactory completion of the background screening, reference checks and "
            "the employment eligibility verification (I-9).\n\n"
            "By signing below you accept this offer.\n\nMedic Office — Practice Manager"
        ),
        signature=True,
    ),
    "background_screening": Step(
        key="background_screening",
        who="office",
        title="Background screening",
        intro="You consented to the screening in your application. The office records the result; nothing to do here.",
    ),
    "reference_check": Step(
        key="reference_check",
        who="hire",
        title="References",
        intro="Give us two professional references. The office contacts them and ticks this step off.",
        fields=(
            Field("ref1_name", "Reference 1 — name"),
            Field("ref1_relationship", "Reference 1 — relationship (e.g. former supervisor)"),
            Field("ref1_contact", "Reference 1 — phone or e-mail"),
            Field("ref2_name", "Reference 2 — name"),
            Field("ref2_relationship", "Reference 2 — relationship"),
            Field("ref2_contact", "Reference 2 — phone or e-mail"),
            Field("consent", "The office may contact these references", type="checkbox"),
        ),
        office_note="References provided online — contact them, then mark this step done.",
    ),
    "i9": Step(
        key="i9",
        who="hire",
        title="I-9 Employment Eligibility Verification — Section 1",
        intro=(
            "Section 1 is completed by you; the office completes Section 2 in person on or before your first day, "
            "when you present your original documents. Do NOT enter document or government id numbers here."
        ),
        document=(
            f"I-9 SECTION 1 ATTESTATION {_DUMMY}\n\n"
            "I attest, under penalty of perjury, that I am (check one): a citizen or national of this country; "
            "a lawful permanent resident; or an alien authorised to work until the date given. I am aware that "
            "federal law provides for imprisonment and/or fines for false statements in connection with this form.\n\n"
            "Original identity and employment-authorisation documents are presented in person (Section 2)."
        ),
        fields=(
            Field(
                "status",
                "Citizenship / immigration status",
                type="select",
                options=(
                    ("citizen", "Citizen or national"),
                    ("permanent_resident", "Lawful permanent resident"),
                    ("authorized_alien", "Authorised to work (with expiry)"),
                ),
            ),
            Field("work_until", "Authorised to work until (if applicable)", type="date", required=False),
            Field("other_names", "Other last names used (if any)", required=False, maxlength=100),
            Field(
                "documents_plan",
                "Documents I will bring for Section 2",
                type="select",
                options=(
                    ("list_a", "One List A document (e.g. passport)"),
                    ("list_b_c", "One List B + one List C document (e.g. driver's licence + social security card)"),
                ),
                hint="Bring the originals on your first day. Numbers are recorded in person only.",
            ),
        ),
        signature=True,
    ),
    "w4": Step(
        key="w4",
        who="hire",
        title="W-4 Employee's Withholding Certificate",
        intro=(
            "Tell payroll how to withhold. Your social security number is NOT collected online — payroll records it "
            "in person from your card."
        ),
        document=(
            f"W-4 DECLARATION {_DUMMY}\n\nUnder penalties of perjury, I declare that this certificate, "
            "to the best of my knowledge and belief, is true, correct, and complete."
        ),
        fields=(
            Field(
                "filing_status",
                "Filing status",
                type="select",
                options=(
                    ("single", "Single or married filing separately"),
                    ("married_jointly", "Married filing jointly / qualifying surviving spouse"),
                    ("head_of_household", "Head of household"),
                ),
            ),
            Field(
                "multiple_jobs",
                "I hold more than one job / my spouse also works (Step 2)",
                type="checkbox",
                required=False,
            ),
            Field(
                "dependents_amount", "Dependents amount (Step 3, whole currency units)", type="number", required=False
            ),
            Field("other_income", "Other income not from jobs (Step 4a)", type="number", required=False),
            Field("deductions", "Deductions (Step 4b)", type="number", required=False),
            Field("extra_withholding", "Extra withholding per pay period (Step 4c)", type="number", required=False),
            Field("exempt", "I claim exemption from withholding", type="checkbox", required=False),
        ),
        signature=True,
    ),
    "payroll": Step(
        key="payroll",
        who="hire",
        title="Payroll registration",
        intro=(
            "Direct deposit details. For your security only the bank name, account type and the LAST FOUR digits of "
            "the account are stored here — payroll verifies the full numbers from a voided cheque in person."
        ),
        fields=(
            Field("bank_name", "Bank name", maxlength=120),
            Field(
                "account_type",
                "Account type",
                type="select",
                options=(("checking", "Checking"), ("savings", "Savings")),
            ),
            Field("account_last4", "Account number — last 4 digits", maxlength=4, hint="Only the last four digits."),
            Field(
                "pay_stub_delivery",
                "Pay stubs",
                type="select",
                options=(("electronic", "Electronic"), ("paper", "Paper")),
            ),
            Field("authorize", "I authorise Medic Office to deposit my pay to this account", type="checkbox"),
        ),
    ),
    "handbook": Step(
        key="handbook",
        who="hire",
        title="Employee Handbook",
        intro="Read the handbook summary and acknowledge receipt.",
        document=(
            f"EMPLOYEE HANDBOOK — ACKNOWLEDGEMENT {_DUMMY}\n\n"
            "1. Standards of conduct: treat patients and colleagues with respect; no discrimination or harassment.\n"
            "2. Attendance: standard hours are Monday–Friday 9 AM–5 PM; report absences to the practice manager before "
            "your shift.\n"
            "3. Confidentiality: patient information is confidential at all times (see the HIPAA agreement).\n"
            "4. Safety: follow the OSHA safety procedures; report incidents immediately.\n"
            "5. Use of systems: office accounts are personal; never share passwords.\n\n"
            "I acknowledge that I have received and read the Employee Handbook and agree to follow it."
        ),
        signature=True,
    ),
    "hipaa": Step(
        key="hipaa",
        who="hire",
        title="HIPAA confidentiality agreement",
        intro="Read and sign the confidentiality agreement.",
        document=(
            f"HIPAA CONFIDENTIALITY AGREEMENT {_DUMMY}\n\n"
            "I understand that in the course of my work I will have access to protected health information (PHI). "
            "I agree to: use and disclose PHI only as required for my duties and as permitted by law; never discuss "
            "patient information where it can be overheard; protect records, screens and devices from unauthorised "
            "access; report any suspected privacy or security incident immediately; and continue to keep PHI "
            "confidential after my employment ends. I understand that violations may lead to disciplinary action up "
            "to termination and to civil or criminal penalties."
        ),
        signature=True,
    ),
    "osha": Step(
        key="osha",
        who="hire",
        title="OSHA safety acknowledgement",
        intro="Confirm you have reviewed the safety procedures.",
        document=(
            f"OSHA SAFETY ACKNOWLEDGEMENT {_DUMMY}\n\n"
            "I acknowledge that I have been informed about: the bloodborne pathogens exposure control plan and the "
            "use of personal protective equipment; the hazard communication program and where to find safety data "
            "sheets; sharps handling and disposal; emergency procedures, exits and the location of first-aid kits and "
            "eyewash stations; and how to report injuries and unsafe conditions. I will complete the annual safety "
            "training."
        ),
        fields=(
            Field("training_date", "Preferred date for the in-person safety training", type="date", required=False),
        ),
        signature=True,
    ),
    "emergency_contact": Step(
        key="emergency_contact",
        who="hire",
        title="Emergency contact",
        intro="Who should we call in an emergency?",
        fields=(
            Field("name", "Name", maxlength=120),
            Field("relationship", "Relationship", maxlength=60),
            Field("phone", "Phone", type="tel", maxlength=32),
            Field("alt_phone", "Alternative phone", type="tel", required=False, maxlength=32),
        ),
    ),
}


def step(key: str) -> Step | None:
    return STEPS.get(key)


def render_document(text: str, context: dict[str, str]) -> str:
    """Fill {first_name} … placeholders; missing values become an underscore line."""
    out = text
    for name, value in context.items():
        out = out.replace("{" + name + "}", value or "________")
    return out


def public(s: Step, context: dict[str, str]) -> dict:
    return {
        "key": s.key,
        "who": s.who,
        "title": s.title,
        "intro": s.intro,
        "document": render_document(s.document, context) if s.document else "",
        "version": s.version,
        "signature": s.signature,
        "fields": [
            {
                "name": f.name,
                "label": f.label,
                "type": f.type,
                "required": f.required,
                "options": [list(o) for o in f.options],
                "hint": f.hint,
                "maxlength": f.maxlength,
            }
            for f in s.fields
        ],
    }
