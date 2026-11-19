import { Component, DestroyRef, OnInit, computed, inject, input, signal } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { ApiError } from '../../core/account/api.service';
import {
  type Appointment,
  AppointmentsService,
  checkInOpensLabel,
  checkInState,
  checkInWindow,
  checkedInLabel,
  officeClock,
  providerLabel,
} from '../../core/appointments/appointments.service';
import { TipDirective } from '../../core/ui/tip.directive';
import { ToastService } from '../../core/ui/toast.service';

/**
 * The page behind the link a patient receives by e-mail / text after booking
 * (/appointments/<token>): review the appointment, add it to a calendar (the .ics file opens
 * the calendar app on phones; Google Calendar / Outlook.com on the web), confirm it, or cancel
 * it. The token in the address is the proof — no sign-in, no identity form.
 *
 * The reminder the day before links here with `?action=confirm` (confirms on opening),
 * `?action=check-in` (checks in on opening once check-in is open) or `?action=cancel` (asks for
 * the confirmation click).
 *
 * Check-in: from 24 hours before the visit until it ends the page offers **Check in**, which opens
 * the check-in page (questionnaire + estimate, both signed); earlier it says when check-in opens,
 * later it sends the patient to the front desk. A link the office sent, and a failed check-in
 * (with the reason), are shown here too.
 */
@Component({
  selector: 'app-appointment-view',
  imports: [RouterLink, TipDirective],
  template: `
    <section class="page intake appointment-view" aria-labelledby="view-title">
      <header class="page-header">
        <div>
          <h2 id="view-title">Your appointment</h2>
          <p class="muted small">
            This is the page from the link we sent you. Keep it — you can come back to it any time.
          </p>
        </div>
      </header>

      @if (loading()) {
        <p class="empty" id="view-loading">Loading your appointment…</p>
      } @else if (error(); as message) {
        <div class="callout error" id="view-error" role="alert">
          <strong>We could not open this appointment</strong>
          <span>{{ message }}</span>
          <a
            class="btn"
            routerLink="/patients/appointments"
            appTip="Look up your appointments with your name and date of birth instead."
            >Find my appointments</a
          >
        </div>
      } @else if (appointment(); as a) {
        <section
          class="card intake-card result"
          [class.new]="a.status === 'scheduled'"
          [class.existing]="a.status !== 'scheduled'"
          id="view-card"
          aria-live="polite"
        >
          <div class="result-icon" aria-hidden="true">
            {{ a.status === 'scheduled' ? '✓' : '•' }}
          </div>
          <h3 id="view-heading">
            @switch (a.status) {
              @case ('scheduled') {
                {{ a.start_local }}
              }
              @case ('canceled') {
                This appointment was canceled
              }
              @case ('completed') {
                This visit took place
              }
              @default {
                This appointment is marked as a no-show
              }
            }
          </h3>
          @if (a.status === 'scheduled') {
            @switch (checkIn()) {
              @case ('checked-in') {
                <div class="callout ok" id="view-checked-in">
                  <strong>✓ You are checked in</strong>
                  <span class="small"
                    >{{ checkedInText() }}.{{
                      a.check_in?.staff_notified_at
                        ? ' ' + (a.check_in?.staff_name || 'Your provider') + ' has been told.'
                        : ''
                    }}
                    Please take a seat — we will call you.</span
                  >
                </div>
              }
              @case ('link-sent') {
                <div class="callout warn" id="view-check-in-link">
                  <strong>The front desk sent you a check-in link</strong>
                  <span class="small"
                    >Please complete it by {{ linkExpiresText() }} — it was sent by
                    {{ linkChannelsText() }}.</span
                  >
                  @if (windowOpen()) {
                    <button
                      class="btn primary"
                      type="button"
                      id="btn-view-checkin"
                      [disabled]="busy()"
                      (click)="doCheckIn()"
                    >
                      {{ busy() ? 'Opening…' : 'Check in here' }}
                    </button>
                  }
                </div>
              }
              @case ('failed') {
                <div class="callout error" id="view-check-in-failed" role="alert">
                  <strong>Check-in failed</strong>
                  <span class="small" id="view-check-in-failure">{{
                    a.check_in?.failure_reason
                  }}</span>
                  @if (windowOpen()) {
                    <button
                      class="btn primary"
                      type="button"
                      id="btn-view-checkin"
                      [disabled]="busy()"
                      (click)="doCheckIn()"
                    >
                      {{ busy() ? 'Opening…' : 'Try again' }}
                    </button>
                  } @else {
                    <span class="small">Please check in at the front desk.</span>
                  }
                </div>
              }
              @case ('open') {
                <div class="callout confirm-ask" id="view-check-in">
                  <strong>Check in for your visit</strong>
                  <span class="small"
                    >Arrived, or on your way? Check in here: a few questions about your visit and
                    the estimated cost your insurance does not cover — both signed. Open until the
                    end of your appointment.</span
                  >
                  <button
                    class="btn primary"
                    type="button"
                    id="btn-view-checkin"
                    [disabled]="busy()"
                    (click)="doCheckIn()"
                    appTip="Opens the check-in page: questionnaire and estimate, both signed."
                  >
                    {{ busy() ? 'Opening…' : 'Check in' }}
                  </button>
                </div>
              }
              @case ('not-yet') {
                @if (action() === 'check-in') {
                  <div class="callout warn" id="view-check-in-later">
                    <strong>Check-in is not open yet</strong>
                    <span class="small"
                      >You can check in from {{ opensText() }} (24 hours before your visit) — or at
                      the front desk when you arrive.</span
                    >
                  </div>
                } @else {
                  <p class="small muted" id="view-check-in-later">
                    Check-in opens {{ opensText() }} (24 hours before your visit).
                  </p>
                }
              }
              @case ('closed') {
                <p class="small muted" id="view-check-in-desk">
                  The time of this appointment has passed — please check in at the front desk.
                </p>
              }
            }
          }
          @if (a.status === 'scheduled' && !a.checked_in_at) {
            @if (a.confirmed_at) {
              <div class="callout ok" id="view-confirmed">
                <strong>✓ Confirmed — see you then!</strong>
                <span class="small">You told us you are coming. Thank you.</span>
              </div>
            } @else if (action() === 'cancel') {
              <div class="callout warn" id="view-cancel-asked">
                <strong>Cancel this appointment?</strong>
                <span class="small"
                  >Press <em>Cancel this appointment</em> below to confirm. The slot becomes free
                  for other patients.</span
                >
              </div>
            } @else {
              <div class="callout confirm-ask" id="view-confirm-ask">
                <strong>Are you coming?</strong>
                <span class="small"
                  >Please confirm so the office can plan — or cancel if you cannot make it.</span
                >
                <button
                  class="btn primary"
                  type="button"
                  id="btn-view-confirm"
                  [disabled]="busy()"
                  (click)="confirm()"
                  appTip="Tell the office you are coming to this appointment."
                >
                  {{ busy() ? 'Confirming…' : '✓ Yes, I am coming' }}
                </button>
              </div>
            }
          }
          <dl class="record">
            <div>
              <dt>Number</dt>
              <dd>
                <span class="mrn" id="view-number">{{ a.number }}</span>
              </dd>
            </div>
            <div>
              <dt>When</dt>
              <dd id="view-when">
                {{ a.start_local }}{{ differentTimezone() ? ' (' + a.timezone + ')' : '' }}
              </dd>
            </div>
            <div>
              <dt>With</dt>
              <dd id="view-provider">{{ providerLabel(a.provider) }}</dd>
            </div>
            <div>
              <dt>Where</dt>
              <dd>{{ a.office_name }}{{ a.office_address ? ' · ' + a.office_address : '' }}</dd>
            </div>
            <div>
              <dt>Patient</dt>
              <dd>{{ a.patient.first_name }} {{ a.patient.last_name }} · {{ a.patient.mrn }}</dd>
            </div>
            @if (a.reason) {
              <div>
                <dt>Reason</dt>
                <dd>{{ a.reason }}</dd>
              </div>
            }
            <div>
              <dt>Status</dt>
              <dd id="view-status">{{ statusLabel() }}</dd>
            </div>
            <div>
              <dt>Payment</dt>
              <dd id="view-payment">
                {{
                  a.payment === 'self_pay'
                    ? 'Self-pay — you agreed to pay for this visit yourself'
                    : 'Insurance (coverage was active when you booked)'
                }}
              </dd>
            </div>
            @if (a.reminder_sent_at) {
              <div>
                <dt>Reminder</dt>
                <dd id="view-reminder">Sent {{ formatDateTime(a.reminder_sent_at) }}</dd>
              </div>
            }
          </dl>

          @if (a.status === 'scheduled' && a.calendar; as cal) {
            <h4 class="section-title">Add to your calendar</h4>
            <div class="calendar-actions" id="view-calendar">
              <a
                class="btn primary"
                id="btn-view-ics"
                [href]="icsUrl()"
                download
                appTip="Download the appointment as a calendar file. On a phone it opens in your calendar app (Apple Calendar, Samsung Calendar, …); on a computer it imports into Outlook, Apple Calendar or Thunderbird."
                >📅 Add to calendar (.ics)</a
              >
              <a
                class="btn"
                id="btn-view-google"
                [href]="cal.google_url"
                target="_blank"
                rel="noopener"
                appTip="Open a pre-filled event in Google Calendar (new tab)."
                >Google Calendar</a
              >
              <a
                class="btn"
                id="btn-view-outlook"
                [href]="cal.outlook_url"
                target="_blank"
                rel="noopener"
                appTip="Open a pre-filled event in Outlook.com (new tab)."
                >Outlook.com</a
              >
            </div>
            <p class="small muted">
              A reminder one hour before the visit is included in the calendar entry. Please arrive
              a few minutes early and mention your number at the front desk.
            </p>
          }

          <div class="dialog-actions">
            <a
              class="btn"
              routerLink="/patients/appointments"
              appTip="Book another visit or see all your appointments."
              >All my appointments</a
            >
            <span class="spacer"></span>
            @if (a.status === 'scheduled') {
              <button
                class="btn danger"
                type="button"
                id="btn-view-cancel"
                [disabled]="busy()"
                (click)="cancel()"
                appTip="Cancel this appointment. The slot becomes free for other patients; you can book a new time afterwards."
              >
                {{ busy() ? 'Canceling…' : 'Cancel this appointment' }}
              </button>
            }
          </div>
        </section>
      }
    </section>
  `,
})
export class AppointmentView implements OnInit {
  private readonly appointments = inject(AppointmentsService);
  private readonly toast = inject(ToastService);
  private readonly router = inject(Router);

  /** The token from the route (/appointments/:token), bound by withComponentInputBinding(). */
  readonly token = input.required<string>();

  readonly loading = signal(true);
  readonly busy = signal(false);
  readonly error = signal<string | null>(null);
  readonly appointment = signal<Appointment | null>(null);
  readonly providerLabel = providerLabel;

  /** `?action=confirm` / `?action=cancel` from the reminder message (bound from the query string). */
  readonly action = input<string>();

  /** Re-checked every 30 s: the check-in window may open while the page is open. */
  private readonly now = signal(new Date());
  readonly checkIn = computed(() => {
    const a = this.appointment();
    return a ? checkInState(a, this.now()) : 'none';
  });
  readonly opensText = computed(() => {
    const a = this.appointment();
    return a ? checkInOpensLabel(a) : '';
  });
  readonly windowOpen = computed(() => {
    const a = this.appointment();
    return a ? checkInWindow(a, this.now()) === 'open' : false;
  });
  readonly linkExpiresText = computed(() => {
    const a = this.appointment();
    return a?.check_in ? officeClock(a.check_in.expires_at, a.timezone) : '';
  });
  readonly linkChannelsText = computed(
    () =>
      (this.appointment()?.check_in?.link_channels ?? [])
        .map((c) => (c === 'sms' ? 'text message' : 'e-mail'))
        .join(' and ') || 'e-mail',
  );
  readonly checkedInText = computed(() => {
    const a = this.appointment();
    return a ? checkedInLabel(a) : '';
  });

  readonly statusLabel = computed(() => {
    const a = this.appointment();
    if (!a) return '';
    const base = {
      scheduled: 'Scheduled',
      canceled: 'Canceled',
      completed: 'Completed',
      no_show: 'No-show',
    }[a.status];
    if (a.status !== 'scheduled') return base;
    if (a.checked_in_at) return `${base} · checked in`;
    return a.confirmed_at ? `${base} · confirmed` : base;
  });

  constructor() {
    const timer = setInterval(() => this.now.set(new Date()), 30_000);
    inject(DestroyRef).onDestroy(() => clearInterval(timer));
  }
  readonly differentTimezone = computed(() => {
    const tz = this.appointment()?.timezone;
    if (!tz) return false;
    try {
      return Intl.DateTimeFormat().resolvedOptions().timeZone !== tz;
    } catch {
      return false;
    }
  });

  ngOnInit(): void {
    void this.load();
  }

  icsUrl(): string {
    const cal = this.appointment()?.calendar;
    return cal ? this.appointments.apiUrl(cal.ics_url) : '#';
  }

  private async load(): Promise<void> {
    this.loading.set(true);
    try {
      const a = await this.appointments.byToken(this.token());
      this.appointment.set(a);
      // The reminder's "Confirm" link: confirm on opening.
      if (this.action() === 'confirm' && a.status === 'scheduled' && !a.confirmed_at) {
        await this.confirm();
      }
      // The reminder's "Check in" link: open the check-in page — once the window is open.
      if (this.action() === 'check-in' && checkInWindow(a) === 'open' && !a.checked_in_at) {
        await this.doCheckIn();
      }
    } catch (e) {
      this.error.set(this.describe(e));
    } finally {
      this.loading.set(false);
    }
  }

  async confirm(): Promise<void> {
    const a = this.appointment();
    if (!a || this.busy()) return;
    this.busy.set(true);
    try {
      this.appointment.set(await this.appointments.confirmByToken(this.token()));
      this.toast.show(`Thank you — ${a.number} is confirmed.`);
    } catch (e) {
      this.toast.error(this.describe(e));
    } finally {
      this.busy.set(false);
    }
  }

  /** Open the check-in page (questionnaire + estimate, both signed) for this visit. */
  async doCheckIn(): Promise<void> {
    const a = this.appointment();
    if (!a || this.busy()) return;
    this.busy.set(true);
    try {
      const started = await this.appointments.checkInByToken(this.token());
      await this.router.navigate(['/check-in', started.token]);
    } catch (e) {
      this.toast.error(this.describe(e));
    } finally {
      this.busy.set(false);
    }
  }

  formatDateTime(iso: string): string {
    const d = new Date(iso);
    return isNaN(+d)
      ? iso
      : d.toLocaleString(undefined, {
          month: 'short',
          day: 'numeric',
          hour: 'numeric',
          minute: '2-digit',
        });
  }

  async cancel(): Promise<void> {
    const a = this.appointment();
    if (!a || this.busy()) return;
    if (!confirm(`Cancel your appointment ${a.number} on ${a.start_local}?`)) return;
    this.busy.set(true);
    try {
      this.appointment.set(await this.appointments.cancelByToken(this.token()));
      this.toast.show(`Appointment ${a.number} canceled.`);
    } catch (e) {
      this.toast.error(this.describe(e));
    } finally {
      this.busy.set(false);
    }
  }

  private describe(e: unknown): string {
    if (e instanceof ApiError && e.status === 404)
      return 'This link is not valid (it may have been typed incorrectly). Please look up your appointments with your name and date of birth.';
    if (e instanceof ApiError && e.status === 429)
      return 'Too many attempts from this device. Please wait a few minutes and try again.';
    if (e instanceof ApiError && e.offline)
      return "We can't reach the office right now. Please check your connection and try again.";
    return e instanceof Error ? e.message : String(e);
  }
}
