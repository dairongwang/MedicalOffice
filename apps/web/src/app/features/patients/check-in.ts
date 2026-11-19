import { Component, DestroyRef, computed, inject, input, output, signal } from '@angular/core';
import {
  type Appointment,
  checkInOpensLabel,
  checkInState,
  checkInWindow,
  checkedInLabel,
  officeClock,
} from '../../core/appointments/appointments.service';
import { TipDirective } from '../../core/ui/tip.directive';

/** How often the control re-checks the clock (the window opens while the page may be open). */
const TICK_MS = 30_000;

/**
 * The check-in part of one appointment in a list (My appointments, the lookup page):
 *
 *  - checked in     → "✓ Checked in in the app at 09:12"
 *  - link sent      → the office sent the check-in page — "complete it by 10:42" (+ "Check in here")
 *  - failed         → "Check-in failed: <why>" (+ "Try again" while the window is open)
 *  - window open    → a **Check in** button — the host opens the check-in page
 *  - not open yet   → "Check-in opens Tue, Jun 6, 09:30" (24 hours before the visit)
 *  - visit over     → "Check in at the front desk"
 *
 * Element ids: `btn-<prefix>-checkin-<id>`, `<prefix>-checked-in-<id>`, `<prefix>-checkin-later-<id>`,
 * `<prefix>-checkin-failed-<id>`, `<prefix>-checkin-link-<id>`, `<prefix>-checkin-desk-<id>`.
 */
@Component({
  selector: 'app-check-in',
  imports: [TipDirective],
  template: `
    @switch (state()) {
      @case ('checked-in') {
        <span
          class="tag confirmed check-in-done"
          [id]="prefix() + '-checked-in-' + appointment().id"
        >
          ✓ {{ checkedIn() }}
        </span>
      }
      @case ('link-sent') {
        <span class="small check-in-link" [id]="prefix() + '-checkin-link-' + appointment().id"
          >The front desk sent you a check-in link by {{ channels() }} — please complete it by
          {{ linkExpires() }}.</span
        >
      }
      @case ('failed') {
        <span
          class="small pay-problem check-in-failed"
          [id]="prefix() + '-checkin-failed-' + appointment().id"
          >Check-in failed: {{ appointment().check_in?.failure_reason }}</span
        >
        @if (!windowOpen()) {
          <span class="small muted">Please check in at the front desk.</span>
        }
      }
      @case ('not-yet') {
        <span
          class="small muted check-in-later"
          [id]="prefix() + '-checkin-later-' + appointment().id"
          appTip="You can check in from the app from 24 hours before the visit, or at the front desk when you arrive."
          >Check-in opens {{ opens() }}</span
        >
      }
      @case ('closed') {
        <span
          class="small muted check-in-desk"
          [id]="prefix() + '-checkin-desk-' + appointment().id"
          >Check in at the front desk</span
        >
      }
    }
    @if (showButton()) {
      <button
        class="btn primary"
        type="button"
        [id]="'btn-' + prefix() + '-checkin-' + appointment().id"
        [disabled]="busy()"
        (click)="checkIn.emit(appointment())"
        appTip="Opens the check-in page: a few questions about your visit and the estimated cost your insurance does not cover — both signed. Open from 24 hours before the visit."
      >
        {{ busy() ? 'Opening…' : buttonLabel() }}
      </button>
    }
  `,
})
export class CheckInControl {
  readonly appointment = input.required<Appointment>();
  readonly busy = input(false);
  /** Id prefix of the host page: "mine", "sched", … */
  readonly prefix = input('appt');
  readonly checkIn = output<Appointment>();

  private readonly now = signal(new Date());
  readonly state = computed(() => checkInState(this.appointment(), this.now()));
  readonly windowOpen = computed(() => checkInWindow(this.appointment(), this.now()) === 'open');
  /** The button: inside the patient's window, unless already checked in. */
  readonly showButton = computed(() => this.windowOpen() && this.state() !== 'checked-in');
  readonly buttonLabel = computed(() =>
    this.state() === 'failed'
      ? 'Try again'
      : this.state() === 'link-sent'
        ? 'Check in here'
        : 'Check in',
  );
  readonly opens = computed(() => checkInOpensLabel(this.appointment()));
  readonly checkedIn = computed(() => checkedInLabel(this.appointment()));
  readonly linkExpires = computed(() => {
    const c = this.appointment().check_in;
    return c ? officeClock(c.expires_at, this.appointment().timezone) : '';
  });
  readonly channels = computed(
    () =>
      (this.appointment().check_in?.link_channels ?? [])
        .map((c) => (c === 'sms' ? 'text message' : 'e-mail'))
        .join(' and ') || 'e-mail',
  );

  constructor() {
    const timer = setInterval(() => this.now.set(new Date()), TICK_MS);
    inject(DestroyRef).onDestroy(() => clearInterval(timer));
  }
}
