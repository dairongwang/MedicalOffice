import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { ApiError } from '../../core/account/api.service';
import {
  MedicationsService,
  type MyMedications,
  type PatientMedications,
  shortDate,
  supplyText,
} from '../../core/medications/medications.service';
import { TipDirective } from '../../core/ui/tip.directive';

/**
 * The medicines of the account's patient records — the body of My medicines (/patients/medicines,
 * `MedicinesPage`): one tab per patient record on the account (yourself, a child, …) with the
 * prescription medicines to take now, the dosing, how to take them (the prescription's directions), the instructions the doctor wrote for the patient and
 * what each one is for; the allergies; the medicines stopped and the prescriptions issued (folded
 * away); the pharmacy. Read-only — the office keeps the list (`GET /me/medications`).
 *
 * The tabs follow the WAI-ARIA tabs pattern: ←/→ (and Home/End) move between them.
 */
@Component({
  selector: 'app-my-medicines',
  imports: [TipDirective],
  template: `
    <section class="card intake-card meds" id="medicines-card" aria-label="Medicines by patient">
      @if (loading()) {
        <p class="empty" id="meds-loading">Loading your medicines…</p>
      } @else if (error(); as message) {
        <div class="callout error" id="meds-error" role="alert">
          <strong>Could not load your medicines</strong>
          <span>{{ message }}</span>
          <button class="btn" type="button" (click)="load()">Try again</button>
        </div>
      } @else if (data(); as d) {
        @if (!d.records.length) {
          <p class="empty" id="meds-no-records">
            No patient record is linked to this account yet — your medicines show here once it is.
          </p>
        } @else {
          <div class="meds-tabs" role="tablist" aria-label="Whose medicines">
            @for (r of d.records; track r.patient_id; let i = $index) {
              <button
                class="meds-tab"
                type="button"
                role="tab"
                [id]="'meds-tab-' + r.patient_id"
                [attr.aria-selected]="active()?.patient_id === r.patient_id"
                [attr.aria-controls]="'meds-panel-' + r.patient_id"
                [tabIndex]="active()?.patient_id === r.patient_id ? 0 : -1"
                (click)="select(r.patient_id)"
                (keydown)="onKey($event, i)"
              >
                {{ r.first_name }}
                <span class="meds-count" [attr.aria-label]="countLabel(r)">{{
                  r.current.length
                }}</span>
              </button>
            }
          </div>

          @if (active(); as r) {
            <div
              class="meds-panel"
              role="tabpanel"
              tabindex="0"
              [id]="'meds-panel-' + r.patient_id"
              [attr.aria-labelledby]="'meds-tab-' + r.patient_id"
            >
              <p class="small muted meds-who">
                {{ r.patient_name }} · <span class="mrn">{{ r.mrn }}</span> · born
                {{ shortDate(r.date_of_birth) }}
              </p>

              @if (r.allergies.length) {
                <div class="callout warn meds-allergies" [id]="'meds-allergies-' + r.patient_id">
                  <strong>Allergies</strong>
                  <span>{{ allergyText(r) }}</span>
                </div>
              } @else {
                <p class="small muted" [id]="'meds-allergies-' + r.patient_id">
                  No allergies on file.
                </p>
              }

              <h4>Take now</h4>
              @if (r.current.length) {
                <ul class="med-list" [id]="'meds-current-' + r.patient_id">
                  @for (m of r.current; track $index) {
                    <li class="med" [id]="'med-' + r.patient_id + '-' + $index">
                      <div class="med-head">
                        <strong class="med-name">{{ m.line }}</strong>
                        @if (!m.prescribed) {
                          <span
                            class="tag"
                            appTip="On your medication list — not prescribed at this office (another doctor's, or over the counter)."
                            >from your list</span
                          >
                        }
                      </div>
                      @if (m.directions) {
                        <p class="med-directions">
                          <span class="med-label">How to take it</span>{{ m.directions }}
                        </p>
                      }
                      @if (m.patient_instructions) {
                        <p class="med-instructions">
                          <span class="med-label">Your doctor's instructions</span
                          >{{ m.patient_instructions }}
                        </p>
                      }
                      @if (m.indication) {
                        <p class="small med-for">
                          <span class="med-label">For</span>{{ m.indication }}
                        </p>
                      }
                      @if (m.prescribed) {
                        <p class="small muted med-meta">
                          Prescribed by {{ m.prescriber_name || 'your provider' }} on
                          {{ shortDate(m.prescribed_on) }} · {{ m.prescription_number }}
                          @if (supplyText(m); as supply) {
                            · {{ supply }}
                          }
                          @if (m.sent_to_pharmacy) {
                            · sent to {{ m.sent_to_pharmacy }}
                          }
                        </p>
                      } @else if (m.note) {
                        <p class="small muted med-meta">{{ m.note }}</p>
                      }
                    </li>
                  }
                </ul>
              } @else {
                <p class="empty" [id]="'meds-none-' + r.patient_id">
                  No medicines to take right now.
                </p>
              }

              @if (r.stopped.length) {
                <details class="meds-more" [id]="'meds-stopped-' + r.patient_id">
                  <summary>No longer taking ({{ r.stopped.length }})</summary>
                  <ul class="med-list compact">
                    @for (m of r.stopped; track $index) {
                      <li class="med stopped">
                        <strong class="med-name">{{ m.line }}</strong>
                        @if (m.directions) {
                          <span class="small muted">{{ m.directions }}</span>
                        }
                        @if (m.note) {
                          <p class="small muted med-meta">{{ m.note }}</p>
                        }
                      </li>
                    }
                  </ul>
                </details>
              }

              @if (r.history.length) {
                <details class="meds-more" [id]="'meds-history-' + r.patient_id">
                  <summary>Prescriptions from the office ({{ r.history.length }})</summary>
                  <ul class="med-list compact">
                    @for (h of r.history; track $index) {
                      <li class="med">
                        <strong class="med-name">{{ h.line }}</strong>
                        <span class="small">{{ h.directions }}</span>
                        @if (h.patient_instructions) {
                          <p class="small med-instructions-inline">{{ h.patient_instructions }}</p>
                        }
                        <p class="small muted med-meta">
                          {{ h.prescription_number }} · {{ shortDate(h.prescribed_on) }}
                          @if (h.prescriber_name) {
                            · {{ h.prescriber_name }}
                          }
                          · {{ supplyText(h) }}
                          @if (h.indication) {
                            · for {{ h.indication }}
                          }
                        </p>
                      </li>
                    }
                  </ul>
                </details>
              }

              <p class="small meds-pharmacy">
                @if (r.pharmacy_name) {
                  Your pharmacy: <strong>{{ r.pharmacy_name }}</strong>
                  @if (r.pharmacy_phone) {
                    · {{ r.pharmacy_phone }}
                  }
                } @else {
                  No pharmacy on file yet — tell us at your next visit which one to send
                  prescriptions to.
                }
              </p>
              <p class="small muted meds-note">
                Do not stop or change a medicine without talking to your doctor first. Questions
                about a medicine or a refill? Call {{ d.office_name
                }}{{ d.office_phone ? ' (' + d.office_phone + ')' : '' }} or ask your pharmacist.
                Something missing or wrong on this list? Tell us at your next visit.
              </p>
            </div>
          }
        }
      }
    </section>
  `,
})
export class MedicinesPanel implements OnInit {
  private readonly medications = inject(MedicationsService);

  readonly loading = signal(false);
  readonly error = signal<string | null>(null);
  readonly data = signal<MyMedications | null>(null);
  /** The selected tab (patient id); the first record when none is chosen yet. */
  readonly selected = signal<number | null>(null);
  readonly active = computed<PatientMedications | null>(() => {
    const records = this.data()?.records ?? [];
    return records.find((r) => r.patient_id === this.selected()) ?? records[0] ?? null;
  });
  readonly shortDate = shortDate;
  readonly supplyText = supplyText;

  ngOnInit(): void {
    void this.load();
  }

  async load(): Promise<void> {
    this.loading.set(true);
    this.error.set(null);
    try {
      this.data.set(await this.medications.mine());
    } catch (e) {
      this.error.set(this.describe(e));
    } finally {
      this.loading.set(false);
    }
  }

  select(patientId: number): void {
    this.selected.set(patientId);
  }

  /** ←/→ move to the previous / next tab (wrapping), Home/End to the first / last — and focus it. */
  onKey(event: KeyboardEvent, index: number): void {
    const records = this.data()?.records ?? [];
    if (!records.length) return;
    const last = records.length - 1;
    const next =
      event.key === 'ArrowRight'
        ? (index + 1) % records.length
        : event.key === 'ArrowLeft'
          ? (index - 1 + records.length) % records.length
          : event.key === 'Home'
            ? 0
            : event.key === 'End'
              ? last
              : null;
    if (next === null) return;
    event.preventDefault();
    const id = records[next].patient_id;
    this.select(id);
    setTimeout(() => document.getElementById(`meds-tab-${id}`)?.focus());
  }

  countLabel(r: PatientMedications): string {
    const n = r.current.length;
    return `${n} medicine${n === 1 ? '' : 's'} to take`;
  }

  allergyText(r: PatientMedications): string {
    return r.allergies.map((a) => (a.detail ? `${a.name} (${a.detail})` : a.name)).join(', ');
  }

  private describe(e: unknown): string {
    if (e instanceof ApiError && e.status === 401) return 'Please sign in again.';
    if (e instanceof ApiError && e.offline)
      return "We can't reach the office right now. Please check your connection and try again.";
    return e instanceof Error ? e.message : String(e);
  }
}
