import { Component, OnInit, computed, effect, inject, signal, untracked } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { ApiError } from '../../core/account/api.service';
import { AccountService } from '../../core/account/account.service';
import { AppointmentsService } from '../../core/appointments/appointments.service';
import type { CardPhoto } from '../../core/patients/card-photo';
import {
  INSURANCE_RELATIONSHIPS,
  type InsuranceRelationship,
  type PatientForm,
  type PatientIdentity,
  type PatientRecord,
  type PatientRegistration,
  type PatientSex,
  type PatientSummary,
  type PatientUpdate,
  type PatientUpdateChallenge,
  PatientsService,
  describeDestinations,
  describeInsurance,
  describeMatch,
} from '../../core/patients/patients.service';
import { InsuranceVerificationView } from './insurance-verification-view';
import { TipDirective } from '../../core/ui/tip.directive';
import { ToastService } from '../../core/ui/toast.service';
import { InsuranceCardCapture } from './insurance-card-capture';

/** `mine` = a signed-in patient's overview of their saved records (the default when signed in). */
type Step = 'mine' | 'identity' | 'verify' | 'details' | 'done';

/** What the page ends with: a registration (new / existing) or a saved update. */
type Outcome = PatientRegistration | { status: 'updated'; matched_on: []; patient: PatientRecord };

/** How the patient proved the record is theirs for an update. */
type EditSession = { kind: 'token'; token: string } | { kind: 'account'; id: number };

/** The editable form, as typed (strings; the request is built from it in `payload()`). */
interface Draft {
  first_name: string;
  last_name: string;
  date_of_birth: string;
  email: string;
  phone: string;
  sex: PatientSex | '';
  address_line: string;
  city: string;
  state: string;
  postal_code: string;
  country: string;
  insurance_provider: string;
  insurance_plan_name: string;
  insurance_member_id: string;
  insurance_group_number: string;
  insurance_policyholder_relationship: InsuranceRelationship;
  insurance_policyholder_name: string;
  emergency_contact_name: string;
  emergency_contact_phone: string;
  reason_for_visit: string;
  consent: boolean;
}

function emptyDraft(): Draft {
  return {
    first_name: '',
    last_name: '',
    date_of_birth: '',
    email: '',
    phone: '',
    sex: '',
    address_line: '',
    city: '',
    state: '',
    postal_code: '',
    country: '',
    insurance_provider: '',
    insurance_plan_name: '',
    insurance_member_id: '',
    insurance_group_number: '',
    insurance_policyholder_relationship: 'self',
    insurance_policyholder_name: '',
    emergency_contact_name: '',
    emergency_contact_phone: '',
    reason_for_visit: '',
    consent: false,
  };
}

const orNull = (v: string): string | null => (v.trim() ? v.trim() : null);

/**
 * Patient registration (/patients): the public intake page.
 *
 *   1. Who you are    — name, date of birth, e-mail / phone → "are you new here?"
 *                       (POST /patients/check). A known patient gets "welcome back" and their
 *                       record number; nothing is asked twice.
 *   2. Your details   — address, **insurance** (company, plan, member id, group number, whose
 *                       name the policy is in, and photos of the card — uploaded or taken with
 *                       the camera), emergency contact, reason for the visit, consent
 *                       → POST /patients creates the record (the card photos travel in the
 *                       same request, so the record and its photos are stored together).
 *   3. Done           — the record number to quote at the front desk, and what was stored.
 *
 * No account is needed; everything is the patient's own input. Insurance is optional — a
 * self-pay patient, or one who would rather show the card at the desk, leaves it blank.
 *
 * **Updating.** A known patient ("welcome back") can change their details — name, contact,
 * address, insurance (details and card photos), emergency contact — after proving the record is
 * theirs: signed in with the patient account the record is linked to, or with a one-time code
 * sent to the e-mail / phone *on the record* (step "verify"). The same form is then shown in
 * edit mode (`editing`), filled from the record; saving goes to PUT /patients/update (by code)
 * or PUT /me/patients/{id} (signed in). The date of birth is never editable here.
 *
 * **Signed in** with a patient account, the tab opens on *My details* instead of the blank
 * form (`mine`): every record on the account with what is saved — contact details, address,
 * insurance and its latest coverage check, card photos, emergency contact — each with
 * "Update details" (the same edit form, no code needed). "Register someone else" leads to the
 * blank form; a record registered while signed in belongs to the account at once.
 */
@Component({
  selector: 'app-patient-intake',
  imports: [FormsModule, RouterLink, TipDirective, InsuranceCardCapture, InsuranceVerificationView],
  template: `
    <section class="page intake" aria-labelledby="intake-title">
      <header class="page-header">
        <div>
          <h2 id="intake-title">{{ step() === 'mine' ? 'My details' : 'Patient registration' }}</h2>
          <p class="muted small">
            @if (step() === 'mine') {
              What the office has on file for you — check it and update anything that changed.
            } @else {
              New to the office? Register here before your first visit. Already a patient? The same
              form tells you so and gives you your record number.
            }
          </p>
        </div>
        @if (step() !== 'mine') {
          <ol class="steps" aria-label="Progress">
            <li [class.current]="step() === 'identity'" [class.done]="step() !== 'identity'">
              <span class="step-no">1</span> Who you are
            </li>
            @if (step() === 'verify') {
              <li class="current"><span class="step-no">2</span> Confirm it is you</li>
            }
            <li
              [class.current]="step() === 'details'"
              [class.done]="step() === 'done'"
              [hidden]="step() === 'verify'"
            >
              <span class="step-no">2</span> Your details
            </li>
            <li [class.current]="step() === 'done'"><span class="step-no">3</span> Done</li>
          </ol>
        }
      </header>

      @if (step() === 'mine') {
        @if (mineLoading() && !mine()) {
          <p class="empty" id="intake-mine-loading">Loading your details…</p>
        } @else if (mineError(); as message) {
          <div class="callout error" id="intake-mine-error" role="alert">
            <strong>Could not load your details</strong>
            <span>{{ message }}</span>
            <button class="btn" type="button" (click)="showMine()">Try again</button>
          </div>
        } @else {
          @for (p of mine() ?? []; track p.id) {
            <section
              class="card intake-card result existing mine-record"
              [id]="'intake-mine-' + p.id"
              [attr.aria-labelledby]="'intake-mine-title-' + p.id"
            >
              <div class="card-header">
                <h3 [id]="'intake-mine-title-' + p.id">
                  {{ p.first_name }} {{ p.last_name }}
                  <span class="mrn">{{ p.mrn }}</span>
                </h3>
                <p class="small muted">
                  Born {{ p.date_of_birth }}{{ p.sex ? ' · ' + sexLabel(p.sex) : '' }} · last
                  updated {{ p.updated_at.slice(0, 10) }}
                </p>
              </div>
              <dl class="record">
                <div>
                  <dt>E-mail</dt>
                  <dd>{{ p.email || '—' }}</dd>
                </div>
                <div>
                  <dt>Phone</dt>
                  <dd>{{ p.phone || '—' }}</dd>
                </div>
                <div>
                  <dt>Address</dt>
                  <dd>{{ addressOf(p) || '—' }}</dd>
                </div>
                <div>
                  <dt>Insurance</dt>
                  <dd>{{ describeInsurance(p) || 'None on file (self-pay)' }}</dd>
                </div>
                <div>
                  <dt>Insurance card photos</dt>
                  <dd>{{ p.insurance_cards.length ? cardsSummary(p) : '—' }}</dd>
                </div>
                <div>
                  <dt>Emergency contact</dt>
                  <dd>
                    {{
                      p.emergency_contact_name
                        ? p.emergency_contact_name +
                          (p.emergency_contact_phone ? ' · ' + p.emergency_contact_phone : '')
                        : '—'
                    }}
                  </dd>
                </div>
                @if (p.reason_for_visit) {
                  <div>
                    <dt>Reason for the visit</dt>
                    <dd>{{ p.reason_for_visit }}</dd>
                  </div>
                }
              </dl>
              @if (p.insurance_verification; as v) {
                <app-insurance-verification-view [verification]="v" audience="patient" />
              }
              <div class="dialog-actions">
                <button
                  class="btn primary"
                  type="button"
                  [id]="'btn-intake-mine-edit-' + p.id"
                  [disabled]="busy()"
                  (click)="editRecord(p)"
                  appTip="Change the contact details, address, insurance (details and card photos) or emergency contact on this record."
                >
                  Update details
                </button>
                <span class="spacer"></span>
                <a
                  class="btn"
                  [id]="'btn-intake-mine-book-' + p.id"
                  routerLink="/patients/appointments"
                  (click)="handOverRecord(p)"
                  appTip="Book an appointment for this record — the details are filled in for you."
                  >Book an appointment →</a
                >
              </div>
            </section>
          } @empty {
            <section class="card intake-card" id="intake-mine-empty">
              <h3>No record on this account yet</h3>
              <p class="small muted">
                Records registered with your e-mail address appear here automatically. Registered
                with another address, or years ago? Attach the record by name and date of birth on
                <a routerLink="/patients/mine">My appointments</a>, or register below.
              </p>
            </section>
          }
          <div class="dialog-actions mine-actions">
            <button
              class="btn"
              type="button"
              id="btn-intake-mine-refresh"
              [disabled]="mineLoading()"
              (click)="showMine()"
              appTip="Reload what is on file."
            >
              {{ mineLoading() ? 'Refreshing…' : 'Refresh' }}
            </button>
            <span class="spacer"></span>
            <button
              class="btn primary"
              type="button"
              id="btn-intake-mine-register"
              (click)="registerAnother()"
              appTip="Register another person — a child, a partner — with a blank form. The new record is added to your account."
            >
              Register someone else
            </button>
          </div>
        }
      } @else if (step() === 'identity') {
        <form class="card intake-card" id="intake-identity" (ngSubmit)="check()" novalidate>
          <h3>Who you are</h3>
          <p class="small muted">
            We use your name and date of birth to see whether you already have a record here.
          </p>
          <div class="field-row">
            <div class="field">
              <label for="intake-first-name">First name</label>
              <input
                class="input"
                id="intake-first-name"
                name="first_name"
                autocomplete="given-name"
                required
                maxlength="100"
                [(ngModel)]="draft.first_name"
              />
            </div>
            <div class="field">
              <label for="intake-last-name">Last name</label>
              <input
                class="input"
                id="intake-last-name"
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
              <label for="intake-dob">Date of birth</label>
              <input
                class="input"
                id="intake-dob"
                name="date_of_birth"
                type="date"
                autocomplete="bday"
                required
                [max]="today"
                [(ngModel)]="draft.date_of_birth"
              />
            </div>
            <div class="field">
              <label for="intake-phone">Mobile phone <span class="muted">(optional)</span></label>
              <input
                class="input"
                id="intake-phone"
                name="phone"
                type="tel"
                autocomplete="tel"
                maxlength="32"
                placeholder="+1 555 010 0123"
                [(ngModel)]="draft.phone"
              />
            </div>
          </div>
          <div class="field">
            <label for="intake-email">E-mail <span class="muted">(optional)</span></label>
            <input
              class="input"
              id="intake-email"
              name="email"
              type="email"
              autocomplete="email"
              maxlength="320"
              [(ngModel)]="draft.email"
            />
            <p class="hint small muted">
              Appointment confirmations and reminders go to your e-mail and phone.
            </p>
          </div>

          @if (error(); as message) {
            <div class="callout error" id="intake-error" role="alert">
              <strong>Please check the form</strong>
              <span>{{ message }}</span>
            </div>
          }

          <div class="dialog-actions">
            @if (account.signedIn()) {
              <button
                class="btn"
                type="button"
                id="btn-intake-to-mine"
                (click)="showMine()"
                appTip="Back to the records on your account."
              >
                ← My details
              </button>
            } @else {
              <a
                class="btn"
                routerLink="/patients/appointments"
                appTip="Already registered and want to book? Go straight to the appointments page."
                >I just want to book</a
              >
            }
            <span class="spacer"></span>
            <button
              class="btn primary"
              type="submit"
              id="btn-intake-check"
              [disabled]="busy()"
              appTip="Check whether you already have a record with this office. New patients continue to the next step."
            >
              {{ busy() ? 'Checking…' : 'Continue' }}
            </button>
          </div>
        </form>
      } @else if (step() === 'verify' && challenge(); as ch) {
        <form class="card intake-card" id="intake-verify" (ngSubmit)="confirmCode()" novalidate>
          <h3>Confirm it is you</h3>
          <p class="small muted" id="intake-verify-note">
            To protect your record we sent a 6-digit code by
            {{ describeDestinations(ch.sent_to) }} — the contact details on your record, not what
            you typed. Enter it below to update your details.
          </p>
          @if (ch.fake) {
            <div class="callout link-hint" id="intake-verify-fake">
              <span class="fake-note small"
                >Built-in fake messaging — nothing left this machine.
                @if (ch.outbox_url) {
                  Read the code in the
                  <a [href]="appointments.apiUrl(ch.outbox_url)" target="_blank" rel="noopener"
                    >test inbox</a
                  >.
                }
              </span>
            </div>
          }
          <div class="field">
            <label for="intake-code">Code</label>
            <input
              class="input code-input"
              id="intake-code"
              name="code"
              inputmode="numeric"
              autocomplete="one-time-code"
              maxlength="12"
              placeholder="6-digit code"
              [(ngModel)]="code"
            />
          </div>
          @if (error(); as message) {
            <div class="callout error" id="intake-error" role="alert">
              <strong>That did not work</strong>
              <span>{{ message }}</span>
            </div>
          }
          <div class="dialog-actions">
            <button
              class="btn"
              type="button"
              id="btn-intake-verify-back"
              [disabled]="busy()"
              (click)="cancelEdit()"
              appTip="Never mind — back to your record number."
            >
              ← Back
            </button>
            <button
              class="btn"
              type="button"
              id="btn-intake-resend"
              [disabled]="busy()"
              (click)="startEdit()"
              appTip="Send a new code to the contact details on your record."
            >
              Send again
            </button>
            <span class="spacer"></span>
            <button
              class="btn primary"
              type="submit"
              id="btn-intake-confirm-code"
              [disabled]="busy() || code.trim().length < 4"
              appTip="Check the code and open your details for editing."
            >
              {{ busy() ? 'Checking…' : 'Continue' }}
            </button>
          </div>
        </form>
      } @else if (step() === 'details') {
        <form class="card intake-card" id="intake-details" (ngSubmit)="submitDetails()" novalidate>
          <h3>{{ editing() ? 'Update your details' : 'Your details' }}</h3>
          @if (editing()) {
            <p class="small muted" id="intake-edit-note">
              Change what is out of date and press <em>Save changes</em>. Your date of birth ({{
                editingRecord()?.date_of_birth
              }}) can only be corrected at the front desk.
            </p>
            <h4 class="section-title">Name and contact</h4>
            <div class="field-row">
              <div class="field">
                <label for="intake-edit-first-name">First name</label>
                <input
                  class="input"
                  id="intake-edit-first-name"
                  name="first_name"
                  autocomplete="given-name"
                  required
                  maxlength="100"
                  [(ngModel)]="draft.first_name"
                />
              </div>
              <div class="field">
                <label for="intake-edit-last-name">Last name</label>
                <input
                  class="input"
                  id="intake-edit-last-name"
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
                <label for="intake-edit-email">E-mail</label>
                <input
                  class="input"
                  id="intake-edit-email"
                  name="email"
                  type="email"
                  autocomplete="email"
                  maxlength="320"
                  [(ngModel)]="draft.email"
                />
              </div>
              <div class="field">
                <label for="intake-edit-phone">Mobile phone</label>
                <input
                  class="input"
                  id="intake-edit-phone"
                  name="phone"
                  type="tel"
                  autocomplete="tel"
                  maxlength="32"
                  [(ngModel)]="draft.phone"
                />
              </div>
            </div>
          } @else {
            <p class="small muted" id="intake-new-note">
              You are new to the office, {{ draft.first_name.trim() }}. A few more details and you
              are registered. Everything below except the consent is optional.
            </p>
          }

          <h4 class="section-title">About you</h4>
          <div class="field-row">
            <div class="field">
              <label for="intake-sex">Sex</label>
              <select class="input" id="intake-sex" name="sex" [(ngModel)]="draft.sex">
                <option value="">Prefer not to say</option>
                <option value="female">Female</option>
                <option value="male">Male</option>
                <option value="other">Other</option>
                <option value="unspecified">Unspecified</option>
              </select>
            </div>
          </div>

          <h4 class="section-title">Address</h4>
          <div class="field">
            <label for="intake-address">Street address</label>
            <input
              class="input"
              id="intake-address"
              name="address_line"
              autocomplete="street-address"
              maxlength="200"
              [(ngModel)]="draft.address_line"
            />
          </div>
          <div class="field-row three">
            <div class="field">
              <label for="intake-city">City</label>
              <input
                class="input"
                id="intake-city"
                name="city"
                autocomplete="address-level2"
                maxlength="100"
                [(ngModel)]="draft.city"
              />
            </div>
            <div class="field">
              <label for="intake-state">State / province</label>
              <input
                class="input"
                id="intake-state"
                name="state"
                autocomplete="address-level1"
                maxlength="100"
                [(ngModel)]="draft.state"
              />
            </div>
            <div class="field">
              <label for="intake-postal">Postal code</label>
              <input
                class="input"
                id="intake-postal"
                name="postal_code"
                autocomplete="postal-code"
                maxlength="20"
                [(ngModel)]="draft.postal_code"
              />
            </div>
          </div>
          <div class="field">
            <label for="intake-country">Country</label>
            <input
              class="input"
              id="intake-country"
              name="country"
              autocomplete="country-name"
              maxlength="100"
              [(ngModel)]="draft.country"
            />
          </div>

          <h4 class="section-title" id="intake-insurance-title">Insurance</h4>
          <section class="insurance" aria-labelledby="intake-insurance-title" id="intake-insurance">
            <p class="small muted">
              Copy the details from your insurance card, and add a photo of each side — take it with
              your camera or upload one. The office checks your cover before the visit, so you spend
              less time at the front desk. No insurance, or rather show the card in person? Leave
              this section blank.
            </p>
            <div class="field-row">
              <div class="field">
                <label for="intake-ins-provider">Insurance company</label>
                <input
                  class="input"
                  id="intake-ins-provider"
                  name="insurance_provider"
                  maxlength="120"
                  placeholder="e.g. Example Health"
                  [(ngModel)]="draft.insurance_provider"
                />
              </div>
              <div class="field">
                <label for="intake-ins-plan">Plan name / type</label>
                <input
                  class="input"
                  id="intake-ins-plan"
                  name="insurance_plan_name"
                  maxlength="120"
                  placeholder="e.g. PPO Silver"
                  [(ngModel)]="draft.insurance_plan_name"
                />
              </div>
            </div>
            <div class="field-row">
              <div class="field">
                <label for="intake-ins-member">Member ID</label>
                <input
                  class="input"
                  id="intake-ins-member"
                  name="insurance_member_id"
                  maxlength="64"
                  autocomplete="off"
                  [(ngModel)]="draft.insurance_member_id"
                />
                <p class="hint small muted">Also called subscriber or policy number.</p>
              </div>
              <div class="field">
                <label for="intake-ins-group">Group number</label>
                <input
                  class="input"
                  id="intake-ins-group"
                  name="insurance_group_number"
                  maxlength="64"
                  autocomplete="off"
                  [(ngModel)]="draft.insurance_group_number"
                />
              </div>
            </div>
            <div class="field-row">
              <div class="field">
                <label for="intake-ins-relationship">Whose name is the policy in?</label>
                <select
                  class="input"
                  id="intake-ins-relationship"
                  name="insurance_policyholder_relationship"
                  [(ngModel)]="draft.insurance_policyholder_relationship"
                >
                  @for (r of relationships; track r.value) {
                    <option [value]="r.value">{{ r.label }}</option>
                  }
                </select>
              </div>
              @if (draft.insurance_policyholder_relationship !== 'self') {
                <div class="field">
                  <label for="intake-ins-holder">Policyholder's name</label>
                  <input
                    class="input"
                    id="intake-ins-holder"
                    name="insurance_policyholder_name"
                    maxlength="120"
                    autocomplete="off"
                    [(ngModel)]="draft.insurance_policyholder_name"
                  />
                </div>
              }
            </div>

            <div class="card-photos" id="intake-card-photos">
              <app-insurance-card-capture
                side="front"
                [(photo)]="cardFront"
                [stored]="storedCard('front')"
                [(removed)]="frontRemoved"
                [disabled]="busy()"
              />
              <app-insurance-card-capture
                side="back"
                [(photo)]="cardBack"
                [stored]="storedCard('back')"
                [(removed)]="backRemoved"
                [disabled]="busy()"
              />
            </div>
            <p class="small muted">
              Photos are resized on your device before they are sent and are stored with your
              record, where only the office staff can see them.
            </p>
          </section>

          <h4 class="section-title">Emergency contact</h4>
          <div class="field-row">
            <div class="field">
              <label for="intake-ec-name">Name</label>
              <input
                class="input"
                id="intake-ec-name"
                name="emergency_contact_name"
                maxlength="120"
                [(ngModel)]="draft.emergency_contact_name"
              />
            </div>
            <div class="field">
              <label for="intake-ec-phone">Phone</label>
              <input
                class="input"
                id="intake-ec-phone"
                name="emergency_contact_phone"
                type="tel"
                maxlength="32"
                [(ngModel)]="draft.emergency_contact_phone"
              />
            </div>
          </div>

          <h4 class="section-title">Your visit</h4>
          <div class="field">
            <label for="intake-reason">Reason for your first visit</label>
            <textarea
              class="input"
              id="intake-reason"
              name="reason_for_visit"
              maxlength="2000"
              rows="3"
              [(ngModel)]="draft.reason_for_visit"
            ></textarea>
          </div>

          @if (!editing()) {
            <label class="checkbox consent" for="intake-consent">
              <input
                type="checkbox"
                id="intake-consent"
                name="consent"
                [(ngModel)]="draft.consent"
                required
              />
              <span>
                I confirm these details are mine and agree that the office stores them — including
                the insurance card photos — to manage my care and billing.
              </span>
            </label>
          }

          @if (error(); as message) {
            <div class="callout error" id="intake-error" role="alert">
              <strong>{{
                editing() ? 'We could not save that' : 'We could not register you'
              }}</strong>
              <span>{{ message }}</span>
            </div>
          }

          <div class="dialog-actions">
            @if (editing()) {
              <button
                class="btn"
                type="button"
                id="btn-intake-cancel-edit"
                [disabled]="busy()"
                (click)="cancelEdit()"
                appTip="Leave everything as it is."
              >
                Cancel
              </button>
              <span class="spacer"></span>
              <button
                class="btn primary"
                type="submit"
                id="btn-intake-save"
                [disabled]="busy()"
                appTip="Save the changes to your patient record. If the insurance changed, the coverage is checked again."
              >
                {{ busy() ? 'Saving…' : 'Save changes' }}
              </button>
            } @else {
              <button
                class="btn"
                type="button"
                id="btn-intake-back"
                [disabled]="busy()"
                (click)="back()"
                appTip="Go back to your name and date of birth. What you typed here is kept."
              >
                ← Back
              </button>
              <span class="spacer"></span>
              <button
                class="btn primary"
                type="submit"
                id="btn-intake-register"
                [disabled]="busy()"
                appTip="Create your patient record with the details above. You get a record number to quote at the front desk."
              >
                {{ busy() ? 'Registering…' : 'Register' }}
              </button>
            }
          </div>
        </form>
      } @else if (result(); as r) {
        <section
          class="card intake-card result"
          [class.new]="r.status !== 'existing'"
          [class.existing]="r.status === 'existing'"
          id="intake-result"
          [attr.data-status]="r.status"
          aria-live="polite"
        >
          <div class="result-icon" aria-hidden="true">
            {{ r.status === 'existing' ? '👋' : '✓' }}
          </div>
          <h3 id="intake-result-heading">
            @switch (r.status) {
              @case ('new') {
                You are registered, {{ r.patient.first_name }}!
              }
              @case ('updated') {
                Your details are saved, {{ r.patient.first_name }}
              }
              @default {
                Welcome back, {{ r.patient.first_name }} — you are already registered
              }
            }
          </h3>
          <p>
            Your record number is
            <span class="mrn" id="intake-mrn">{{ r.patient.mrn }}</span>
            — please mention it at the front desk.
            @if (r.status === 'existing') {
              <span id="intake-matched"
                >We recognised you by {{ describeMatch(r.matched_on) }}. Nothing was stored twice.
                Moved, new phone, new insurance card? Use <em>Update my details</em> below.</span
              >
            }
          </p>
          @if (editError(); as message) {
            <div class="callout error" id="intake-edit-error" role="alert">
              <strong>We could not open your details</strong>
              <span>{{ message }}</span>
            </div>
          }
          <dl class="record" id="intake-record">
            <div>
              <dt>Name</dt>
              <dd>{{ r.patient.first_name }} {{ r.patient.last_name }}</dd>
            </div>
            <div>
              <dt>Date of birth</dt>
              <dd>{{ r.patient.date_of_birth }}</dd>
            </div>
            @if (record(); as p) {
              @if (p.email) {
                <div>
                  <dt>E-mail</dt>
                  <dd>{{ p.email }}</dd>
                </div>
              }
              @if (p.phone) {
                <div>
                  <dt>Phone</dt>
                  <dd>{{ p.phone }}</dd>
                </div>
              }
              @if (insuranceSummary()) {
                <div>
                  <dt>Insurance</dt>
                  <dd id="intake-insurance-summary">{{ insuranceSummary() }}</dd>
                </div>
              }
              @if (p.insurance_cards.length) {
                <div>
                  <dt>Insurance card photos</dt>
                  <dd id="intake-insurance-cards">{{ cardsSummary(p) }}</dd>
                </div>
              }
            }
          </dl>
          @if (record()?.insurance_verification; as v) {
            <app-insurance-verification-view [verification]="v" audience="patient" />
          }
          @if (r.status !== 'existing' && storedPhotos().length) {
            <div class="card-photos stored" aria-label="The insurance card photos you sent">
              @for (photo of storedPhotos(); track photo.side) {
                <figure class="card-thumb">
                  <img
                    [src]="photo.dataUrl"
                    [alt]="'Photo of the ' + photo.side + ' of your insurance card'"
                  />
                  <figcaption class="small muted">
                    {{ photo.side === 'front' ? 'Front' : 'Back' }}
                  </figcaption>
                </figure>
              }
            </div>
          }
          <div class="dialog-actions">
            @if (account.signedIn()) {
              <button
                class="btn"
                type="button"
                id="btn-intake-result-mine"
                (click)="showMine()"
                appTip="Back to all the records on your account."
              >
                ← My details
              </button>
            } @else {
              <button
                class="btn"
                type="button"
                id="btn-intake-again"
                (click)="reset()"
                appTip="Register another person (for example a child) with a blank form."
              >
                Register someone else
              </button>
            }
            <button
              class="btn"
              type="button"
              id="btn-intake-edit"
              [disabled]="busy()"
              (click)="startEdit()"
              [appTip]="
                account.signedIn()
                  ? 'Change your contact details, address, insurance or emergency contact.'
                  : 'Change your contact details, address, insurance or emergency contact. We first send a code to the e-mail / phone on your record to make sure it is you.'
              "
            >
              {{
                busy() ? 'One moment…' : r.status === 'updated' ? 'Edit again' : 'Update my details'
              }}
            </button>
            <span class="spacer"></span>
            <a
              class="btn primary"
              id="btn-intake-book"
              routerLink="/patients/appointments"
              (click)="handOver()"
              appTip="Book your first appointment with a doctor or nurse of this office — your details are filled in for you."
              >Book an appointment →</a
            >
          </div>
        </section>
      }
    </section>
  `,
})
export class PatientIntake implements OnInit {
  private readonly patients = inject(PatientsService);
  readonly appointments = inject(AppointmentsService);
  readonly account = inject(AccountService);
  private readonly toast = inject(ToastService);

  readonly step = signal<Step>('identity');
  readonly busy = signal(false);
  readonly error = signal<string | null>(null);
  readonly result = signal<Outcome | null>(null);

  // ---- a signed-in patient's saved records (step "mine")
  readonly mine = signal<PatientRecord[] | null>(null);
  readonly mineLoading = signal(false);
  readonly mineError = signal<string | null>(null);
  readonly describeInsurance = describeInsurance;

  // ---- updating a record
  /** The editing session, once the record was proven to be the patient's. */
  readonly editing = signal<EditSession | null>(null);
  /** The record being edited (the form starts from it; the stored card photos come from it). */
  readonly editingRecord = signal<PatientRecord | null>(null);
  readonly challenge = signal<PatientUpdateChallenge | null>(null);
  readonly editError = signal<string | null>(null);
  readonly frontRemoved = signal(false);
  readonly backRemoved = signal(false);
  readonly describeDestinations = describeDestinations;
  code = '';

  draft: Draft = emptyDraft();
  /** The card photos picked on step 2 (kept when going back and forth). */
  readonly cardFront = signal<CardPhoto | null>(null);
  readonly cardBack = signal<CardPhoto | null>(null);

  readonly relationships = INSURANCE_RELATIONSHIPS;
  readonly describeMatch = describeMatch;
  readonly today = new Date().toISOString().slice(0, 10);

  /** The full record — a *new* registration or a saved update returns one. */
  readonly record = computed<PatientRecord | null>(() => {
    const r = this.result();
    return r && r.status !== 'existing' ? r.patient : null;
  });

  constructor() {
    // Signed in → the tab opens on "My details" (unless the patient is in the middle of
    // something); signed out → back to the blank form, dropping what only the account may see.
    effect(() => {
      const signedIn = this.account.signedIn();
      untracked(() => {
        if (signedIn) {
          if (this.step() === 'identity' && this.isBlank()) void this.showMine();
        } else if (this.step() === 'mine' || this.editing()?.kind === 'account') {
          this.mine.set(null);
          this.reset();
        }
      });
    });
  }

  ngOnInit(): void {
    // "Update details" on the My appointments page: open the form for that record straight away.
    const wanted = this.patients.editRequest();
    if (wanted && this.account.signedIn()) {
      this.patients.editRequest.set(null);
      this.result.set({ status: 'existing', matched_on: [], patient: wanted });
      this.step.set('done');
      void this.startEdit();
    }
    // Otherwise the effect in the constructor opens "My details" for a signed-in patient.
  }

  /** Nothing typed yet on the identity form. */
  private isBlank(): boolean {
    const d = this.draft;
    return !d.first_name && !d.last_name && !d.date_of_birth && !d.email && !d.phone;
  }

  /** Signed in: load (or reload) every record on the account and show the overview. */
  async showMine(): Promise<void> {
    if (!this.account.signedIn()) return;
    this.step.set('mine');
    this.error.set(null);
    this.mineError.set(null);
    this.mineLoading.set(true);
    try {
      this.mine.set(await this.patients.myRecordsInFull());
    } catch (e) {
      this.mineError.set(this.describe(e));
    } finally {
      this.mineLoading.set(false);
    }
  }

  /** "Update details" on one of the account's records: straight into the edit form. */
  editRecord(record: PatientRecord): void {
    this.result.set({ status: 'existing', matched_on: [], patient: record });
    this.openEditor({ kind: 'account', id: record.id }, record);
  }

  /** "Register someone else" from the overview: the blank form ("← My details" leads back). */
  registerAnother(): void {
    this.reset();
    this.step.set('identity');
  }

  /** "Book an appointment →" for one of the account's records. */
  handOverRecord(p: PatientRecord): void {
    this.appointments.prefill.set({
      first_name: p.first_name,
      last_name: p.last_name,
      date_of_birth: p.date_of_birth,
      email: p.email,
      phone: p.phone,
    });
  }

  sexLabel(sex: string): string {
    return (
      { female: 'Female', male: 'Male', other: 'Other', unspecified: 'Unspecified' }[sex] ?? sex
    );
  }

  addressOf(p: PatientRecord): string {
    const line2 = [p.city, p.state, p.postal_code].filter(Boolean).join(' ');
    return [p.address_line, line2, p.country].filter(Boolean).join(', ');
  }

  storedCard(side: 'front' | 'back') {
    return this.editingRecord()?.insurance_cards.find((c) => c.side === side) ?? null;
  }

  submitDetails(): Promise<void> {
    return this.editing() ? this.save() : this.register();
  }
  readonly insuranceSummary = computed(() => {
    const p = this.record();
    return p ? describeInsurance(p) : '';
  });
  /** What was sent, for the thumbnails on the confirmation (the server only echoes metadata). */
  readonly storedPhotos = computed(() => {
    const out: { side: 'front' | 'back'; dataUrl: string }[] = [];
    const front = this.cardFront();
    const back = this.cardBack();
    if (front) out.push({ side: 'front', dataUrl: front.dataUrl });
    if (back) out.push({ side: 'back', dataUrl: back.dataUrl });
    return out;
  });

  identity(): PatientIdentity {
    return {
      first_name: this.draft.first_name.trim(),
      last_name: this.draft.last_name.trim(),
      date_of_birth: this.draft.date_of_birth,
      email: orNull(this.draft.email),
      phone: orNull(this.draft.phone),
    };
  }

  /** The request body of POST /patients, from the draft and the photos. */
  payload(): PatientForm {
    const d = this.draft;
    const relationship = d.insurance_policyholder_relationship;
    const hasInsurance = !!(
      d.insurance_provider.trim() ||
      d.insurance_plan_name.trim() ||
      d.insurance_member_id.trim() ||
      d.insurance_group_number.trim() ||
      this.cardFront() ||
      this.cardBack()
    );
    return {
      ...this.identity(),
      sex: d.sex || null,
      address_line: orNull(d.address_line),
      city: orNull(d.city),
      state: orNull(d.state),
      postal_code: orNull(d.postal_code),
      country: orNull(d.country),
      insurance_provider: orNull(d.insurance_provider),
      insurance_plan_name: orNull(d.insurance_plan_name),
      insurance_member_id: orNull(d.insurance_member_id),
      insurance_group_number: orNull(d.insurance_group_number),
      // "Myself" is only meaningful once there is an insurance to speak of.
      insurance_policyholder_relationship: hasInsurance ? relationship : null,
      insurance_policyholder_name:
        relationship === 'self' ? null : orNull(d.insurance_policyholder_name),
      insurance_card_front: this.cardFront()?.dataUrl ?? null,
      insurance_card_back: this.cardBack()?.dataUrl ?? null,
      emergency_contact_name: orNull(d.emergency_contact_name),
      emergency_contact_phone: orNull(d.emergency_contact_phone),
      reason_for_visit: orNull(d.reason_for_visit),
      consent: d.consent,
    };
  }

  private validateIdentity(): string | null {
    const id = this.identity();
    if (!id.first_name || !id.last_name) return 'Please enter your first and last name.';
    if (!/^\d{4}-\d{2}-\d{2}$/.test(id.date_of_birth)) return 'Please enter your date of birth.';
    if (id.date_of_birth > this.today) return 'The date of birth cannot be in the future.';
    if (id.email && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(id.email))
      return 'Please check the e-mail address.';
    if (id.phone) {
      const digits = id.phone.replace(/\D/g, '').length;
      if (digits < 7 || digits > 15) return 'Please check the phone number (7 to 15 digits).';
    }
    return null;
  }

  async check(): Promise<void> {
    if (this.busy()) return;
    const problem = this.validateIdentity();
    if (problem) {
      this.error.set(problem);
      return;
    }
    this.busy.set(true);
    this.error.set(null);
    try {
      const res = await this.patients.check(this.identity());
      if (res.is_new || !res.patient) {
        this.step.set('details');
      } else {
        this.result.set({ status: 'existing', matched_on: res.matched_on, patient: res.patient });
        this.step.set('done');
      }
    } catch (e) {
      this.error.set(this.describe(e));
    } finally {
      this.busy.set(false);
    }
  }

  back(): void {
    this.error.set(null);
    this.step.set('identity');
  }

  async register(): Promise<void> {
    if (this.busy()) return;
    const problem = this.validateIdentity();
    if (problem) {
      this.error.set(problem);
      return;
    }
    if (!this.draft.consent) {
      this.error.set('Please confirm that the office may store your details.');
      return;
    }
    if (
      this.draft.insurance_policyholder_relationship !== 'self' &&
      !this.draft.insurance_policyholder_name.trim() &&
      (this.draft.insurance_provider.trim() || this.draft.insurance_member_id.trim())
    ) {
      this.error.set("Please enter the policyholder's name, or choose “Myself”.");
      return;
    }
    this.busy.set(true);
    this.error.set(null);
    try {
      const res = await this.patients.register(this.payload());
      this.result.set(res);
      this.step.set('done');
      this.toast.show(
        res.status === 'new'
          ? `Registered — your record number is ${res.patient.mrn}.`
          : `You are already registered: ${res.patient.mrn}.`,
      );
    } catch (e) {
      this.error.set(this.describe(e));
    } finally {
      this.busy.set(false);
    }
  }

  reset(): void {
    this.draft = emptyDraft();
    this.cardFront.set(null);
    this.cardBack.set(null);
    this.result.set(null);
    this.error.set(null);
    this.clearEdit();
    this.step.set('identity');
  }

  // ---- updating a record

  /** "Update my details": signed in → the account's record; otherwise a code to the record's contact details. */
  async startEdit(): Promise<void> {
    const r = this.result();
    if (!r || this.busy()) return;
    const session = this.editing();
    const record = this.editingRecord();
    if (session && record && this.step() === 'done') {
      this.openEditor(session, record); // "Edit again": the session from before is still good
      return;
    }
    this.busy.set(true);
    this.editError.set(null);
    this.error.set(null);
    try {
      if (this.account.signedIn()) {
        const record = await this.loadMine(r.patient);
        this.openEditor({ kind: 'account', id: record.id }, record);
      } else {
        const ch = await this.patients.requestUpdate({
          first_name: r.patient.first_name,
          last_name: r.patient.last_name,
          date_of_birth: r.patient.date_of_birth,
          email: orNull(this.draft.email),
          phone: orNull(this.draft.phone),
        });
        this.challenge.set(ch);
        this.code = '';
        this.step.set('verify');
        this.toast.show(`Code sent by ${describeDestinations(ch.sent_to)}.`);
      }
    } catch (e) {
      if (this.step() === 'verify') this.error.set(this.describe(e));
      else this.editError.set(this.describe(e));
    } finally {
      this.busy.set(false);
    }
  }

  /** Signed in: the record in full — attaching it to the account first when it is not yet. */
  private async loadMine(p: PatientSummary): Promise<PatientRecord> {
    try {
      return await this.patients.myRecord(p.id);
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 404)) throw e;
      await this.appointments.linkRecord({
        first_name: p.first_name,
        last_name: p.last_name,
        date_of_birth: p.date_of_birth,
        email: orNull(this.draft.email),
        phone: orNull(this.draft.phone),
      });
      return await this.patients.myRecord(p.id);
    }
  }

  async confirmCode(): Promise<void> {
    const ch = this.challenge();
    if (!ch || this.busy()) return;
    this.busy.set(true);
    this.error.set(null);
    try {
      const session = await this.patients.confirmUpdate(ch.challenge, this.code.trim());
      this.openEditor({ kind: 'token', token: session.edit_token }, session.patient);
    } catch (e) {
      this.error.set(this.describe(e));
    } finally {
      this.busy.set(false);
    }
  }

  private openEditor(session: EditSession, record: PatientRecord): void {
    this.editing.set(session);
    this.editingRecord.set(record);
    this.draft = {
      ...emptyDraft(),
      first_name: record.first_name,
      last_name: record.last_name,
      date_of_birth: record.date_of_birth,
      email: record.email ?? '',
      phone: record.phone ?? '',
      sex: (record.sex as PatientSex | null) ?? '',
      address_line: record.address_line ?? '',
      city: record.city ?? '',
      state: record.state ?? '',
      postal_code: record.postal_code ?? '',
      country: record.country ?? '',
      insurance_provider: record.insurance_provider ?? '',
      insurance_plan_name: record.insurance_plan_name ?? '',
      insurance_member_id: record.insurance_member_id ?? '',
      insurance_group_number: record.insurance_group_number ?? '',
      insurance_policyholder_relationship:
        (record.insurance_policyholder_relationship as InsuranceRelationship | null) ?? 'self',
      insurance_policyholder_name: record.insurance_policyholder_name ?? '',
      emergency_contact_name: record.emergency_contact_name ?? '',
      emergency_contact_phone: record.emergency_contact_phone ?? '',
      reason_for_visit: record.reason_for_visit ?? '',
      consent: true,
    };
    this.cardFront.set(null);
    this.cardBack.set(null);
    this.frontRemoved.set(false);
    this.backRemoved.set(false);
    this.challenge.set(null);
    this.error.set(null);
    this.step.set('details');
  }

  /** The body of PUT /patients/update — the form in full plus the card decisions. */
  updatePayload(): PatientUpdate {
    const form = this.payload();
    return {
      first_name: form.first_name,
      last_name: form.last_name,
      email: form.email,
      phone: form.phone,
      sex: form.sex,
      address_line: form.address_line,
      city: form.city,
      state: form.state,
      postal_code: form.postal_code,
      country: form.country,
      insurance_provider: form.insurance_provider,
      insurance_plan_name: form.insurance_plan_name,
      insurance_member_id: form.insurance_member_id,
      insurance_group_number: form.insurance_group_number,
      insurance_policyholder_name: form.insurance_policyholder_name,
      insurance_policyholder_relationship: form.insurance_policyholder_relationship,
      insurance_card_front: form.insurance_card_front,
      insurance_card_back: form.insurance_card_back,
      remove_insurance_card_front: this.frontRemoved() && !this.cardFront(),
      remove_insurance_card_back: this.backRemoved() && !this.cardBack(),
      emergency_contact_name: form.emergency_contact_name,
      emergency_contact_phone: form.emergency_contact_phone,
      reason_for_visit: form.reason_for_visit,
    };
  }

  async save(): Promise<void> {
    const session = this.editing();
    if (!session || this.busy()) return;
    const problem = this.validateIdentity();
    if (problem) {
      this.error.set(problem);
      return;
    }
    if (
      this.draft.insurance_policyholder_relationship !== 'self' &&
      !this.draft.insurance_policyholder_name.trim() &&
      (this.draft.insurance_provider.trim() || this.draft.insurance_member_id.trim())
    ) {
      this.error.set("Please enter the policyholder's name, or choose “Myself”.");
      return;
    }
    this.busy.set(true);
    this.error.set(null);
    try {
      const body = this.updatePayload();
      const record =
        session.kind === 'token'
          ? await this.patients.update(session.token, body)
          : await this.patients.updateMine(session.id, body);
      this.result.set({ status: 'updated', matched_on: [], patient: record });
      this.editingRecord.set(record);
      this.toast.show('Your details are saved.');
      if (session.kind === 'account' && this.account.signedIn()) {
        this.clearEdit();
        await this.showMine(); // the overview, refreshed — with the new coverage check if any
      } else {
        this.step.set('done');
      }
    } catch (e) {
      this.error.set(this.describe(e));
      if (e instanceof ApiError && e.status === 401 && session.kind === 'token') this.clearEdit();
    } finally {
      this.busy.set(false);
    }
  }

  /** Back without saving: the overview for a signed-in patient, else the result card (the
   *  editing session is kept for "Edit again"). */
  cancelEdit(): void {
    this.error.set(null);
    this.challenge.set(null);
    if (this.editing()?.kind === 'account' && this.account.signedIn()) {
      this.clearEdit();
      void this.showMine();
      return;
    }
    this.step.set('done');
  }

  private clearEdit(): void {
    this.editing.set(null);
    this.editingRecord.set(null);
    this.challenge.set(null);
    this.editError.set(null);
    this.frontRemoved.set(false);
    this.backRemoved.set(false);
    this.code = '';
  }

  /** "Book an appointment →": the scheduler starts with this identity filled in. */
  handOver(): void {
    this.appointments.prefill.set(this.identity());
  }

  cardsSummary(p: PatientRecord): string {
    const sides = p.insurance_cards.map((c) => c.side);
    if (sides.includes('front') && sides.includes('back')) return 'Front and back on file';
    return sides.includes('front') ? 'Front on file' : 'Back on file';
  }

  private describe(e: unknown): string {
    if (e instanceof ApiError && e.status === 429)
      return e.message.includes('code')
        ? e.message
        : 'Too many attempts from this device. Please wait a few minutes and try again.';
    if (e instanceof ApiError && e.status === 413)
      return 'The insurance card photos are too large to send. Please retake them or remove one.';
    if (e instanceof ApiError && e.offline)
      return "We can't reach the office right now. Please check your connection and try again.";
    return e instanceof Error ? e.message : String(e);
  }
}
