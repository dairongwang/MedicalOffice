import { Component, effect, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { ApiError } from '../../core/account/api.service';
import { AccountService } from '../../core/account/account.service';
import {
  type Appointment,
  AppointmentsService,
  type MyAppointments as MyAppointmentsData,
  type MyPatientRecord,
  providerLabel,
} from '../../core/appointments/appointments.service';
import { PatientsService } from '../../core/patients/patients.service';
import { ShellService } from '../../core/ui/shell.service';
import { TipDirective } from '../../core/ui/tip.directive';
import { ToastService } from '../../core/ui/toast.service';
import { CheckInControl } from './check-in';
import { VisitSummaryView } from './visit-summary';

const orNull = (v: string): string | null => (v.trim() ? v.trim() : null);

/**
 * My appointments (/patients/mine): everything on the signed-in patient account's records —
 * upcoming (check in from 24 hours before / confirm / cancel / open the link's page) and past —
 * with the visit summary the doctor signed after each visit (`VisitSummaryView`) — plus the
 * records themselves. (The medicines to take are on My medicines, /patients/medicines.)
 *
 * A record belongs to the account when its e-mail is the account's (linked automatically),
 * when the patient booked while signed in, or when it is attached here by identity
 * (`POST /me/patients/link` — a child registered without this e-mail, …). "Book" hands the
 * record's identity to the scheduler through `AppointmentsService.prefill`.
 *
 * This page is the patient's dashboard; the payment methods on file and the payment history are
 * on Payments (/patients/payments).
 *
 * Without an account the page explains and offers to log in / register (the app shell's
 * account dialog, through ShellService).
 */
@Component({
  selector: 'app-my-appointments',
  imports: [FormsModule, RouterLink, TipDirective, CheckInControl, VisitSummaryView],
  template: `
    <section class="page intake mine" aria-labelledby="mine-title">
      <header class="page-header">
        <div>
          <h2 id="mine-title">My appointments</h2>
          <p class="muted small">
            Your dashboard: all appointments on your patient records, upcoming and past, and your
            records (payment methods are under
            <a routerLink="/patients/payments" id="mine-payments-link">Payments</a>). Check in here
            from 24 hours before a visit — or at the front desk when you arrive.
          </p>
        </div>
      </header>

      @if (!account.signedIn()) {
        <section class="card intake-card" id="mine-signed-out">
          <h3>Sign in to see your appointments</h3>
          <p class="small muted">
            A patient account is optional — booking works without one — but it shows every
            appointment on your records (and your family's, once attached) and lets you confirm or
            cancel with one tap.
          </p>
          <div class="dialog-actions">
            <a
              class="btn"
              routerLink="/patients/appointments"
              appTip="See or book appointments with your name and date of birth instead."
              >Without an account</a
            >
            <span class="spacer"></span>
            <button
              class="btn"
              type="button"
              id="btn-mine-register"
              (click)="shell.openAccount('register')"
              appTip="Create a patient account with your e-mail address."
            >
              Register
            </button>
            <button
              class="btn primary"
              type="button"
              id="btn-mine-login"
              (click)="shell.openAccount('login')"
              appTip="Sign in to your patient account."
            >
              Log in
            </button>
          </div>
        </section>
      } @else if (loading()) {
        <p class="empty" id="mine-loading">Loading your appointments…</p>
      } @else if (error(); as message) {
        <div class="callout error" id="mine-error" role="alert">
          <strong>Could not load your appointments</strong>
          <span>{{ message }}</span>
          <button class="btn" type="button" (click)="load()">Try again</button>
        </div>
      } @else if (data(); as d) {
        <section class="card intake-card" id="mine-upcoming">
          <h3>Upcoming</h3>
          @if (d.upcoming.length) {
            <ul class="appt-list">
              @for (a of d.upcoming; track a.id) {
                <li class="appt" [id]="'mine-appt-' + a.id">
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
                      >{{ a.number }} · {{ a.patient.first_name }} {{ a.patient.last_name }} ({{
                        a.patient.mrn
                      }}){{ a.reason ? ' · ' + a.reason : '' }}</span
                    >
                  </div>
                  <app-check-in
                    prefix="mine"
                    [appointment]="a"
                    [busy]="busy()"
                    (checkIn)="checkIn($event)"
                  />
                  @if (!a.confirmed_at) {
                    <button
                      class="btn"
                      type="button"
                      [id]="'btn-mine-confirm-' + a.id"
                      [disabled]="busy()"
                      (click)="confirm(a)"
                      appTip="Tell the office you are coming."
                    >
                      ✓ I am coming
                    </button>
                  }
                  @if (a.link) {
                    <a
                      class="btn"
                      [href]="a.link.url"
                      appTip="This appointment's page: details, add to calendar, confirm or cancel."
                      >Open</a
                    >
                  }
                  <button
                    class="btn danger"
                    type="button"
                    [id]="'btn-mine-cancel-' + a.id"
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
            <p class="empty" id="mine-no-upcoming">No upcoming appointments.</p>
          }
          <div class="dialog-actions">
            <span class="spacer"></span>
            @if (d.patients.length) {
              <button
                class="btn primary"
                type="button"
                id="btn-mine-book"
                (click)="book(d.patients[0])"
                appTip="Book a new appointment — your details are filled in for you."
              >
                Book an appointment →
              </button>
            }
          </div>
        </section>

        <section class="card intake-card" id="mine-records">
          <h3>Your patient records</h3>
          @if (d.patients.length) {
            <ul class="appt-list">
              @for (p of d.patients; track p.id) {
                <li class="appt record-row" [id]="'mine-record-' + p.id">
                  <div class="appt-main">
                    <span class="appt-when">{{ p.first_name }} {{ p.last_name }}</span>
                    <span class="appt-meta muted small"
                      ><span class="mrn">{{ p.mrn }}</span> · born {{ p.date_of_birth }} ·
                      {{
                        p.linked_by === 'email'
                          ? 'linked by your e-mail address'
                          : 'attached by name and date of birth'
                      }}</span
                    >
                  </div>
                  <button
                    class="btn"
                    type="button"
                    [id]="'btn-mine-edit-' + p.id"
                    (click)="edit(p)"
                    appTip="Update the contact details, address, insurance or emergency contact on this record."
                  >
                    Update details
                  </button>
                  <button
                    class="btn"
                    type="button"
                    [id]="'btn-mine-book-' + p.id"
                    (click)="book(p)"
                    appTip="Book an appointment for this record."
                  >
                    Book
                  </button>
                </li>
              }
            </ul>
          } @else {
            <p class="empty" id="mine-no-records">
              No patient record is linked to this account yet. Register first, or attach an existing
              record below.
            </p>
          }

          <form class="attach" id="mine-attach" (ngSubmit)="attach()" novalidate>
            <div>
              <strong>Attach a record</strong>
              <p class="small muted">
                A record registered without this e-mail address — a child, or yourself from a visit
                years ago. Enter the name and date of birth as registered.
              </p>
            </div>
            <div class="field-row">
              <div class="field">
                <label for="mine-first-name">First name</label>
                <input
                  class="input"
                  id="mine-first-name"
                  name="first_name"
                  [(ngModel)]="attachDraft.first_name"
                />
              </div>
              <div class="field">
                <label for="mine-last-name">Last name</label>
                <input
                  class="input"
                  id="mine-last-name"
                  name="last_name"
                  [(ngModel)]="attachDraft.last_name"
                />
              </div>
            </div>
            <div class="field-row">
              <div class="field">
                <label for="mine-dob">Date of birth</label>
                <input
                  class="input"
                  id="mine-dob"
                  name="date_of_birth"
                  type="date"
                  [(ngModel)]="attachDraft.date_of_birth"
                />
              </div>
              <div class="field">
                <label for="mine-phone"
                  >Phone on the record <span class="muted">(optional)</span></label
                >
                <input
                  class="input"
                  id="mine-phone"
                  name="phone"
                  type="tel"
                  [(ngModel)]="attachDraft.phone"
                />
              </div>
            </div>
            @if (attachError(); as message) {
              <div class="callout error" id="mine-attach-error" role="alert">
                <strong>Could not attach the record</strong>
                <span>{{ message }}</span>
              </div>
            }
            <div class="dialog-actions">
              <span class="spacer"></span>
              <button
                class="btn"
                type="submit"
                id="btn-mine-attach"
                [disabled]="busy()"
                appTip="Find the record by name and date of birth and add it to this account."
              >
                {{ busy() ? 'Attaching…' : 'Attach record' }}
              </button>
            </div>
          </form>
        </section>

        @if (d.past.length) {
          <section class="card intake-card" id="mine-past">
            <h3>Past</h3>
            <ul class="appt-list">
              @for (a of d.past; track a.id) {
                <li class="appt past" [id]="'mine-past-' + a.id">
                  <div class="appt-main">
                    <span class="appt-when"
                      >{{ a.start_local }}
                      @if (
                        a.visit_summary?.status === 'signed' && !a.visit_summary?.patient_viewed_at
                      ) {
                        <span class="tag new-summary">new summary</span>
                      }
                    </span>
                    <span class="appt-who">{{ providerLabel(a.provider) }}</span>
                    <span class="appt-meta muted small"
                      >{{ a.number }} · {{ a.patient.first_name }} {{ a.patient.last_name }} ·
                      {{ statusLabel(a) }}</span
                    >
                  </div>
                  @if (a.visit_summary?.status === 'signed') {
                    <button
                      class="btn"
                      type="button"
                      [id]="'btn-mine-summary-' + a.id"
                      [attr.aria-expanded]="openSummary() === a.id"
                      (click)="toggleSummary(a)"
                      appTip="The summary of this visit, signed by your doctor: diagnoses, plan, medications, instructions and follow-up."
                    >
                      {{ openSummary() === a.id ? 'Hide summary' : 'Visit summary' }}
                    </button>
                  }
                </li>
                @if (openSummary() === a.id) {
                  <li class="appt-summary">
                    <app-visit-summary [appointmentId]="a.id" (closed)="openSummary.set(null)" />
                  </li>
                }
              }
            </ul>
          </section>
        }
      }
    </section>
  `,
})
export class MyAppointments {
  readonly account = inject(AccountService);
  readonly shell = inject(ShellService);
  private readonly appointments = inject(AppointmentsService);
  private readonly patients = inject(PatientsService);
  private readonly toast = inject(ToastService);
  private readonly router = inject(Router);

  readonly loading = signal(false);
  readonly busy = signal(false);
  readonly error = signal<string | null>(null);
  readonly attachError = signal<string | null>(null);
  readonly data = signal<MyAppointmentsData | null>(null);
  /** The past visit whose summary is open (one at a time). */
  readonly openSummary = signal<number | null>(null);
  readonly providerLabel = providerLabel;

  attachDraft = { first_name: '', last_name: '', date_of_birth: '', phone: '' };

  constructor() {
    // Load on arrival and again after a sign-in / sign-out in the account dialog.
    effect(() => {
      if (this.account.signedIn()) void this.load();
      else this.data.set(null);
    });
  }

  async load(): Promise<void> {
    this.loading.set(true);
    this.error.set(null);
    try {
      this.data.set(await this.appointments.mine());
    } catch (e) {
      this.error.set(this.describe(e));
    } finally {
      this.loading.set(false);
    }
  }

  /** Open / close a visit's summary; opening marks it read (the server records the first opening). */
  toggleSummary(a: Appointment): void {
    if (this.openSummary() === a.id) {
      this.openSummary.set(null);
      return;
    }
    this.openSummary.set(a.id);
    const brief = a.visit_summary;
    if (brief && !brief.patient_viewed_at) {
      const seen = { ...brief, patient_viewed_at: new Date().toISOString() };
      this.data.update((d) =>
        d
          ? { ...d, past: d.past.map((x) => (x.id === a.id ? { ...x, visit_summary: seen } : x)) }
          : d,
      );
    }
  }

  statusLabel(a: Appointment): string {
    return {
      scheduled: 'Scheduled',
      canceled: 'Canceled',
      completed: 'Completed',
      no_show: 'No-show',
    }[a.status];
  }

  async confirm(a: Appointment): Promise<void> {
    if (this.busy()) return;
    this.busy.set(true);
    try {
      const updated = await this.appointments.confirmMine(a.id);
      this.replace(updated);
      this.toast.show(`Thank you — ${a.number} is confirmed.`);
    } catch (e) {
      this.toast.error(this.describe(e));
    } finally {
      this.busy.set(false);
    }
  }

  /** "Check in" opens the check-in page (questionnaire, estimate, signatures) — within the window. */
  async checkIn(a: Appointment): Promise<void> {
    if (this.busy()) return;
    this.busy.set(true);
    try {
      const started = await this.appointments.checkInMine(a.id);
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
      await this.appointments.cancelMine(a.id);
      this.toast.show(`Appointment ${a.number} canceled.`);
      await this.load();
    } catch (e) {
      this.toast.error(this.describe(e));
    } finally {
      this.busy.set(false);
    }
  }

  async attach(): Promise<void> {
    if (this.busy()) return;
    const d = this.attachDraft;
    if (
      !d.first_name.trim() ||
      !d.last_name.trim() ||
      !/^\d{4}-\d{2}-\d{2}$/.test(d.date_of_birth)
    ) {
      this.attachError.set('Please enter the first name, last name and date of birth.');
      return;
    }
    this.busy.set(true);
    this.attachError.set(null);
    try {
      const records = await this.appointments.linkRecord({
        first_name: d.first_name.trim(),
        last_name: d.last_name.trim(),
        date_of_birth: d.date_of_birth,
        email: null,
        phone: orNull(d.phone),
      });
      this.toast.show(
        `Attached — ${records.length} record${records.length === 1 ? '' : 's'} on your account.`,
      );
      this.attachDraft = { first_name: '', last_name: '', date_of_birth: '', phone: '' };
      await this.load();
    } catch (e) {
      this.attachError.set(this.describe(e));
    } finally {
      this.busy.set(false);
    }
  }

  /** Update one of the records: the registration page opens its form for it (signed in, no code needed). */
  edit(p: MyPatientRecord): void {
    this.patients.editRequest.set(p);
    void this.router.navigateByUrl('/patients');
  }

  /** Book for one of the records: the scheduler starts with the identity filled in. */
  book(p: MyPatientRecord): void {
    this.appointments.prefill.set({
      first_name: p.first_name,
      last_name: p.last_name,
      date_of_birth: p.date_of_birth,
      email: p.email,
      phone: p.phone,
    });
    void this.router.navigateByUrl('/patients/appointments');
  }

  private replace(updated: Appointment): void {
    this.data.update((d) =>
      d ? { ...d, upcoming: d.upcoming.map((x) => (x.id === updated.id ? updated : x)) } : d,
    );
  }

  private describe(e: unknown): string {
    if (e instanceof ApiError && e.status === 401) return 'Please sign in again.';
    if (e instanceof ApiError && e.offline)
      return "We can't reach the office right now. Please check your connection and try again.";
    return e instanceof Error ? e.message : String(e);
  }
}
