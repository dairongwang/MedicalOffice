import { Component, OnInit, inject, input, output, signal } from '@angular/core';
import { ApiError } from '../../core/account/api.service';
import {
  AppointmentsService,
  type VisitSummary,
} from '../../core/appointments/appointments.service';
import { TipDirective } from '../../core/ui/tip.directive';
import { ToastService } from '../../core/ui/toast.service';

/**
 * The visit summary the doctor signed after a visit (`GET /me/appointments/{id}/visit-summary`):
 * why the patient came, the vital signs, the diagnoses, the assessment and plan, the medications
 * prescribed, referrals and tests, the current medication / allergy lists, the instructions and
 * the follow-up — with the doctor's signature and the verification code. "Download PDF" fetches
 * the same document the patient got by e-mail.
 *
 * Shown inline under a past visit on My appointments (`<app-visit-summary [appointmentId]>`).
 */
@Component({
  selector: 'app-visit-summary',
  imports: [TipDirective],
  template: `
    <section
      class="visit-summary"
      [id]="'visit-summary-' + appointmentId()"
      aria-label="Visit summary"
      aria-live="polite"
    >
      @if (loading()) {
        <p class="empty">Loading your visit summary…</p>
      } @else if (error(); as message) {
        <div class="callout error" role="alert">
          <strong>Could not load the visit summary</strong>
          <span>{{ message }}</span>
        </div>
      } @else if (summary(); as s) {
        <header class="vs-head">
          <div>
            <h4>Visit summary</h4>
            <p class="small muted">
              {{ s.content.visit_date }} ({{ s.content.timezone }}) ·
              {{ s.content.provider_name }} · {{ s.content.visit_number }}
              @if (s.version > 1) {
                · amended (version {{ s.version }})
              }
            </p>
          </div>
          <span class="spacer"></span>
          <button
            class="btn"
            type="button"
            [id]="'btn-visit-summary-pdf-' + appointmentId()"
            [disabled]="downloading()"
            (click)="download(s)"
            appTip="The signed summary as a PDF — the same document we sent you by e-mail."
          >
            {{ downloading() ? 'Preparing…' : 'Download PDF' }}
          </button>
          <button
            class="btn"
            type="button"
            (click)="closed.emit()"
            appTip="Hide the visit summary."
          >
            Close
          </button>
        </header>

        @if (s.content.reason) {
          <div class="vs-part">
            <h5>Why you came</h5>
            <p>{{ s.content.reason }}</p>
          </div>
        }
        @if (s.content.vitals) {
          <div class="vs-part">
            <h5>Vital signs</h5>
            <p>
              {{ s.content.vitals
              }}{{ s.content.vitals_note ? ' (' + s.content.vitals_note + ')' : '' }}
            </p>
          </div>
        }
        @if (s.content.diagnoses.length) {
          <div class="vs-part">
            <h5>Diagnoses</h5>
            <ul>
              @for (d of s.content.diagnoses; track $index) {
                <li>{{ d }}</li>
              }
            </ul>
          </div>
        }
        @if (s.assessment) {
          <div class="vs-part">
            <h5>Assessment</h5>
            <p class="vs-text">{{ s.assessment }}</p>
          </div>
        }
        @if (s.plan) {
          <div class="vs-part">
            <h5>Plan</h5>
            <p class="vs-text">{{ s.plan }}</p>
          </div>
        }
        @if (s.content.prescriptions.length) {
          <div class="vs-part">
            <h5>Medications prescribed at this visit</h5>
            <ul>
              @for (rx of s.content.prescriptions; track $index) {
                <li>{{ rx }}</li>
              }
            </ul>
          </div>
        }
        <div class="vs-part">
          <h5>Your medications</h5>
          @if (s.content.medications.length) {
            <ul>
              @for (m of s.content.medications; track $index) {
                <li>{{ m }}</li>
              }
            </ul>
          } @else {
            <p class="muted">None recorded.</p>
          }
        </div>
        <div class="vs-part">
          <h5>Allergies</h5>
          @if (s.content.allergies.length) {
            <ul>
              @for (a of s.content.allergies; track $index) {
                <li>{{ a }}</li>
              }
            </ul>
          } @else {
            <p class="muted">No known allergies recorded.</p>
          }
        </div>
        @if (s.content.referrals.length) {
          <div class="vs-part">
            <h5>Referrals</h5>
            <ul>
              @for (r of s.content.referrals; track $index) {
                <li>{{ r }}</li>
              }
            </ul>
          </div>
        }
        @if (s.content.tests.length) {
          <div class="vs-part">
            <h5>Tests</h5>
            <ul>
              @for (t of s.content.tests; track $index) {
                <li>{{ t }}</li>
              }
            </ul>
          </div>
        }
        @if (s.instructions) {
          <div class="vs-part vs-instructions">
            <h5>Your instructions</h5>
            <p class="vs-text">{{ s.instructions }}</p>
          </div>
        }
        @if (s.follow_up) {
          <div class="vs-part">
            <h5>Follow-up</h5>
            <p class="vs-text">{{ s.follow_up }}</p>
          </div>
        }

        <div class="vs-signature" [id]="'visit-summary-signature-' + appointmentId()">
          @if (s.signature_png) {
            <img [src]="s.signature_png" [alt]="'Signature of ' + s.signer_name" />
          }
          <p class="small">
            <strong>Electronically signed by {{ s.provider_name || s.signer_name }}</strong>
            @if (s.signed_at) {
              on {{ when(s.signed_at) }}
            }
          </p>
          <p class="small muted">Verification code {{ s.verification }}</p>
        </div>
        <p class="small muted">
          Questions about this summary? Contact {{ s.content.office_name
          }}{{ s.content.office_phone ? ' at ' + s.content.office_phone : '' }}. If your symptoms
          get worse, contact the office; in an emergency call your local emergency number.
        </p>
      }
    </section>
  `,
})
export class VisitSummaryView implements OnInit {
  private readonly appointments = inject(AppointmentsService);
  private readonly toast = inject(ToastService);

  readonly appointmentId = input.required<number>();
  readonly closed = output<void>();

  readonly loading = signal(true);
  readonly downloading = signal(false);
  readonly error = signal<string | null>(null);
  readonly summary = signal<VisitSummary | null>(null);

  ngOnInit(): void {
    void this.load();
  }

  async load(): Promise<void> {
    this.loading.set(true);
    this.error.set(null);
    try {
      this.summary.set(await this.appointments.visitSummary(this.appointmentId()));
    } catch (e) {
      this.error.set(describe(e));
    } finally {
      this.loading.set(false);
    }
  }

  when(iso: string): string {
    return new Date(iso).toLocaleString(undefined, {
      year: 'numeric',
      month: 'long',
      day: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
    });
  }

  async download(s: VisitSummary): Promise<void> {
    if (this.downloading()) return;
    this.downloading.set(true);
    try {
      const blob = await this.appointments.visitSummaryPdf(this.appointmentId());
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${s.number}-v${s.version}.pdf`;
      document.body.append(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch (e) {
      this.toast.error(describe(e));
    } finally {
      this.downloading.set(false);
    }
  }
}

function describe(e: unknown): string {
  if (e instanceof ApiError && e.status === 401) return 'Please sign in again.';
  if (e instanceof ApiError && e.offline)
    return "We can't reach the office right now. Please check your connection and try again.";
  return e instanceof Error ? e.message : String(e);
}
