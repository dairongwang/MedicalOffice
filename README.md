# Healthcare management

A healthcare manegement platform with three front-ends and one API:

| Part | Folder | What it is for |
|------|--------|----------------|
| **Patients' app** (PWA) | `apps/web` (Angular 21) | registration with insurance, booking, check-in (questionnaire + signatures + estimate), **Payments** (payment methods and payment history on one tab), **My medicines** (a tab per family member: the medicines to take, dosing and instructions), **visit summaries**, **My reports** (ask for the immunization record / annual health examination report) |
| **Staff dashboard** | `apps/staff` (plain ES modules, no build) | job applications, onboarding, roster, availability; a provider's appointments and the patient's **chart** (visit note, diagnoses, prescriptions with instructions for the patient, referrals, immunizations, **visit summary**); **Reports** for doctors to complete and sign; medical assistants' tasks |
| **Office administration dashboard** | `apps/admin` (plain ES modules, no build) | patients (with **immunizations & reports**), the appointment agenda (check-in, assistants, **visit summaries**), the **Reports** queue, payments, the pharmacy directory |
| **API** | `services/account` (FastAPI · SQLAlchemy · Alembic) | everything above; SQLite for development, PostgreSQL for real deployments |

Everything runs on one machine without any external account: e-mail, text messages, payments (Stripe,
PayPal), "Sign in with Google / Facebook / Apple", e-signatures, insurance eligibility and the payment vault
all have **built-in fakes** that are used whenever no real provider is configured (never in production).
All data the project creates is **dummy data** (`dummy-…@example.com`, phone numbers from the 555-01xx range
reserved for fiction).

---

## Contents

1. [Quick start](#quick-start)
2. [Dummy accounts](#dummy-accounts)
3. [Visit summaries — signed by the doctor after every visit](#visit-summaries)
4. [Reports on request — immunization record and annual health examination, signed by a doctor](#reports-on-request)
5. [My medicines — a tab per patient, next to My reports](#my-medicines)
6. [Payments — payment methods and payment history on one tab](#payments)
7. [Built-in fakes and the test inbox](#built-in-fakes-and-the-test-inbox)
8. [Configuration](#configuration)
9. [Database](#database)
10. [Tests](#tests)
11. [Docker](#docker)
12. [Production notes](#production-notes)
13. [Project layout](#project-layout)
14. [What was added to make the app run](#what-was-added-to-make-the-app-run)
15. [Troubleshooting](#troubleshooting)

---

## Quick start

Requirements: **Python 3.10 – 3.14** and **Node.js 20.19+, 22.12+ or 24+** (Angular 21). PostgreSQL and
Docker are optional. Windows, macOS and Linux work the same way — `dev.py` uses the standard library only.

```bash
python dev.py setup      # venv + Python packages, npm packages, services/account/.env from .env.example,
                         # database migrated to the current schema
python dev.py run        # API + patients' app + staff dashboard + office dashboard (Ctrl+C stops all)
```

| URL | What |
|-----|------|
| http://localhost:4200/ | patients' app (Angular dev server, proxies `/api` to the API) |
| http://localhost:4300/ | staff dashboard |
| http://localhost:4400/ | office administration dashboard |
| http://localhost:8000/api/docs | the API (OpenAPI / Swagger) |
| http://localhost:8000/api/v1/messages/fake/outbox | the **test inbox**: every e-mail / text the fakes "sent" |

`python dev.py run` uses SQLite (`services/account/data/account.db`) unless a local PostgreSQL answers
(`python dev.py db init && python dev.py db start`). At every start the API creates / refreshes the dummy
data (`SEED_FAKE_ACCOUNTS=true`).

Other commands (`python dev.py help` lists them all):

```bash
python dev.py seed            # (re)create the dummy data now (resets the dummy passwords)
python dev.py outbox          # print what the fake e-mail / SMS outbox received
python dev.py stop            # kill leftover dev processes on the ports above
python dev.py serve           # production build of the PWA served by the API at http://localhost:8000/
python dev.py test            # Python + Angular + dashboard unit tests
python dev.py e2e             # end-to-end tests in a real browser
python dev.py lint            # ruff + prettier + tsc
python dev.py docker          # everything in containers (PostgreSQL included)
```

## Dummy accounts

Password pattern: **`Dummy-<Kind>-pass-123`** (e.g. `Dummy-Doctor-pass-123`). All fake.

| E-mail | Password | Use it for |
|--------|----------|-----------|
| `dummy-patient@example.com` | `Dummy-Patient-pass-123` | **patients' app** — Pat Dummy: a past visit with a signed visit summary, an annual physical, two upcoming visits (one checked in), immunizations, a signed immunization record and an open annual-exam request, a card and a PayPal wallet on file — and her daughter **Sky Dummy** on the same account (asthma check with two prescriptions), so *My medicines* shows a tab for each |
| `dummy-doctor@example.com` | `Dummy-Doctor-pass-123` | **staff dashboard** — Dr. Alex Example (W-000001): My appointments, the chart, **signs visit summaries and reports** |
| `dummy-nurse@example.com` | `Dummy-Nurse-pass-123` | staff dashboard — Sam Sample, nurse (prepares but cannot sign summaries) |
| `dummy-assistant@example.com` | `Dummy-Assistant-pass-123` | staff dashboard — Jamie Helper, medical assistant (My tasks) |
| `dummy-assistant2@example.com` | `Dummy-Assistant2-pass-123` | staff dashboard — Taylor Aide, medical assistant (afternoons, Mon–Thu) |
| `dummy-admin@example.com` | `Dummy-Admin-pass-123` | **office dashboard**, the staff dashboard's manager tabs, API docs in prod |
| `dummy-free@` · `dummy-trial@` · `dummy-pro@` · `dummy-lifetime@` · `dummy-pastdue@` · `dummy-expired@` (example.com) | `Dummy-Free-pass-123`, … | every membership / billing state |
| `dummy-twofa@example.com` | `Dummy-Twofa-pass-123` | two-factor sign-in — authenticator secret `DUMMYDUMMYDUMMYDUMMYDUMMYDUMMYDU`, recovery codes `dummy-00001` … `dummy-00010` |

Also seeded: the front-desk record **W-000003 / dummy-frontdesk@example.com** (Robin Placeholder) *without*
an account — try "Create staff account" on the staff dashboard (the code arrives in the test inbox); three
job applications; three pharmacies; four patients (Pat, Casey — self-pay, Riley — HMO plan, Sky — Pat's daughter); four
sponsored-content campaigns; immunization histories for Pat and Riley. The patient's card is the public test number **4242 4242 4242 4242**, tokenized
by the fake vault (only brand and last four digits are stored).

## Visit summaries

> After a visit the patient gets a **visit summary, signed by the doctor**.

### What it contains

Generated from the chart, in plain words: the visit (date, provider, office), why the patient came (check-in
answer or booking reason), the vital signs the medical assistant took, the diagnoses (ICD-10), the
**assessment** and **plan**, the medications prescribed at the visit (and the pharmacy they were sent to),
the referrals written, the tests ordered (with "values outside the reference range" where that applies),
the current medication and allergy lists — plus the doctor's **instructions** and **follow-up**. The PDF adds
the doctor's drawn signature, the time, the e-signature reference and a verification code
(`VS-000001-3F9A…`).

### How it flows

1. **Prepared as a draft — automatically once the visit is over**
   * when the office marks the visit **Completed** (office dashboard → Appointments), or
   * when the background job (every `REMINDER_CHECK_SECONDS`, 60 s) finds a visit that ended within the last
     7 days which the provider started ("Start visit") and which has no summary yet, or
   * when the doctor opens **Visit summary** on the chart (once the visit has started).

   When prepared automatically, the doctor is told by e-mail / text that it waits for their signature (a link
   to the chart, no medical details).
2. **Reviewed on the chart** (staff dashboard → My appointments → *Open chart* → **Visit summary**). Until it is
   signed the draft follows the chart (diagnoses, prescriptions, referrals, the visit note). The assessment and
   plan are the visit note's unless the doctor rewrites them for the patient; instructions and follow-up are
   typed there. *Open PDF* shows the draft (marked DRAFT).
3. **Signed by the doctor** — drawn signature + typed full name + consent to sign electronically (esign.py;
   the built-in fake outside production). Only a **doctor or physician assistant** can sign (a nurse's visit
   can be prepared but not signed). Signing freezes it.
4. **Handed to the patient** — e-mailed with the PDF attached and a text message that it is ready (no
   medical details in the text); in the patients' app under **My appointments → Past → Visit summary** (with
   *Download PDF*, a "new summary" tag until it is opened). The office sees *summary signed / summary to
   sign* on the agenda and can **Print summary** or **Send summary** again.
5. **Amendments** — a signed summary is never edited. *Amend…* (with a reason) creates version 2 from it; once
   signed it supersedes version 1 (both stay on record; the patient sees the latest).

### Try it (2 minutes)

1. `python dev.py run`.
2. Patients' app http://localhost:4200/patients/mine → *Log in* as `dummy-patient@example.com` /
   `Dummy-Patient-pass-123` → **Past** → *Visit summary* on the August visit — the seeded summary Dr. Alex
   Example signed. *Download PDF*.
3. Office dashboard http://localhost:4400/ as `dummy-admin@example.com` → Appointments → *Next 7 days*
   → on Pat's checked-in visit click **Completed** → the row says *summary to sign* and the doctor got a message
   (test inbox).
4. Staff dashboard http://localhost:4300/ as `dummy-doctor@example.com` → My appointments → *Open chart* →
   **Visit summary**: add instructions, draw a signature, tick the consent → **Sign visit summary**.
5. The test inbox shows the e-mail with the PDF; the patient's app shows the new summary.

### API

| Who | Route |
|-----|-------|
| the visit's provider | `GET/PUT /api/v1/staff/me/appointments/{id}/visit-summary` · `POST …/sign` · `POST …/amend` · `POST …/send` · `GET …/pdf[?version=n]` |
| the patient (signed in) | `GET /api/v1/me/appointments/{id}/visit-summary` · `GET …/pdf` (signed versions only) |
| the office (administrators) | `GET /api/v1/appointments/{id}/visit-summary` · `GET …/pdf` · `POST …/send` · `POST /api/v1/appointments/visit-summaries/run[?as_of=]` |

Every appointment in the API carries a `visit_summary` brief (patients see signed ones only). Code:
`services/account/visit_summaries.py`, table `visit_summaries` (migration `0037_visit_summaries`), PNG images in
PDFs: `services/account/pdf.py`; UI: `apps/staff/js/views/chart-summary.js`,
`apps/web/src/app/features/patients/visit-summary.ts`, `apps/admin/js/views/appointments.js`.

## Reports on request

> Upon request, the office generates the **immunization report** and the **annual health examination
> report** of a patient, **signed by a doctor**.

### The two reports

| Report | What it contains | The doctor's part |
|--------|------------------|-------------------|
| **Immunization record** | every vaccine on record (vaccine, date, dose *n of m*, product and lot, site and route, who gave it — a member of staff or another clinic), the allergies | contraindications / exemptions, the vaccines due or recommended, comments — and the certification that the list is what the record holds |
| **Annual health examination report** | the exam visit it is based on (date, provider), the vital signs taken there, the medical, family and social history, medications, allergies, immunizations (latest dose of each), results of the last 12 months | height, weight, blood pressure, vision, hearing; the examination by system (normal / abnormal + what / not examined); the **decision for the stated purpose** — fit · fit with restrictions · not fit · further evaluation needed — restrictions, recommendations, the next exam; the certification that they examined the patient |

Each report is for a stated purpose (school, work, sports, travel, camp, personal, …, and e.g. the school's
name) and can carry a "needed by" date.

### How it flows

1. **Requested** — by the patient in the app (*My reports → Ask for a report*), or recorded by the office for a
   request made at the desk / on the phone (patient page → *Immunizations & reports → New request*). A patient's
   request tells the office (`OFFICE_NOTIFY_EMAIL` / `OFFICE_NOTIFY_PHONE`) and lands in the **Reports** queue.
   One report of each kind can be open per patient.
2. **Prepared by the office** — *Prepare…* builds it from the record: the office chooses the **doctor** who
   signs and, for the annual exam, the **exam visit** it is based on (suggested: the latest annual / physical
   exam). Warnings tell what is missing ("no vital signs at the exam visit", "the exam was more than a year ago",
   "no immunizations on record"). The doctor is told by e-mail / text (no medical details). Until it is signed
   the report follows the record (a vaccine entered later shows up).
3. **Completed and signed by the doctor** — staff dashboard → **Reports**: the record part, their form, a
   checklist of what is still missing, *Open PDF (draft)*, then drawn signature + typed name + consent →
   **Sign report**. Only a **doctor** signs (a nurse never sees these). A doctor can also **return** it to the
   office with the reason (e.g. "no exam within the last year — please book one"); the office prepares it again
   or cancels it (the patient is told why).
4. **Handed to the patient** — e-mailed with the PDF attached and a text that it is ready; in the app (*My
   reports → View / PDF*). The office can *Print* it or *Send to patient* again.
5. **Amended** — a signed report is never edited: *Amend…* creates version 2 (the doctor's part copied); once
   signed it supersedes version 1.

**Immunizations** are part of the record: the office enters doses on the patient page (*Immunizations &
reports*: given here — with the member of staff — or reported by another clinic; vaccine, CVX code, dose of
series, manufacturer, lot, site, route), marks a wrong entry *entered in error* (kept, but off every report);
providers see them on the chart.

### Try it

1. `python dev.py run`; office dashboard http://localhost:4400/ as `dummy-admin@example.com` → **Reports**: Pat's
   request for an annual health examination report (for the running club) → *Prepare…* → the annual physical
   is suggested → *Prepare & send to the doctor*.
2. Staff dashboard http://localhost:4300/ as `dummy-doctor@example.com` → **Reports** → open it → mark a few
   systems *Normal*, choose *Fit…*, draw the signature, tick the consent → **Sign report**.
3. Patients' app http://localhost:4200/patients/reports as `dummy-patient@example.com` → *View* / *PDF* (the
   seeded immunization record is there too) — and *Ask for a report* for another one.

### API

| Who | Route |
|-----|-------|
| the office | `GET /api/v1/patient-reports?status=open\|requested\|draft\|declined\|signed\|canceled\|all&kind=&q=` · `GET …/{id}` · `POST …/{id}/generate {worker_id, appointment_id}` · `POST …/{id}/cancel {reason}` · `POST …/{id}/send` · `GET …/{id}/pdf` · `GET/POST /api/v1/patients/{id}/reports` · `GET /api/v1/patients/{id}/reports/options` · `GET/POST /api/v1/patients/{id}/immunizations` · `PATCH …/immunizations/{iid}` |
| the doctor | `GET /api/v1/staff/me/reports?status=todo\|signed\|all` · `GET/PUT …/{id} {findings, doctor_note}` · `POST …/{id}/sign \| decline \| amend \| send` · `GET …/{id}/pdf` |
| the patient | `GET/POST /api/v1/me/reports` · `GET …/{id}` · `GET …/{id}/pdf` · `POST …/{id}/cancel` |

Code: `services/account/patient_reports.py`, `services/account/immunizations.py`, tables `patient_reports` and
`patient_immunizations` (migration `0038_patient_reports`); UI: `apps/admin/js/views/reports.js`,
`apps/admin/js/views/immunizations.js`, `apps/staff/js/views/reports.js`,
`apps/web/src/app/features/patients/reports-page.ts`.

## My medicines

> **My medicines** is a top-level page of the patients' app, next to *My reports*, with a **tab for each
> patient** on the account (yourself, a child, …): the **prescription medicines to take, the dosing and the
> instructions**.

Patients' app → **Patients** → **My medicines** (http://localhost:4200/patients/medicines — the sub-navigation
reads *Registration · Appointments · My appointments · **My medicines** · My reports · Payments*). Signed out, the page offers *Log in* / *Register*. Each tab (one per patient record linked to the
account, with the number of medicines to take) shows:

| Part | What |
|------|------|
| **Allergies** | the active allergy list, at the top |
| **Take now** | every current medicine: name, strength and form (*Lisinopril 10 mg tablet*); **How to take it** — the prescription's directions (*Take 1 tablet by mouth once daily*); **Your doctor's instructions** — plain-language advice the prescriber wrote for the patient (*Take it at the same time every day… Stand up slowly if you feel dizzy*); **For** — what it treats (the prescription's diagnosis); who prescribed it and when, the RX number, the quantity and refills, the pharmacy it was sent to. A medicine on the list that was not prescribed here (another doctor's, over the counter) is marked *from your list* and shows what the list says |
| **No longer taking** | (folded) stopped medicines and why |
| **Prescriptions from the office** | (folded) every prescription line issued, newest first |
| **Your pharmacy** | the pharmacy the provider recorded, and a reminder not to change a medicine without the doctor |

The tabs follow the WAI-ARIA tabs pattern (←/→, Home/End). The page is read-only — the office keeps the list.

Where the data comes from: the chart's **medication list** (kept current by the office, the medical assistant's
review of every visit and every prescription) says *what* the patient takes; the latest **prescription** of that
medicine says *how*. Prescribers add the patient's instructions per medication on the chart (staff dashboard →
chart → *New prescription* → "Instructions for the patient (optional)"); they are also printed on the
prescription PDF and shown on the office dashboard. A **voided** prescription (a mistake) is left out everywhere,
together with the list entry it added.

### Try it

1. `python dev.py run`; patients' app http://localhost:4200/patients/medicines → *Log in* as
   `dummy-patient@example.com` / `Dummy-Patient-pass-123`: *Pat* (lisinopril with instructions, atorvastatin from her list, penicillin allergy) and
   *Sky* (albuterol inhaler and cetirizine with instructions, amoxicillin stopped — hives).
2. Staff dashboard http://localhost:4300/ as `dummy-doctor@example.com` → open a chart → *New prescription*, fill
   in "Instructions for the patient", sign & issue → reload the patients' app: the medicine is on that tab.

### API

| Who | Route |
|-----|-------|
| the patient | `GET /api/v1/me/medications` → `{records: [{patient_id, patient_name, allergies, current, stopped, history, pharmacy_name, …}], office_name, office_phone}` |
| the prescriber | `POST /api/v1/staff/me/appointments/{id}/prescriptions` — each item takes an optional `patient_instructions` (≤ 1000 characters) |

Code: `services/account/patient_medications.py`, column `prescription_items.patient_instructions` (migration
`0039_rx_patient_instructions`); UI: `apps/web/src/app/features/patients/medicines-page.ts` (the page, route
`patients/medicines` in `app.routes.ts`, link in `patients-page.ts`), `medicines-panel.ts` (the tabs),
`apps/web/src/app/core/medications/medications.service.ts`, `apps/staff/js/views/chart-orders.js`.

## Payments

The patients' app has **one Payments tab** (http://localhost:4200/patients/payments) instead of the former
*Payment methods* and *Payment history* tabs:

1. **Payment methods** — the cards, Stripe / PayPal wallets and bank accounts on the account: add (validated by a
   small authorization that is released at once), check again, make default, edit, remove. Formerly also shown
   at the bottom of *My appointments* — that page now only links here.
2. **Payment history** — totals (charged · on hold · refunded), every hold, charge, refund and validation,
   with filters.

The old address http://localhost:4200/patients/history leads to `/patients/payments#payment-history` (the page
scrolls to the history); links from the scheduler and the check-in page go there too. Signed out, the page
offers *Log in* / *Register*. Try it as `dummy-patient@example.com` / `Dummy-Patient-pass-123`: the seeded card
(the public test number 4242 4242 4242 4242, fake vault) and PayPal wallet, and the holds of Pat's upcoming
visits.

Code: `apps/web/src/app/features/patients/payments-page.ts` (the page), `payment-methods.ts`
(`PaymentMethodsPanel`), `payment-history.ts` (`PaymentHistoryPanel`), routes in `app.routes.ts`; API unchanged
(`/api/v1/me/payment-methods`, `/api/v1/me/payments`).

## Built-in fakes and the test inbox

Used automatically outside production whenever the real provider is not configured:

| Fake | Where | What it does |
|------|-------|--------------|
| E-mail / SMS | `messaging.py` | messages land in `DATA_DIR/message_outbox.json`, readable at `/api/v1/messages/fake/outbox` (attachments included) and with `python dev.py outbox` |
| Stripe / PayPal | `fakes.py` | hosted checkout / approval / billing-portal pages served by the API, the real REST calls and **signed webhooks** delivered in-process — *Pay*, *Decline*, *Simulate renewal / failed renewal*, refunds on cancel; `/api/v1/billing/fake/events` lists the webhooks |
| Sign in with Google / Facebook / Apple | `oauth.py` | a consent screen where any e-mail can be typed |
| E-signatures | `esign.py` | records the signature locally, reference `FAKE-ESIGN-…` |
| Insurance eligibility | `insurance_verification.py` | deterministic answers from the member id (`…INACTIVE…`, `…HMO…`, `…NOTFOUND…` …) |
| Payment vault | `patient_payment_methods.py` | tokenizes cards / wallets / bank accounts; declines cards ending in 0002 |
| Ad providers | `ad_sources.py` | "FAKE" AdSense / Microsoft / network tags |

## Configuration

`services/account/.env` (created from [`services/account/.env.example`](services/account/.env.example) by
`python dev.py setup`; real environment variables win). Every value in the example is a **dummy**. The most
important settings (`services/account/config.py` documents all of them):

| Variable | Default | Meaning |
|----------|---------|---------|
| `APP_ENV` | `dev` | `dev` · `test` · `qa` · `staging` · `prod` — prod switches every fake and the dummy data off |
| `TOKEN_SECRET` | random per start (dev) | signs sessions; **required** in staging / prod |
| `DATABASE_URL` | SQLite in `services/account/data/` | e.g. `postgresql+psycopg://user:password@host:5432/assistant` |
| `SEED_FAKE_ACCOUNTS` | `false` (`dev.py run`: `true`) | create the dummy data at start (never in prod) |
| `OFFICE_NAME` / `OFFICE_ADDRESS` / `OFFICE_PHONE` / `OFFICE_TIMEZONE` | `Medic Office` / – / – / `UTC` | shown in messages, documents (visit summaries, prescriptions, referral letters) and the agenda |
| `REMINDER_CHECK_SECONDS` | `60` (`0` in tests) | the background job: reminders, expired check-ins, **visit-summary drafts** |
| `SMTP_*`, `TWILIO_*` | empty | real e-mail / SMS instead of the test inbox |
| `STRIPE_*`, `PAYPAL_*` | empty | real payments instead of the fakes |
| `ESIGN_PROVIDER` | `fake` outside prod | e-signature provider (check-in, onboarding, visit summaries) |
| `CORS_ORIGINS`, `STAFF_URL`, `ADMIN_DASHBOARD_URL`, `WEB_APP_URL` | set by `dev.py run` | where the separately deployed front-ends live |

The dashboards read their API origin from `config.js` (written by `serve.py` / the container entrypoint) or
from `?api=https://…` in the URL.

## Database

Alembic owns the schema for SQLite and PostgreSQL (`services/account/alembic/versions`, currently
`0039_rx_patient_instructions`). SQLite databases migrate themselves at start; PostgreSQL with `AUTO_MIGRATE=true`,
`python dev.py db upgrade`, or the container's `alembic upgrade head`.

```bash
python dev.py db init        # a local PostgreSQL cluster in .pgdata/ (picks a free port if 5432 is taken)
python dev.py db start|stop|status
python dev.py db upgrade     # migrate whatever DATABASE_URL / the local cluster points at
python dev.py db revision "describe the change"
```

## Tests

```bash
python dev.py test              # pytest (services/account/tests) + Angular (vitest) + node --test (dashboards)
python dev.py test --postgres   # the API tests against PostgreSQL (TEST_DATABASE_URL, a database ending in _test)
python dev.py e2e               # browser tests (e2e/tests): staff dashboard, office dashboard, patients' app,
                                # visit summaries, reports and My medicines across all three
```

The end-to-end session starts an isolated stack on free ports (API with a throw-away database and the dummy
data, both dashboards, the production build of the patients' app — built when missing or outdated). Browser:
Playwright's Chromium (`python -m playwright install chromium`), or `E2E_BROWSER_PATH=<chrome binary>`, or
`cd e2e && npm install` (a Chromium inside an npm package). `E2E_HEADED=1` shows the window.

## Docker

```bash
python dev.py docker            # = docker compose up --build
```

[`docker-compose.yml`](docker-compose.yml) runs PostgreSQL, the API (which also serves the patients' app on
http://localhost:8000/), the staff dashboard (http://localhost:4300/) and the office dashboard
(http://localhost:4400/) with `APP_ENV=dev`, the dummy data and the fakes. Its passwords and secrets are
dummies for a local try-out. Images can also be built one by one:
`docker build -f services/account/Dockerfile -t medic-office-api .`, `docker build -t medic-office-staff apps/staff`,
`docker build -t medic-office-admin apps/admin`.

## Production notes

* `APP_ENV=prod` turns every fake, the test inbox and the dummy data off; the API refuses to start without
  `TOKEN_SECRET`; `/api/docs` needs an administrator account. The first administrator: `ADMIN_EMAILS`, or
  `python dev.py promote <email>`.
* Configure real providers: SMTP and/or Twilio (codes, appointment links, **visit summaries to patients**),
  Stripe / PayPal, OAuth, an insurance clearinghouse, a payment vault and an **e-signature provider**
  (`ESIGN_PROVIDER` — until an adapter exists, signing check-ins, onboarding documents, visit summaries and
  reports answers 503 in production).
* `infra/azure/main.bicep` describes an Azure deployment (Container Apps + PostgreSQL + Static Web App).
* Documents printed outside production carry a "SAMPLE — dummy data" banner.

## Project layout

```
dev.py                       developer commands (setup, run, test, e2e, db, docker, …)
docker-compose.yml           local containers: PostgreSQL + API + dashboards
services/account/            the API — main.py (routes), config.py, models.py, schemas.py, alembic/
  visit_summaries.py         after-visit summaries signed by the doctor
  patient_reports.py         reports on request (immunization record, annual exam) signed by a doctor
  immunizations.py           the vaccines in the patient's record
  patient_medications.py     My medicines: the medicines to take, per patient record (patients' app)
  fakes.py                   built-in Stripe / PayPal
  seed.py                    the dummy data
  tests/                     pytest
apps/web/                    patients' app (Angular PWA)
apps/staff/                  staff dashboard (static; serve.py for development)
apps/admin/                  office administration dashboard (static; serve.py for development)
e2e/                         browser tests (pytest + Playwright)
infra/azure/                 Bicep template
```

## What was added to make the app run

The uploaded project was missing files that other code imports or loads; they were recreated from how the
rest of the code uses them (and from the dummy data found in the bundled development database):

| File | Why it was needed |
|------|-------------------|
| `services/account/fakes.py` | imported by `main.py` and `payments.py` — the API did not start |
| `services/account/seed.py` | the dummy data: API start-up (`SEED_FAKE_ACCOUNTS`), `dev.py seed / promote`, the tests |
| `services/account/.env.example` | copied by `dev.py setup` |
| `apps/web/src/app/core/ads/ads.service.ts` | imported by the app shell — the Angular build failed |
| `apps/staff/js/views/login.js`, `create-account.js`, `apply.js`, `availability.js` | imported by `apps/staff/js/app.js` — the staff dashboard did not load |
| `apps/admin/js/views/pharmacies.js` | imported by `apps/admin/js/app.js` — the office dashboard did not load |
| `docker-compose.yml` | used by `dev.py docker` |
| `e2e/conftest.py`, `e2e/tests/…` | used by `dev.py e2e` |
| `services/account/tests/test_*.py`, `apps/staff/tests/validation.test.mjs`, `apps/web/…/visit-summary.spec.ts` | tests for the above and the visit summaries |

New for the visit summaries: `services/account/visit_summaries.py`, migration `0037_visit_summaries`, the
`VisitSummary` model and schemas, PNG images in `pdf.py`, the chart section `apps/staff/js/views/chart-summary.js`,
the patient view `apps/web/src/app/features/patients/visit-summary.ts`, and additions to the agenda
(`apps/admin/js/views/appointments.js`), My appointments (staff and patients) and the background job.

New for the reports on request: `services/account/patient_reports.py`, `services/account/immunizations.py`,
migration `0038_patient_reports`, the `PatientReport` / `PatientImmunization` models and schemas, the office's
Reports tab and patient sub-page (`apps/admin/js/views/reports.js`, `immunizations.js`), the doctors' Reports tab
(`apps/staff/js/views/reports.js`) and the immunizations on the chart, the patients' *My reports*
(`apps/web/src/app/features/patients/reports-page.ts`, `core/reports/reports.service.ts`), the seed data, and
tests (`tests/test_patient_reports.py`, `reports-page.spec.ts`, `apps/admin/tests/reports.test.mjs`,
`e2e/tests/test_reports.py`).

New for My medicines: `services/account/patient_medications.py` (`GET /api/v1/me/medications`), migration
`0039_rx_patient_instructions` (instructions for the patient on each prescribed medication — the chart's
prescription form, the PDF, the office dashboard), the *My medicines* page at `/patients/medicines`
(`apps/web/src/app/features/patients/medicines-page.ts`, `medicines-panel.ts`, `core/medications/medications.service.ts`), the seed
data (`seed_family()`: Sky Dummy with her prescriptions; instructions on Pat's lisinopril), and tests
(`tests/test_patient_medications.py`, `medicines-panel.spec.ts`, `e2e/tests/test_medicines.py`).

Payments as one tab: `apps/web/src/app/features/patients/payments-page.ts` (payment methods + payment history),
the history turned into a panel (`payment-history.ts`), `/patients/history` redirecting to it, the payment
methods removed from *My appointments*; tests in `payment-history.spec.ts` and `e2e/tests/test_patient_app.py`.

## Troubleshooting

* **Port already in use** — `python dev.py stop`, or set `PORT` / `WEB_PORT` / `STAFF_PORT` / `ADMIN_PORT`.
* **Everybody is signed out after a restart** — set `TOKEN_SECRET` in `services/account/.env` (the example has
  a dummy one).
* **"Node.js … is too old"** — install the current LTS and run `python dev.py setup` again.
* **No PostgreSQL** — nothing to do: `dev.py run` falls back to SQLite and says so.
* **Start from a clean database** — stop the servers and delete `services/account/data/` (SQLite); the next
  start migrates and seeds a fresh one. An existing development database is upgraded in place and topped
  up with the new dummy data (including the signed visit summary and Sky Dummy on the patient account).
* **"The visit summary is prepared once the visit has started"** — the visit is still in the future: open the
  chart of a visit that has started (or click *Start visit*), or have the office mark one *Completed*.
* **"An … is already in progress"** — one report of each kind can be open per patient: finish, or cancel / withdraw
  the open one first.
* **The doctor does not see a report** — the office has to *Prepare* it first (requested reports are the office's),
  and it appears only for the doctor it was assigned to.
* **Where did the e-mail go?** — the test inbox: http://localhost:8000/api/v1/messages/fake/outbox, or
  `python dev.py outbox`.
