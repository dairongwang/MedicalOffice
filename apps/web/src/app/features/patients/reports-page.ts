import { Component, effect, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { AccountService } from '../../core/account/account.service';
import { ApiError } from '../../core/account/api.service';
import {
  AppointmentsService,
  type MyPatientRecord,
} from '../../core/appointments/appointments.service';
import {
  EXAM_SYSTEMS,
  FITNESS_LABELS,
  type PatientReport,
  REPORT_KINDS,
  REPORT_PURPOSES,
  type ReportKind,
  type ReportPurpose,
  ReportsService,
  isOpen,
} from '../../core/reports/reports.service';
import { ShellService } from '../../core/ui/shell.service';
import { TipDirective } from '../../core/ui/tip.directive';
import { ToastService } from '../../core/ui/toast.service';

/**
 * My reports (/patients/reports): ask the office for the **immunization record** or the **annual health
 * examination report** of one of the account's records (what for, by when); the office prepares it from the
 * record and a **doctor signs** it. Signed reports are shown here (and come by e-mail as a PDF); open
 * requests can be withdrawn.
 */
@Component({
  selector: 'app-reports-page',
  imports: [FormsModule, TipDirective],
  template: `
    <section class="page intake mine" aria-labelledby="reports-title">
      <header class="page-header">
        <div>
          <h2 id="reports-title">My reports</h2>
          <p class="muted small">
            Your immunization record and your annual health examination report — for school, work,
            sports, travel or camp. The office prepares them from your record and a doctor signs
            them; you get them by e-mail and here.
          </p>
        </div>
      </header>

      @if (!account.signedIn()) {
        <section class="card intake-card" id="reports-signed-out">
          <h3>Sign in to ask for a report</h3>
          <p class="small muted">Reports are part of your patient account.</p>
          <div class="dialog-actions">
            <span class="spacer"></span>
            <button
              class="btn"
              type="button"
              id="btn-reports-register"
              (click)="shell.openAccount('register')"
            >
              Register
            </button>
            <button
              class="btn primary"
              type="button"
              id="btn-reports-login"
              (click)="shell.openAccount('login')"
            >
              Log in
            </button>
          </div>
        </section>
      } @else if (loading()) {
        <p class="empty" id="reports-loading">Loading your reports…</p>
      } @else if (error(); as message) {
        <div class="callout error" id="reports-error" role="alert">
          <strong>Could not load your reports</strong>
          <span>{{ message }}</span>
          <button class="btn" type="button" (click)="load()">Try again</button>
        </div>
      } @else {
        <section class="card intake-card" id="reports-list">
          <h3>Your reports</h3>
          @if (reports().length) {
            <ul class="appt-list">
              @for (r of reports(); track r.id) {
                <li class="appt report-row" [id]="'report-' + r.id" [attr.data-status]="r.status">
                  <div class="appt-main">
                    <span class="appt-when"
                      >{{ r.kind_label }}
                      @if (r.status === 'signed' && !r.patient_viewed_at) {
                        <span class="tag new-summary">new</span>
                      }
                      <span class="tag" [class.confirmed]="r.status === 'signed'">{{
                        r.status_label
                      }}</span>
                    </span>
                    <span class="appt-who">{{ r.patient_name }} · {{ r.purpose_label }}</span>
                    <span class="appt-meta muted small"
                      >{{ r.number }}{{ r.version > 1 ? ' (amended)' : '' }} · asked
                      {{ day(r.requested_at)
                      }}{{ r.needed_by ? ' · needed by ' + date(r.needed_by) : '' }}
                      @if (r.signed_at) {
                        · signed by {{ r.doctor_name || r.signer_name }} on {{ day(r.signed_at) }}
                      }
                      @if (r.status === 'canceled' && r.cancel_reason) {
                        · {{ r.cancel_reason }}
                      }
                    </span>
                  </div>
                  @if (r.status === 'signed') {
                    <button
                      class="btn"
                      type="button"
                      [id]="'btn-report-view-' + r.id"
                      [attr.aria-expanded]="open() === r.id"
                      (click)="toggle(r)"
                      appTip="Read the signed report."
                    >
                      {{ open() === r.id ? 'Hide' : 'View' }}
                    </button>
                    <button
                      class="btn"
                      type="button"
                      [id]="'btn-report-pdf-' + r.id"
                      [disabled]="busy()"
                      (click)="download(r)"
                      appTip="The signed report as a PDF — print it or send it on."
                    >
                      PDF
                    </button>
                  } @else if (isOpen(r)) {
                    <button
                      class="btn danger"
                      type="button"
                      [id]="'btn-report-cancel-' + r.id"
                      [disabled]="busy()"
                      (click)="withdraw(r)"
                      appTip="You no longer need it — the office stops preparing it."
                    >
                      Withdraw
                    </button>
                  }
                </li>
                @if (open() === r.id && detail(); as d) {
                  <li class="appt-summary">
                    <section class="visit-summary report-view" [id]="'report-view-' + r.id">
                      <header class="vs-head">
                        <div>
                          <h4>{{ d.kind_label }}</h4>
                          <p class="small muted">
                            {{ d.patient_name }} · born
                            {{ d.content ? date(d.content.patient_date_of_birth) : '' }} ·
                            {{ d.patient_mrn }} · for {{ d.purpose_label }}
                          </p>
                        </div>
                      </header>
                      @if (d.content; as c) {
                        @if (d.kind === 'immunization') {
                          @if (c.immunizations.length) {
                            <table class="vs-table">
                              <thead>
                                <tr>
                                  <th>Vaccine</th>
                                  <th>Date</th>
                                  <th>Dose</th>
                                  <th>Given by</th>
                                </tr>
                              </thead>
                              <tbody>
                                @for (s of c.immunizations; track $index) {
                                  <tr>
                                    <td>{{ s.vaccine }}</td>
                                    <td class="nowrap">{{ date(s.date_given) }}</td>
                                    <td>{{ s.dose || '—' }}</td>
                                    <td>{{ s.given_by || '—' }}</td>
                                  </tr>
                                }
                              </tbody>
                            </table>
                          } @else {
                            <p class="muted">No immunizations on record.</p>
                          }
                          @if (d.findings.contraindications) {
                            <div class="vs-part">
                              <h5>Contraindications / exemptions</h5>
                              <p class="vs-text">{{ d.findings.contraindications }}</p>
                            </div>
                          }
                          @if (d.findings.vaccines_due) {
                            <div class="vs-part vs-instructions">
                              <h5>Vaccines due or recommended</h5>
                              <p class="vs-text">{{ d.findings.vaccines_due }}</p>
                            </div>
                          }
                        } @else {
                          <div class="vs-part">
                            <h5>Examination</h5>
                            <p>
                              {{ c.exam_date || 'Documented by the doctor' }}
                              {{ c.exam_provider ? '· ' + c.exam_provider : '' }}
                            </p>
                          </div>
                          @if (measurements(d); as m) {
                            <div class="vs-part">
                              <h5>Measurements</h5>
                              <p>{{ m }}</p>
                            </div>
                          }
                          <div class="vs-part">
                            <h5>Findings</h5>
                            <ul>
                              @for (s of examined(d); track s.label) {
                                <li>
                                  {{ s.label }}: {{ s.status }}{{ s.note ? ' — ' + s.note : '' }}
                                </li>
                              }
                            </ul>
                          </div>
                          <div class="vs-part vs-instructions">
                            <h5>Assessment</h5>
                            <p id="report-fitness">
                              <strong>{{ fitness(d) }}</strong>
                            </p>
                            @if (d.findings.restrictions) {
                              <p class="vs-text">Restrictions: {{ d.findings.restrictions }}</p>
                            }
                          </div>
                          @if (d.findings.recommendations) {
                            <div class="vs-part">
                              <h5>Recommendations</h5>
                              <p class="vs-text">{{ d.findings.recommendations }}</p>
                            </div>
                          }
                          @if (d.findings.next_exam_due) {
                            <div class="vs-part">
                              <h5>Next examination due</h5>
                              <p>{{ date(d.findings.next_exam_due) }}</p>
                            </div>
                          }
                        }
                        @if (d.doctor_note) {
                          <div class="vs-part">
                            <h5>Comments</h5>
                            <p class="vs-text">{{ d.doctor_note }}</p>
                          </div>
                        }
                      }
                      <div class="vs-signature">
                        @if (d.signature_png) {
                          <img [src]="d.signature_png" [alt]="'Signature of ' + d.signer_name" />
                        }
                        <p class="small">
                          <strong
                            >Electronically signed by {{ d.doctor_name || d.signer_name }}</strong
                          >
                          @if (d.signed_at) {
                            on {{ day(d.signed_at) }}
                          }
                        </p>
                        <p class="small muted">Verification code {{ d.verification }}</p>
                      </div>
                    </section>
                  </li>
                }
              }
            </ul>
          } @else {
            <p class="empty" id="reports-empty">No reports yet.</p>
          }
        </section>

        <section class="card intake-card" id="reports-request">
          <h3>Ask for a report</h3>
          @if (!records().length) {
            <p class="small muted" id="reports-no-record">
              Your account has no patient record yet — register (or attach your record on My
              appointments) first.
            </p>
          } @else {
            <form (ngSubmit)="request()" #form="ngForm">
              @if (records().length > 1) {
                <div class="field">
                  <label for="report-patient">For</label>
                  <select
                    class="input"
                    id="report-patient"
                    name="patient"
                    [(ngModel)]="draft.patient_id"
                  >
                    @for (p of records(); track p.id) {
                      <option [ngValue]="p.id">
                        {{ p.first_name }} {{ p.last_name }} ({{ p.mrn }})
                      </option>
                    }
                  </select>
                </div>
              }
              <div class="field">
                <span class="label">Which report</span>
                @for (k of kinds; track k.id) {
                  <label class="checkbox" [for]="'report-kind-' + k.id" [appTip]="k.tip">
                    <input
                      type="radio"
                      name="kind"
                      [id]="'report-kind-' + k.id"
                      [value]="k.id"
                      [(ngModel)]="draft.kind"
                    />
                    <span>{{ k.label }}</span>
                  </label>
                }
              </div>
              <div class="field-row">
                <div class="field">
                  <label for="report-purpose">What for</label>
                  <select
                    class="input"
                    id="report-purpose"
                    name="purpose"
                    [(ngModel)]="draft.purpose"
                  >
                    @for (p of purposes; track p.id) {
                      <option [value]="p.id">{{ p.label }}</option>
                    }
                  </select>
                </div>
                <div class="field">
                  <label for="report-detail"
                    >School / employer / club <span class="muted">(optional)</span></label
                  >
                  <input
                    class="input"
                    id="report-detail"
                    name="detail"
                    maxlength="200"
                    [(ngModel)]="draft.purpose_detail"
                  />
                </div>
              </div>
              <div class="field-row">
                <div class="field">
                  <label for="report-needed">Needed by <span class="muted">(optional)</span></label>
                  <input
                    class="input"
                    id="report-needed"
                    name="needed"
                    type="date"
                    [min]="today"
                    [(ngModel)]="draft.needed_by"
                  />
                </div>
                <div class="field">
                  <label for="report-note"
                    >Note for the office <span class="muted">(optional)</span></label
                  >
                  <input
                    class="input"
                    id="report-note"
                    name="note"
                    maxlength="1000"
                    [(ngModel)]="draft.note"
                  />
                </div>
              </div>
              @if (requestError(); as message) {
                <div class="callout error" id="report-request-error" role="alert">
                  <strong>Could not send the request</strong>
                  <span>{{ message }}</span>
                </div>
              }
              <div class="dialog-actions">
                <span class="spacer"></span>
                <button
                  class="btn primary"
                  type="submit"
                  id="btn-report-request"
                  [disabled]="busy()"
                  appTip="The office prepares it and a doctor signs it — you get an e-mail when it is ready."
                >
                  {{ busy() ? 'Sending…' : 'Ask for the report' }}
                </button>
              </div>
            </form>
          }
        </section>
      }
    </section>
  `,
})
export class ReportsPage {
  readonly account = inject(AccountService);
  readonly shell = inject(ShellService);
  private readonly reportsApi = inject(ReportsService);
  private readonly appointments = inject(AppointmentsService);
  private readonly toast = inject(ToastService);

  readonly kinds = REPORT_KINDS;
  readonly purposes = REPORT_PURPOSES;
  readonly isOpen = isOpen;
  readonly today = new Date().toISOString().slice(0, 10);

  readonly loading = signal(false);
  readonly busy = signal(false);
  readonly error = signal<string | null>(null);
  readonly requestError = signal<string | null>(null);
  readonly reports = signal<PatientReport[]>([]);
  readonly records = signal<MyPatientRecord[]>([]);
  readonly open = signal<number | null>(null);
  readonly detail = signal<PatientReport | null>(null);

  draft: {
    patient_id: number | null;
    kind: ReportKind;
    purpose: ReportPurpose;
    purpose_detail: string;
    needed_by: string;
    note: string;
  } = this.blank();

  constructor() {
    effect(() => {
      if (this.account.signedIn()) void this.load();
      else this.reports.set([]);
    });
  }

  private blank() {
    return {
      patient_id: this.records?.()[0]?.id ?? null,
      kind: 'immunization' as ReportKind,
      purpose: 'personal' as ReportPurpose,
      purpose_detail: '',
      needed_by: '',
      note: '',
    };
  }

  async load(): Promise<void> {
    this.loading.set(true);
    this.error.set(null);
    try {
      const [reports, mine] = await Promise.all([this.reportsApi.list(), this.appointments.mine()]);
      this.reports.set(reports);
      this.records.set(mine.patients);
      if (!this.draft.patient_id) this.draft.patient_id = mine.patients[0]?.id ?? null;
    } catch (e) {
      this.error.set(describe(e));
    } finally {
      this.loading.set(false);
    }
  }

  day(iso: string): string {
    return new Date(iso).toLocaleDateString(undefined, {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
    });
  }

  /** A date-only value ("2026-10-06") in the reader's format — parsed as a local date, never shifted by UTC. */
  date(value: string): string {
    const [y, m, d] = value.split('-').map(Number);
    return new Date(y, m - 1, d).toLocaleDateString(undefined, {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
    });
  }

  measurements(r: PatientReport): string {
    const f = r.findings;
    const c = r.content;
    return [
      f.height && `Height ${f.height}`,
      (f.weight || c?.weight) && `Weight ${f.weight || c?.weight}`,
      (f.blood_pressure || c?.blood_pressure) &&
        `Blood pressure ${f.blood_pressure || c?.blood_pressure}`,
      f.vision && `Vision ${f.vision}`,
      f.hearing && `Hearing ${f.hearing}`,
    ]
      .filter(Boolean)
      .join(' · ');
  }

  examined(r: PatientReport): { label: string; status: string; note: string | null }[] {
    return EXAM_SYSTEMS.map(([key, label]) => ({ label, finding: r.findings.systems?.[key] }))
      .filter((x) => x.finding && x.finding.status !== 'not_examined')
      .map((x) => ({ label: x.label, status: x.finding!.status, note: x.finding!.note }));
  }

  fitness(r: PatientReport): string {
    return r.findings.fitness ? FITNESS_LABELS[r.findings.fitness] : '—';
  }

  async toggle(r: PatientReport): Promise<void> {
    if (this.open() === r.id) {
      this.open.set(null);
      return;
    }
    try {
      const d = await this.reportsApi.get(r.id);
      this.detail.set(d);
      this.open.set(r.id);
      this.reports.update((all) =>
        all.map((x) => (x.id === r.id ? { ...x, patient_viewed_at: d.patient_viewed_at } : x)),
      );
    } catch (e) {
      this.toast.error(describe(e));
    }
  }

  async download(r: PatientReport): Promise<void> {
    if (this.busy()) return;
    this.busy.set(true);
    try {
      const blob = await this.reportsApi.pdf(r.id);
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${r.number}-v${r.version}.pdf`;
      document.body.append(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch (e) {
      this.toast.error(describe(e));
    } finally {
      this.busy.set(false);
    }
  }

  async request(): Promise<void> {
    if (this.busy() || !this.draft.patient_id) return;
    this.busy.set(true);
    this.requestError.set(null);
    try {
      const r = await this.reportsApi.request({
        patient_id: this.draft.patient_id,
        kind: this.draft.kind,
        purpose: this.draft.purpose,
        purpose_detail: this.draft.purpose_detail.trim() || null,
        needed_by: this.draft.needed_by || null,
        note: this.draft.note.trim() || null,
      });
      this.toast.show(
        `Asked for — ${r.kind_label} (${r.number}). We e-mail you when it is signed.`,
      );
      this.draft = this.blank();
      await this.load();
    } catch (e) {
      this.requestError.set(describe(e));
    } finally {
      this.busy.set(false);
    }
  }

  async withdraw(r: PatientReport): Promise<void> {
    if (this.busy() || !confirm(`Withdraw your request for the ${r.kind_label.toLowerCase()}?`))
      return;
    this.busy.set(true);
    try {
      await this.reportsApi.cancel(r.id);
      this.toast.show(`${r.number} withdrawn.`);
      await this.load();
    } catch (e) {
      this.toast.error(describe(e));
    } finally {
      this.busy.set(false);
    }
  }
}

function describe(e: unknown): string {
  if (e instanceof ApiError && e.status === 401) return 'Please sign in again.';
  if (e instanceof ApiError && e.offline)
    return "We can't reach the office right now. Please check your connection and try again.";
  return e instanceof Error ? e.message : String(e);
}
