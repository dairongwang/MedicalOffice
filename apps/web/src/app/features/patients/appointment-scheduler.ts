import { Component, OnInit, computed, effect, inject, signal, untracked } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { ApiError } from '../../core/account/api.service';
import { AccountService } from '../../core/account/account.service';
import {
  type Appointment,
  type AppointmentLookup,
  AppointmentsService,
  type CoverageCheck,
  type DaySlots,
  type Provider,
  type ProviderList,
  type Slot,
  describeDelivery,
  isoDate,
  providerLabel,
  weekdayOf,
  weekdaysLabel,
} from '../../core/appointments/appointments.service';
import type { PatientIdentity } from '../../core/patients/patients.service';
import {
  type PaymentMethod,
  PaymentMethodsService,
  money,
} from '../../core/payments/payment-methods.service';
import { ShellService } from '../../core/ui/shell.service';
import { TipDirective } from '../../core/ui/tip.directive';
import { ToastService } from '../../core/ui/toast.service';
import { CheckInControl } from './check-in';
import { InsuranceVerificationView } from './insurance-verification-view';

type Step = 'identity' | 'time' | 'done';

interface IdentityDraft {
  first_name: string;
  last_name: string;
  date_of_birth: string;
  email: string;
  phone: string;
}

const orNull = (v: string): string | null => (v.trim() ? v.trim() : null);
/** How many days ahead the page looks for the first day with a free slot. */
const LOOKAHEAD_DAYS = 14;

/**
 * Appointments (/patients/appointments) — no account needed.
 *
 *   1. Who you are    — the same identity as on the registration page (name + date of birth,
 *                       optional e-mail / phone) → POST /appointments/lookup: the record and
 *                       the upcoming appointments, each with "open" (the link's page) and cancel.
 *   2. Pick a time    — provider, day, one of the free slots the server cut from the provider's
 *                       availability (the labels are in the office's time zone), a reason.
 *                       Pressing Book first asks POST /appointments/coverage whether the
 *                       insurance on the record is active for that day; if not, the patient
 *                       must tick "I agree to pay for this visit myself" — otherwise no
 *                       appointment is made (the server refuses with 409 too).
 *                       → POST /appointments. 409 (slot just taken) reloads the day and keeps
 *                       the message; after hours the page starts on the next day with a free slot.
 *   3. Done           — the confirmation number, where the link went (or the test inbox when the
 *                       built-in fake delivered it), and the link itself.
 *
 * The identity typed on the registration page ("Book an appointment →") or picked on the
 * account's page ("book for this record") arrives through `AppointmentsService.prefill`.
 */
@Component({
  selector: 'app-appointment-scheduler',
  imports: [FormsModule, RouterLink, TipDirective, InsuranceVerificationView, CheckInControl],
  template: `
    <section class="page intake scheduler" aria-labelledby="sched-title">
      <header class="page-header">
        <div>
          <h2 id="sched-title">Appointments</h2>
          <p class="muted small">
            Book a visit with a doctor or nurse of this office, or see and cancel the appointments
            you already have. You need to be registered first.
          </p>
        </div>
        <ol class="steps" aria-label="Progress">
          <li [class.current]="step() === 'identity'" [class.done]="step() !== 'identity'">
            <span class="step-no">1</span> Who you are
          </li>
          <li [class.current]="step() === 'time'" [class.done]="step() === 'done'">
            <span class="step-no">2</span> Pick a time
          </li>
          <li [class.current]="step() === 'done'"><span class="step-no">3</span> Done</li>
        </ol>
      </header>

      @if (step() === 'identity') {
        <form class="card intake-card" id="sched-identity" (ngSubmit)="lookup()" novalidate>
          <h3>Who you are</h3>
          <p class="small muted">
            Enter the details you registered with. Not registered yet?
            <a routerLink="/patients" id="sched-register-link">Register first</a>.
            @if (account.signedIn()) {
              Signed in? <a routerLink="/patients/mine">Your account page</a> lists every
              appointment on your records.
            }
          </p>
          <div class="field-row">
            <div class="field">
              <label for="sched-first-name">First name</label>
              <input
                class="input"
                id="sched-first-name"
                name="first_name"
                autocomplete="given-name"
                required
                maxlength="100"
                [(ngModel)]="draft.first_name"
              />
            </div>
            <div class="field">
              <label for="sched-last-name">Last name</label>
              <input
                class="input"
                id="sched-last-name"
                name="last_name"
                autocomplete="family-name"
                required
                maxlength="100"
                [(ngModel)]="draft.last_name"
              />
            </div>
          </div>
          <div class="field-row">
            <div class="field">
              <label for="sched-dob">Date of birth</label>
              <input
                class="input"
                id="sched-dob"
                name="date_of_birth"
                type="date"
                autocomplete="bday"
                required
                [max]="today"
                [(ngModel)]="draft.date_of_birth"
              />
            </div>
            <div class="field">
              <label for="sched-phone">Mobile phone <span class="muted">(optional)</span></label>
              <input
                class="input"
                id="sched-phone"
                name="phone"
                type="tel"
                autocomplete="tel"
                maxlength="32"
                [(ngModel)]="draft.phone"
              />
            </div>
          </div>
          <div class="field">
            <label for="sched-email">E-mail <span class="muted">(optional)</span></label>
            <input
              class="input"
              id="sched-email"
              name="email"
              type="email"
              autocomplete="email"
              maxlength="320"
              [(ngModel)]="draft.email"
            />
            <p class="hint small muted">
              The confirmation and a reminder the day before go to the e-mail and phone on your
              record.
            </p>
          </div>

          @if (error(); as message) {
            <div class="callout error" id="sched-error" role="alert">
              <strong>We could not find you</strong>
              <span>{{ message }}</span>
            </div>
          }

          @if (lookupResult(); as found) {
            <section class="attach" id="sched-found" aria-live="polite">
              <p>
                <strong>{{ found.patient.first_name }} {{ found.patient.last_name }}</strong> ·
                <span class="mrn">{{ found.patient.mrn }}</span>
              </p>
              @if (found.appointments.length) {
                <h4 class="section-title">Your upcoming appointments</h4>
                <ul class="appt-list" id="sched-upcoming">
                  @for (a of found.appointments; track a.id) {
                    <li class="appt" [id]="'appt-' + a.id">
                      <div class="appt-main">
                        <span class="appt-when"
                          >{{ a.start_local }}
                          @if (a.confirmed_at) {
                            <span class="tag confirmed">confirmed</span>
                          }
                          @if (a.payment === 'self_pay') {
                            <span class="tag self-pay">self-pay</span>
                          }
                        </span>
                        <span class="appt-who">{{ providerLabel(a.provider) }}</span>
                        <span class="appt-meta muted small"
                          >{{ a.number }}{{ a.reason ? ' · ' + a.reason : '' }}</span
                        >
                      </div>
                      <app-check-in
                        prefix="sched"
                        [appointment]="a"
                        [busy]="busy()"
                        (checkIn)="checkIn($event)"
                      />
                      @if (a.link) {
                        <a
                          class="btn"
                          [href]="a.link.url"
                          appTip="Open this appointment's page: details, add to calendar, confirm or cancel."
                          >Open</a
                        >
                      }
                      <button
                        class="btn danger"
                        type="button"
                        [id]="'btn-cancel-' + a.id"
                        [disabled]="busy()"
                        (click)="cancel(a)"
                        appTip="Cancel this appointment. The slot becomes free for other patients."
                      >
                        Cancel
                      </button>
                    </li>
                  }
                </ul>
              } @else {
                <p class="small muted" id="sched-none">You have no upcoming appointments.</p>
              }
            </section>
          }

          <div class="dialog-actions">
            @if (lookupResult()) {
              <button
                class="btn"
                type="button"
                id="btn-sched-refresh"
                [disabled]="busy()"
                (click)="lookup()"
                appTip="Reload your appointments."
              >
                Refresh
              </button>
              <span class="spacer"></span>
              <button
                class="btn primary"
                type="button"
                id="btn-sched-book"
                [disabled]="busy()"
                (click)="toTime()"
                appTip="Choose a provider, a day and a free time for a new appointment."
              >
                Book a new appointment →
              </button>
            } @else {
              <span class="spacer"></span>
              <button
                class="btn primary"
                type="submit"
                id="btn-sched-lookup"
                [disabled]="busy()"
                appTip="Find your patient record and your appointments."
              >
                {{ busy() ? 'Looking up…' : 'Continue' }}
              </button>
            }
          </div>
        </form>
      } @else if (step() === 'time') {
        <form class="card intake-card" id="sched-time" (ngSubmit)="book()" novalidate>
          <h3>Pick a time</h3>
          @if (providers(); as list) {
            @if (!list.providers.length) {
              <div class="callout warn" id="sched-no-providers">
                <strong>No provider takes appointments online yet</strong>
                <span>Please call the office to book.</span>
              </div>
            } @else {
              <div class="field-row">
                <div class="field">
                  <label for="sched-provider">Provider</label>
                  <select
                    class="input"
                    id="sched-provider"
                    name="provider"
                    [ngModel]="providerId()"
                    (ngModelChange)="chooseProvider($event)"
                  >
                    @for (p of list.providers; track p.id) {
                      <option [ngValue]="p.id">{{ providerLabel(p) }}</option>
                    }
                  </select>
                  @if (provider(); as p) {
                    <p class="hint small muted" id="sched-weekdays">
                      In on {{ weekdaysLabel(p.weekdays) }}. Appointments last
                      {{ list.slot_minutes }} minutes{{
                        differentTimezone() ? '; times are in ' + list.timezone : ''
                      }}.
                    </p>
                  }
                </div>
                <div class="field">
                  <label for="sched-date">Day</label>
                  <input
                    class="input"
                    id="sched-date"
                    name="date"
                    type="date"
                    [min]="today"
                    [max]="maxDate()"
                    [ngModel]="date()"
                    (ngModelChange)="chooseDate($event)"
                  />
                </div>
              </div>

              <h4 class="section-title">Free times on {{ dayLabel() }}</h4>
              @if (slotsLoading()) {
                <p class="empty" id="sched-slots-loading">Loading free times…</p>
              } @else if (daySlots(); as day) {
                @if (day.note && !day.slots.length) {
                  <p class="empty" id="sched-slots-note">{{ day.note }}</p>
                } @else if (!freeCount()) {
                  <p class="empty" id="sched-slots-full">
                    No free time left on this day — please pick another day.
                  </p>
                } @else {
                  <div class="slots" id="sched-slots" role="listbox" aria-label="Free times">
                    @for (s of day.slots; track s.start_at) {
                      <button
                        type="button"
                        class="slot"
                        [class.taken]="!s.available"
                        [class.selected]="selected()?.start_at === s.start_at"
                        [disabled]="!s.available || busy()"
                        [attr.aria-selected]="selected()?.start_at === s.start_at"
                        (click)="selected.set(s)"
                        [attr.data-start]="s.start_at"
                      >
                        {{ s.label }}
                      </button>
                    }
                  </div>
                }
              }

              <div class="field">
                <label for="sched-reason"
                  >Reason for the visit <span class="muted">(optional)</span></label
                >
                <textarea
                  class="input"
                  id="sched-reason"
                  name="reason"
                  rows="2"
                  maxlength="500"
                  [(ngModel)]="reason"
                ></textarea>
              </div>

              @if (coverage(); as cover) {
                @if (cover.self_pay_required) {
                  <section class="callout warn self-pay" id="sched-self-pay" aria-live="polite">
                    <strong>
                      {{
                        cover.has_insurance
                          ? 'Your insurance does not cover this visit'
                          : 'No insurance on your record'
                      }}
                    </strong>
                    <span id="sched-coverage-message">{{ cover.message }}</span>
                    @if (cover.verification; as v) {
                      <app-insurance-verification-view [verification]="v" audience="staff" />
                    }
                    <span class="small"
                      >You can still book by agreeing to pay for the visit yourself. Corrected
                      insurance details? Update them on the
                      <a routerLink="/patients">registration page</a> first and try again.</span
                    >
                    <label class="checkbox consent" for="sched-self-pay-agree">
                      <input
                        type="checkbox"
                        id="sched-self-pay-agree"
                        name="self_pay_agreed"
                        [(ngModel)]="selfPayAgreed"
                      />
                      <span>
                        I understand that my insurance will not pay for this visit, and I agree to
                        pay for it myself (self-pay).
                      </span>
                    </label>
                  </section>
                } @else if (cover.covered) {
                  <p class="small muted" id="sched-covered">
                    ✓ Your insurance coverage is active for {{ cover.service_date }}.
                  </p>
                }
              }
            }
          } @else if (error()) {
            <div class="callout error" role="alert">
              <strong>Could not load the providers</strong>
              <span>{{ error() }}</span>
            </div>
          } @else {
            <p class="empty">Loading providers…</p>
          }

          @if (providers()?.payment_method_required) {
            @if (!account.signedIn()) {
              <div class="callout warn pay-gate" id="sched-gate-signin">
                <strong>Sign in to book</strong>
                <span class="small"
                  >Appointments need a validated payment method on your patient account: the copay
                  your insurance names (or the self-pay visit fee of
                  {{ money(providers()!.self_pay_visit_cents, providers()!.currency) }}) is put on
                  hold when you book, charged after the visit, and released if you cancel.</span
                >
                <span class="calendar-actions">
                  <button
                    class="btn"
                    type="button"
                    id="btn-sched-register"
                    (click)="shell.openAccount('register')"
                    appTip="Create a patient account with your e-mail address."
                  >
                    Register
                  </button>
                  <button
                    class="btn primary"
                    type="button"
                    id="btn-sched-login"
                    (click)="shell.openAccount('login')"
                    appTip="Sign in to your patient account."
                  >
                    Log in
                  </button>
                </span>
              </div>
            } @else if (methodsLoaded() && !bookingMethod()) {
              <div class="callout warn pay-gate" id="sched-gate-method">
                <strong>Add a payment method first</strong>
                <span class="small"
                  >No validated payment method is on file. Add a card, your Stripe or PayPal
                  account, or a bank account and make sure it is validated — then come back to
                  book.</span
                >
                <span class="calendar-actions">
                  <a
                    class="btn primary"
                    id="btn-sched-add-method"
                    routerLink="/patients/payments"
                    appTip="Your payment methods: add one, or validate one that was declined."
                    >Payment methods →</a
                  >
                </span>
              </div>
            } @else if (bookingMethod(); as m) {
              <p class="small muted" id="sched-gate-ok">
                <span class="tag confirmed">payment</span> The copay or self-pay fee goes on hold on
                {{ m.display }} when you book — charged after the visit, released if you cancel.
              </p>
            }
          }

          @if (error() && providers()) {
            <div class="callout error" id="sched-error" role="alert">
              <strong>We could not book that</strong>
              <span>{{ error() }}</span>
            </div>
          }

          <div class="dialog-actions">
            <button
              class="btn"
              type="button"
              id="btn-sched-back"
              [disabled]="busy()"
              (click)="back()"
              appTip="Back to your details and your existing appointments."
            >
              ← Back
            </button>
            <span class="spacer"></span>
            <button
              class="btn primary"
              type="submit"
              id="btn-sched-confirm"
              [disabled]="busy() || !selected() || !canBook()"
              appTip="Book the selected time. You receive the confirmation and a link to the appointment by e-mail / text."
            >
              {{
                busy()
                  ? 'Booking…'
                  : selected()
                    ? (coverage()?.self_pay_required ? 'Book as self-pay · ' : 'Book ') +
                      selected()!.label
                    : 'Pick a time'
              }}
            </button>
          </div>
        </form>
      } @else if (booked(); as a) {
        <section class="card intake-card result new" id="sched-result" aria-live="polite">
          <div class="result-icon" aria-hidden="true">✓</div>
          <h3 id="sched-result-heading">Your appointment is booked</h3>
          <ul class="appt-list">
            <li class="appt new">
              <div class="appt-main">
                <span class="appt-when" id="sched-result-when">{{ a.start_local }}</span>
                <span class="appt-who">{{ providerLabel(a.provider) }}</span>
                <span class="appt-meta muted small"
                  >{{ a.office_name }}{{ a.office_address ? ' · ' + a.office_address : '' }}</span
                >
              </div>
              <span class="mrn" id="sched-result-number">{{ a.number }}</span>
            </li>
          </ul>
          <p class="small" id="sched-result-payment">
            @if (a.payment === 'self_pay') {
              <span class="tag self-pay">self-pay</span> You agreed to pay for this visit yourself{{
                a.coverage_status && a.coverage_status !== 'none'
                  ? ' (coverage check: ' + a.coverage_status.replace('_', ' ') + ')'
                  : ''
              }}.
            } @else {
              <span class="tag confirmed">insurance</span> Your coverage was active for this day.
            }
          </p>
          @if (a.charge; as c) {
            <p class="small" id="sched-result-charge">
              <span class="tag" [class.confirmed]="c.status === 'authorized'">{{
                c.status === 'authorized' ? 'on hold' : c.status
              }}</span>
              {{ money(c.amount_cents, c.currency) }}
              {{ c.kind === 'copay' ? 'copay' : 'self-pay fee' }} on {{ c.method.display }} —
              charged after your visit, released if you cancel. See
              <a routerLink="/patients/payments" fragment="payment-history">Payment history</a>.
            </p>
          }
          @if (a.link; as link) {
            <div class="callout link-hint" id="sched-link">
              @if (link.sent_to.length) {
                <strong>We sent your appointment link by {{ describeDelivery(link) }}.</strong>
              } @else {
                <strong>Your appointment link</strong>
              }
              <span class="small"
                >The page behind it shows the details, adds the visit to your calendar, and lets you
                confirm or cancel.</span
              >
              <a class="appt-link" id="sched-link-url" [href]="link.url">{{ link.url }}</a>
              @if (link.fake) {
                <span class="fake-note small"
                  >Built-in fake messaging — nothing left this machine.
                  @if (link.outbox_url) {
                    Read the message in the
                    <a [href]="appointments.apiUrl(link.outbox_url)" target="_blank" rel="noopener"
                      >test inbox</a
                    >.
                  }
                </span>
              } @else if (link.note) {
                <span class="fake-note small">{{ link.note }}</span>
              }
            </div>
          }
          <div class="dialog-actions">
            <button
              class="btn"
              type="button"
              id="btn-sched-another"
              (click)="another()"
              appTip="Book one more appointment for the same patient."
            >
              Book another
            </button>
            <span class="spacer"></span>
            @if (a.link) {
              <a class="btn primary" id="btn-sched-open" [href]="a.link.url"
                >Open your appointment →</a
              >
            }
          </div>
        </section>
      }
    </section>
  `,
})
export class AppointmentScheduler implements OnInit {
  readonly appointments = inject(AppointmentsService);
  readonly account = inject(AccountService);
  readonly shell = inject(ShellService);
  private readonly paymentMethods = inject(PaymentMethodsService);
  private readonly toast = inject(ToastService);
  private readonly router = inject(Router);
  readonly money = money;

  // ---- the payment rule: a signed-in patient with a validated method (the copay / fee goes on hold)
  readonly methods = signal<PaymentMethod[]>([]);
  readonly methodsLoaded = signal(false);
  /** The default (or first) active method — what the hold goes on. */
  readonly bookingMethod = computed<PaymentMethod | null>(() => {
    const active = this.methods().filter((m) => m.status === 'active');
    return active.find((m) => m.is_default) ?? active[0] ?? null;
  });
  readonly canBook = computed(() => {
    const list = this.providers();
    if (!list || !list.payment_method_required) return true;
    return this.account.signedIn() && !!this.bookingMethod();
  });

  readonly step = signal<Step>('identity');
  readonly busy = signal(false);
  readonly error = signal<string | null>(null);
  readonly lookupResult = signal<AppointmentLookup | null>(null);

  constructor() {
    // The payment methods on the account (re-read after a sign-in in the account dialog).
    effect(() => {
      const signedIn = this.account.signedIn();
      untracked(() => {
        if (signedIn) void this.loadMethods();
        else {
          this.methods.set([]);
          this.methodsLoaded.set(false);
        }
      });
    });
  }

  async loadMethods(): Promise<void> {
    try {
      this.methods.set(await this.paymentMethods.list());
    } catch {
      this.methods.set([]);
    } finally {
      this.methodsLoaded.set(true);
    }
  }

  draft: IdentityDraft = { first_name: '', last_name: '', date_of_birth: '', email: '', phone: '' };
  reason = '';
  readonly today = isoDate(new Date());

  readonly providers = signal<ProviderList | null>(null);
  readonly providerId = signal<number | null>(null);
  readonly date = signal<string>(this.today);
  readonly daySlots = signal<DaySlots | null>(null);
  readonly slotsLoading = signal(false);
  readonly selected = signal<Slot | null>(null);
  readonly booked = signal<Appointment | null>(null);
  /** The coverage check for the chosen day (asked when Book is pressed; cleared on another day). */
  readonly coverage = signal<CoverageCheck | null>(null);
  selfPayAgreed = false;

  readonly providerLabel = providerLabel;
  readonly weekdaysLabel = weekdaysLabel;
  readonly describeDelivery = describeDelivery;

  readonly provider = computed<Provider | null>(
    () => this.providers()?.providers.find((p) => p.id === this.providerId()) ?? null,
  );
  readonly maxDate = computed(() => {
    const d = new Date();
    d.setDate(d.getDate() + (this.providers()?.max_days_ahead ?? 90));
    return isoDate(d);
  });
  readonly freeCount = computed(
    () => this.daySlots()?.slots.filter((s) => s.available).length ?? 0,
  );
  readonly dayLabel = computed(() => {
    const d = new Date(this.date() + 'T00:00:00');
    return isNaN(+d)
      ? this.date()
      : d.toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric' });
  });
  readonly differentTimezone = computed(() => {
    const tz = this.providers()?.timezone;
    if (!tz) return false;
    try {
      return Intl.DateTimeFormat().resolvedOptions().timeZone !== tz;
    } catch {
      return false;
    }
  });

  /** Answers for an earlier provider/day choice are discarded. */
  private slotsRequest = 0;

  ngOnInit(): void {
    const pre = this.appointments.prefill();
    if (pre) {
      this.draft = {
        first_name: pre.first_name,
        last_name: pre.last_name,
        date_of_birth: pre.date_of_birth,
        email: pre.email ?? '',
        phone: pre.phone ?? '',
      };
      this.appointments.prefill.set(null);
      void this.lookup();
    }
  }

  identity(): PatientIdentity {
    return {
      first_name: this.draft.first_name.trim(),
      last_name: this.draft.last_name.trim(),
      date_of_birth: this.draft.date_of_birth,
      email: orNull(this.draft.email),
      phone: orNull(this.draft.phone),
    };
  }

  async lookup(): Promise<void> {
    if (this.busy()) return;
    const id = this.identity();
    if (!id.first_name || !id.last_name || !/^\d{4}-\d{2}-\d{2}$/.test(id.date_of_birth)) {
      this.error.set('Please enter your first name, last name and date of birth.');
      return;
    }
    this.busy.set(true);
    this.error.set(null);
    try {
      this.lookupResult.set(await this.appointments.lookup(id));
    } catch (e) {
      this.lookupResult.set(null);
      this.error.set(this.describe(e));
    } finally {
      this.busy.set(false);
    }
  }

  /** "Check in" (identity from the form) opens the check-in page — within the window. */
  async checkIn(a: Appointment): Promise<void> {
    if (this.busy()) return;
    this.busy.set(true);
    try {
      const started = await this.appointments.checkIn(a.id, this.identity());
      await this.router.navigate(['/check-in', started.token]);
    } catch (e) {
      this.toast.error(this.describe(e));
    } finally {
      this.busy.set(false);
    }
  }

  async cancel(a: Appointment): Promise<void> {
    if (this.busy()) return;
    if (!confirm(`Cancel your appointment ${a.number} on ${a.start_local}?`)) return;
    this.busy.set(true);
    try {
      await this.appointments.cancel(a.id, this.identity());
      this.toast.show(`Appointment ${a.number} canceled.`);
      this.lookupResult.update((r) =>
        r ? { ...r, appointments: r.appointments.filter((x) => x.id !== a.id) } : r,
      );
    } catch (e) {
      this.toast.error(this.describe(e));
    } finally {
      this.busy.set(false);
    }
  }

  async toTime(): Promise<void> {
    this.error.set(null);
    this.step.set('time');
    this.selected.set(null);
    if (!this.providers()) {
      try {
        const list = await this.appointments.providers();
        this.providers.set(list);
        if (list.providers.length) await this.chooseProvider(list.providers[0].id);
      } catch (e) {
        this.error.set(this.describe(e));
      }
    }
  }

  /** A provider was chosen: start on the first day (from today) with a free slot. */
  async chooseProvider(id: number): Promise<void> {
    this.providerId.set(Number(id));
    this.selected.set(null);
    this.coverage.set(null);
    this.selfPayAgreed = false;
    const p = this.provider();
    if (!p) return;
    const start = new Date();
    for (let i = 0; i < LOOKAHEAD_DAYS; i++) {
      const d = new Date(start);
      d.setDate(start.getDate() + i);
      const iso = isoDate(d);
      if (!p.weekdays.includes(weekdayOf(iso))) continue;
      const day = await this.loadSlots(iso);
      if (day === null) return; // superseded or failed
      if (day.slots.some((s) => s.available)) return;
    }
  }

  async chooseDate(iso: string): Promise<void> {
    if (!iso) return;
    this.selected.set(null);
    this.coverage.set(null); // the check is for a day
    this.selfPayAgreed = false;
    await this.loadSlots(iso);
  }

  private async loadSlots(iso: string): Promise<DaySlots | null> {
    const id = this.providerId();
    if (!id) return null;
    const ticket = ++this.slotsRequest;
    this.date.set(iso);
    this.slotsLoading.set(true);
    try {
      const day = await this.appointments.slots(id, iso);
      if (ticket !== this.slotsRequest) return null;
      this.daySlots.set(day);
      return day;
    } catch (e) {
      if (ticket !== this.slotsRequest) return null;
      this.daySlots.set(null);
      this.error.set(this.describe(e));
      return null;
    } finally {
      if (ticket === this.slotsRequest) this.slotsLoading.set(false);
    }
  }

  async book(): Promise<void> {
    const slot = this.selected();
    const id = this.providerId();
    if (!slot || !id || this.busy()) return;
    this.busy.set(true);
    this.error.set(null);
    try {
      // First: is the insurance good for that day? If not, the patient has to agree to self-pay.
      let cover = this.coverage();
      if (!cover || cover.service_date !== slot.start_at.slice(0, 10)) {
        cover = await this.appointments.coverage(this.identity(), slot.start_at);
        this.coverage.set(cover);
        this.selfPayAgreed = false;
        if (cover.self_pay_required) return; // the agreement panel is shown; Book again once ticked
      }
      if (cover.self_pay_required && !this.selfPayAgreed) {
        this.error.set(
          'Please tick "I agree to pay for it myself" to book without active insurance coverage — or update your insurance details first.',
        );
        return;
      }
      const a = await this.appointments.book({
        ...this.identity(),
        provider_id: id,
        start_at: slot.start_at,
        reason: orNull(this.reason),
        self_pay_agreed: cover.self_pay_required && this.selfPayAgreed,
      });
      this.booked.set(a);
      this.step.set('done');
      this.toast.show(`Booked — ${a.number} on ${a.start_local}.`);
    } catch (e) {
      this.error.set(this.describe(e));
      if (e instanceof ApiError && (e.status === 409 || e.status === 400)) {
        this.selected.set(null);
        await this.loadSlots(this.date()); // the grid shows what is really free now
      }
    } finally {
      this.busy.set(false);
    }
  }

  back(): void {
    this.error.set(null);
    this.step.set('identity');
    void this.lookup();
  }

  another(): void {
    this.booked.set(null);
    this.reason = '';
    this.selected.set(null);
    this.coverage.set(null);
    this.selfPayAgreed = false;
    void this.toTime();
    void this.loadSlots(this.date());
  }

  private describe(e: unknown): string {
    if (e instanceof ApiError && e.status === 429)
      return 'Too many attempts from this device. Please wait a few minutes and try again.';
    if (e instanceof ApiError && e.offline)
      return "We can't reach the office right now. Please check your connection and try again.";
    return e instanceof Error ? e.message : String(e);
  }
}
