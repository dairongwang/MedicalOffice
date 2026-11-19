import {
  Component,
  DestroyRef,
  computed,
  effect,
  inject,
  input,
  signal,
  untracked,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { ApiError } from '../../core/account/api.service';
import { officeClock, providerLabel } from '../../core/appointments/appointments.service';
import {
  type CheckInAnswers,
  CheckInService,
  type CheckInSession,
  missingAnswers,
} from '../../core/appointments/check-in.service';
import { money } from '../../core/payments/payment-methods.service';
import { SignaturePad } from '../../core/ui/signature-pad';
import { TipDirective } from '../../core/ui/tip.directive';
import { ToastService } from '../../core/ui/toast.service';

const SCALE = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10];

/**
 * The check-in page (/check-in/<token>) — opened by "Check in" in the app, or from the link the
 * front desk sends (then it must be completed within 60 minutes of being sent):
 *
 *  1. the questionnaire about the visit, signed (typed name + drawn signature);
 *  2. the estimated cost not covered by insurance, with the patient's acceptance of financial
 *     responsibility, signed again — the estimate is pre-authorized on the payment method on file.
 *
 * The visit is checked in only when the server processed all of it; otherwise the page (and the
 * patient's board, and the office's agenda) says why the check-in failed.
 */
@Component({
  selector: 'app-check-in-page',
  imports: [FormsModule, RouterLink, TipDirective, SignaturePad],
  template: `
    <section class="page intake check-in-page" id="checkin-page" aria-labelledby="checkin-title">
      <header class="page-header">
        <div>
          <h2 id="checkin-title">Check in</h2>
          <p class="muted small">
            Answer a few questions about your visit, review what your insurance does not cover, and
            sign. You are checked in once both are signed and processed.
          </p>
        </div>
      </header>

      @if (loading() && !session()) {
        <p class="empty" id="checkin-loading">Loading your check-in…</p>
      } @else if (error(); as message) {
        <div class="callout error" id="checkin-error" role="alert">
          <strong>We could not open this check-in</strong>
          <span>{{ message }}</span>
          <a class="btn" routerLink="/patients/mine">My appointments</a>
        </div>
      } @else if (session(); as s) {
        <section class="card intake-card" id="checkin-appointment">
          <h3>{{ s.appointment.start_local }}</h3>
          <p class="small">
            {{ providerLabel(s.appointment.provider) }} · {{ s.appointment.patient.first_name }}
            {{ s.appointment.patient.last_name }} ·
            <span class="mrn">{{ s.appointment.number }}</span>
          </p>
          @if (s.status === 'pending') {
            <p class="small" id="checkin-deadline" [class.pay-problem]="minutesLeft() <= 10">
              Please finish by {{ clock(s.expires_at) }} ({{ minutesLeft() }}
              {{ minutesLeft() === 1 ? 'minute' : 'minutes' }} left){{
                s.via === 'staff' ? ' — the front desk sent you this page.' : '.'
              }}
            </p>
          }
        </section>

        @switch (s.status) {
          @case ('completed') {
            <section class="card intake-card result new" id="checkin-done" aria-live="polite">
              <div class="result-icon" aria-hidden="true">✓</div>
              <h3>You are checked in</h3>
              <p class="small">
                Your answers were signed by {{ s.answers_signer_name }} and the financial
                responsibility by {{ s.financial_signer_name }}.
              </p>
              @if (s.payment; as p) {
                <p class="small" id="checkin-done-payment">
                  <span class="tag confirmed">pre-authorized</span>
                  {{ money(p.amount_cents, p.currency) }} on {{ p.method.display }} — charged after
                  your visit, released if it is canceled.
                </p>
              } @else {
                <p class="small" id="checkin-done-payment">
                  Nothing to pre-authorize: your insurance covers the estimated cost.
                </p>
              }
              @if (s.staff_notified_at) {
                <p class="small" id="checkin-done-staff">
                  {{ s.staff_name || 'Your provider' }} has been told that you checked in.
                </p>
              }
              <div class="dialog-actions">
                <a class="btn" routerLink="/patients/payments" fragment="payment-history"
                  >Payment history</a
                >
                <span class="spacer"></span>
                <a class="btn primary" routerLink="/patients/mine">My appointments</a>
              </div>
            </section>
          }
          @case ('failed') {
            <section class="card intake-card" id="checkin-failed" role="alert">
              <div class="callout error">
                <strong>Check-in failed</strong>
                <span id="checkin-failure-reason">{{ s.failure_reason }}</span>
              </div>
              <div class="dialog-actions">
                <a class="btn" routerLink="/patients/payments">Payment methods</a>
                <span class="spacer"></span>
                @if (s.can_restart) {
                  <button
                    class="btn primary"
                    type="button"
                    id="btn-checkin-restart"
                    [disabled]="busy()"
                    (click)="restart()"
                  >
                    Start check-in again
                  </button>
                } @else {
                  <span class="small muted">Please check in at the front desk.</span>
                }
              </div>
            </section>
          }
          @case ('canceled') {
            <div class="callout warn" id="checkin-canceled">
              <strong>This check-in page is no longer valid</strong>
              <span>{{ s.failure_reason || 'It was replaced or called off.' }}</span>
              @if (s.can_restart) {
                <button class="btn primary" type="button" (click)="restart()">
                  Start check-in again
                </button>
              }
            </div>
          }
          @default {
            <form class="check-in-form" id="checkin-form" novalidate (ngSubmit)="submit()">
              <section class="card intake-card" id="checkin-questions">
                <h3>1. {{ s.questionnaire.title }}</h3>
                <p class="small muted">{{ s.questionnaire.intro }}</p>
                @for (q of s.questionnaire.questions; track q.id) {
                  <div class="field checkin-question" [id]="'q-' + q.id">
                    @switch (q.kind) {
                      @case ('consent') {
                        <label class="checkbox" [for]="'q-' + q.id + '-input'">
                          <input
                            type="checkbox"
                            [id]="'q-' + q.id + '-input'"
                            [name]="q.id"
                            [(ngModel)]="answers[q.id]"
                          />
                          <span>{{ q.label }}</span>
                        </label>
                      }
                      @case ('textarea') {
                        <label [for]="'q-' + q.id + '-input'"
                          >{{ q.label }}{{ q.required ? '' : ' (optional)' }}</label
                        >
                        <textarea
                          class="input"
                          rows="2"
                          [id]="'q-' + q.id + '-input'"
                          [name]="q.id"
                          [attr.maxlength]="q.max_length"
                          [(ngModel)]="answers[q.id]"
                        ></textarea>
                      }
                      @case ('text') {
                        <label [for]="'q-' + q.id + '-input'"
                          >{{ q.label }}{{ q.required ? '' : ' (optional)' }}</label
                        >
                        <input
                          class="input"
                          [id]="'q-' + q.id + '-input'"
                          [name]="q.id"
                          [attr.maxlength]="q.max_length"
                          [(ngModel)]="answers[q.id]"
                        />
                      }
                      @case ('scale') {
                        <span class="label">{{ q.label }}</span>
                        <div class="scale-row" role="radiogroup" [attr.aria-label]="q.label">
                          @for (n of scale; track n) {
                            <label class="scale-choice" [for]="'q-' + q.id + '-' + n">
                              <input
                                type="radio"
                                [id]="'q-' + q.id + '-' + n"
                                [name]="q.id"
                                [value]="n"
                                [(ngModel)]="answers[q.id]"
                              />
                              <span>{{ n }}</span>
                            </label>
                          }
                        </div>
                      }
                      @default {
                        <span class="label">{{ q.label }}</span>
                        <div class="choice-row" role="radiogroup" [attr.aria-label]="q.label">
                          @for (o of q.options; track o.value) {
                            <label class="checkbox inline" [for]="'q-' + q.id + '-' + o.value">
                              <input
                                type="radio"
                                [id]="'q-' + q.id + '-' + o.value"
                                [name]="q.id"
                                [value]="o.value"
                                [(ngModel)]="answers[q.id]"
                              />
                              <span>{{ o.label }}</span>
                            </label>
                          }
                        </div>
                      }
                    }
                    @if (q.hint) {
                      <p class="hint small muted">{{ q.hint }}</p>
                    }
                  </div>
                }
                <div class="field">
                  <label for="checkin-answers-name">Your full name (signature)</label>
                  <input
                    class="input"
                    id="checkin-answers-name"
                    name="answers_signer_name"
                    autocomplete="name"
                    [(ngModel)]="answersName"
                  />
                </div>
                <app-signature-pad
                  padId="checkin-answers-sig"
                  label="Sign your answers"
                  (signed)="answersSig.set($event)"
                />
                <p class="small muted">
                  By signing you confirm that your answers are true and complete to the best of your
                  knowledge.
                </p>
              </section>

              @if (s.estimate; as e) {
                <section class="card intake-card" id="checkin-estimate">
                  <h3>2. Estimated cost not covered by insurance</h3>
                  <p class="small" [class.muted]="e.covered">{{ e.coverage_message }}</p>
                  <ul class="estimate-lines">
                    @for (line of e.lines; track line.label) {
                      <li>
                        <span>{{ line.label }}</span
                        ><strong>{{ money(line.amount_cents, e.currency) }}</strong>
                      </li>
                    }
                    <li class="estimate-total">
                      <span>Your estimated responsibility</span
                      ><strong id="checkin-estimate-total">{{
                        money(e.total_cents, e.currency)
                      }}</strong>
                    </li>
                  </ul>
                  @for (note of e.notes; track note) {
                    <p class="small muted">{{ note }}</p>
                  }
                  @if (e.total_cents > 0) {
                    @if (e.payment_method; as m) {
                      <p class="small" id="checkin-estimate-method">
                        <span class="tag confirmed">payment</span> When you sign,
                        {{ money(e.total_cents, e.currency) }} is pre-authorized on {{ m }}
                        @if (e.replaces_hold_cents) {
                          (it replaces the {{ money(e.replaces_hold_cents, e.currency) }} hold
                          placed when you booked)
                        }
                        — charged after your visit, released if it is canceled.
                      </p>
                    } @else {
                      <div class="callout warn" id="checkin-no-method">
                        <strong>No payment method on file</strong>
                        <span class="small"
                          >The estimate is pre-authorized on your payment method when you sign —
                          without one the check-in fails.
                          <a routerLink="/patients/payments">Add a payment method</a> first, or
                          check in at the front desk.</span
                        >
                      </div>
                    }
                  }
                  <label class="checkbox" for="checkin-accept">
                    <input type="checkbox" id="checkin-accept" name="accept" [(ngModel)]="accept" />
                    <span
                      >I understand that I am responsible for the charges my insurance does not
                      cover, estimated at {{ money(e.total_cents, e.currency) }} for this visit, and
                      I authorize the office to pre-authorize that amount on my payment
                      method.</span
                    >
                  </label>
                  <div class="field">
                    <label for="checkin-financial-name">Your full name (signature)</label>
                    <input
                      class="input"
                      id="checkin-financial-name"
                      name="financial_signer_name"
                      autocomplete="name"
                      [(ngModel)]="financialName"
                    />
                  </div>
                  <app-signature-pad
                    padId="checkin-financial-sig"
                    label="Sign to accept financial responsibility"
                    (signed)="financialSig.set($event)"
                  />
                </section>
              }

              @if (formError(); as message) {
                <div class="callout error" id="checkin-form-error" role="alert">
                  <strong>Please check the page</strong>
                  <span>{{ message }}</span>
                </div>
              }
              <div class="dialog-actions">
                <a class="btn" routerLink="/patients/mine">Not now</a>
                <span class="spacer"></span>
                <button
                  class="btn primary"
                  type="submit"
                  id="btn-checkin-submit"
                  [disabled]="busy()"
                  appTip="Send your signed answers and acceptance — you are checked in once they are processed."
                >
                  {{ busy() ? 'Checking in…' : 'Sign and check in' }}
                </button>
              </div>
            </form>
          }
        }
      }
    </section>
  `,
})
export class CheckInPage {
  private readonly service = inject(CheckInService);
  private readonly router = inject(Router);
  private readonly toast = inject(ToastService);

  /** /check-in/:token — bound by withComponentInputBinding(). */
  readonly token = input.required<string>();

  readonly loading = signal(true);
  readonly busy = signal(false);
  readonly error = signal<string | null>(null);
  readonly formError = signal<string | null>(null);
  readonly session = signal<CheckInSession | null>(null);
  readonly answersSig = signal<string | null>(null);
  readonly financialSig = signal<string | null>(null);
  answers: CheckInAnswers = {};
  answersName = '';
  financialName = '';
  accept = false;

  readonly scale = SCALE;
  readonly money = money;
  readonly providerLabel = providerLabel;

  private readonly now = signal(Date.now());
  readonly minutesLeft = computed(() => {
    const s = this.session();
    if (!s) return 0;
    return Math.max(0, Math.ceil((Date.parse(s.expires_at) - this.now()) / 60_000));
  });

  constructor() {
    effect(() => {
      const token = this.token();
      untracked(() => void this.load(token));
    });
    const timer = setInterval(() => {
      this.now.set(Date.now());
      const s = this.session();
      // The page ran out of time while open: ask the server (it records the failure and why).
      if (s?.status === 'pending' && Date.parse(s.expires_at) <= Date.now() && !this.busy())
        void this.load(this.token());
    }, 15_000);
    inject(DestroyRef).onDestroy(() => clearInterval(timer));
  }

  clock(iso: string): string {
    return officeClock(iso, this.session()?.appointment.timezone ?? 'UTC');
  }

  async load(token: string): Promise<void> {
    this.loading.set(true);
    this.error.set(null);
    try {
      const s = await this.service.get(token);
      this.session.set(s);
      if (s.status === 'pending') {
        this.answers = { ...s.answers };
        const name = `${s.appointment.patient.first_name} ${s.appointment.patient.last_name}`;
        this.answersName ||= name;
        this.financialName ||= name;
      }
    } catch (e) {
      this.error.set(describe(e));
    } finally {
      this.loading.set(false);
    }
  }

  async submit(): Promise<void> {
    const s = this.session();
    if (!s || !s.estimate || this.busy()) return;
    const missing = missingAnswers(s.questionnaire, this.answers);
    const problem = missing.length
      ? `Please answer: ${missing.join(' · ')}`
      : this.answersName.trim().length < 2 || !this.answersSig()
        ? 'Please type your full name and sign your answers (section 1).'
        : !this.accept
          ? 'Please confirm that you are responsible for the charges not covered by your insurance (section 2).'
          : this.financialName.trim().length < 2 || !this.financialSig()
            ? 'Please type your full name and sign the financial responsibility (section 2).'
            : null;
    if (problem) {
      this.formError.set(problem);
      return;
    }
    this.busy.set(true);
    this.formError.set(null);
    try {
      const result = await this.service.submit(this.token(), {
        answers: this.answers,
        answers_signature: { signer_name: this.answersName.trim(), image_png: this.answersSig()! },
        estimate_cents: s.estimate.total_cents,
        accept_financial_responsibility: true,
        financial_signature: {
          signer_name: this.financialName.trim(),
          image_png: this.financialSig()!,
        },
      });
      this.session.set(result);
      if (result.status === 'completed') this.toast.show('You are checked in. See you soon!');
      else this.toast.error('Check-in failed — see the reason on the page.');
      window.scrollTo({ top: 0 });
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        // The estimate changed (or the page is no longer open): show the current state.
        await this.load(this.token());
      }
      this.formError.set(describe(e));
    } finally {
      this.busy.set(false);
    }
  }

  async restart(): Promise<void> {
    if (this.busy()) return;
    this.busy.set(true);
    try {
      const started = await this.service.restart(this.token());
      this.answersSig.set(null);
      this.financialSig.set(null);
      this.accept = false;
      await this.router.navigate(['/check-in', started.token]);
    } catch (e) {
      this.toast.error(describe(e));
    } finally {
      this.busy.set(false);
    }
  }
}

function describe(e: unknown): string {
  if (e instanceof ApiError && e.status === 404)
    return 'This check-in link is not valid. Please open My appointments, or ask the front desk for a new link.';
  if (e instanceof ApiError && e.status === 429)
    return 'Too many attempts from this device. Please wait a few minutes and try again.';
  if (e instanceof ApiError && e.offline)
    return "We can't reach the office right now. Please check your connection and try again.";
  return e instanceof Error ? e.message : String(e);
}
