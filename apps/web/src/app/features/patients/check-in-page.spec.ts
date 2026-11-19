import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { ApiError } from '../../core/account/api.service';
import type { Appointment } from '../../core/appointments/appointments.service';
import {
  type CheckInSession,
  type CheckInSubmission,
  CheckInService,
  missingAnswers,
} from '../../core/appointments/check-in.service';
import { SignaturePad } from '../../core/ui/signature-pad';
import { ToastService } from '../../core/ui/toast.service';
import { CheckInPage } from './check-in-page';

const PNG = 'data:image/png;base64,' + 'A'.repeat(200);

const APPOINTMENT = {
  id: 7,
  number: 'APT-000007',
  status: 'scheduled',
  start_at: '2027-06-07T09:30:00Z',
  end_at: '2027-06-07T10:00:00Z',
  start_local: 'Monday, June 7, 2027 · 09:30',
  timezone: 'UTC',
  reason: 'Sore throat',
  provider: {
    id: 1,
    staff_number: 'W-000001',
    first_name: 'Alex',
    last_name: 'Example',
    role: 'doctor',
    specialty: 'General practice',
    weekdays: [0, 1, 2, 3, 4],
  },
  patient: {
    id: 1,
    mrn: 'P-000001',
    first_name: 'Pat',
    last_name: 'Dummy',
    date_of_birth: '1985-03-14',
    created_at: '2027-01-01T00:00:00Z',
  },
  created_at: '2027-06-01T00:00:00Z',
  canceled_at: null,
  office_name: 'Medic Office',
  office_address: null,
  link: null,
  calendar: null,
  confirmed_at: null,
  confirmed_via: null,
  reminder_sent_at: null,
  reminder_channels: [],
  reminder_note: null,
  payment: 'insurance',
  coverage_status: 'active',
  self_pay_agreed_at: null,
} as Appointment;

function session(extra: Partial<CheckInSession> = {}): CheckInSession {
  return {
    id: 5,
    status: 'pending',
    via: 'account',
    started_at: '2027-06-07T08:00:00Z',
    expires_at: '2027-06-07T09:00:00Z',
    link_channels: [],
    link_sent_to: [],
    link_sent_at: null,
    link_delivery_note: null,
    failure_code: null,
    failure_reason: null,
    completed_at: null,
    estimate_cents: null,
    currency: 'USD',
    payment_id: null,
    appointment: APPOINTMENT,
    questionnaire: {
      version: '2027-10',
      title: 'Before your visit',
      intro: 'A few questions.',
      questions: [
        {
          id: 'reason',
          label: 'Main reason',
          kind: 'textarea',
          required: true,
          options: [],
          hint: null,
          max_length: 500,
        },
        {
          id: 'fever',
          label: 'Fever?',
          kind: 'yesno',
          required: true,
          options: [
            { value: 'yes', label: 'Yes' },
            { value: 'no', label: 'No' },
          ],
          hint: null,
          max_length: null,
        },
        {
          id: 'pain_level',
          label: 'Pain',
          kind: 'scale',
          required: true,
          options: [],
          hint: null,
          max_length: null,
        },
        {
          id: 'medications',
          label: 'Medications',
          kind: 'text',
          required: false,
          options: [],
          hint: 'Leave empty if none.',
          max_length: 300,
        },
        {
          id: 'consent_treatment',
          label: 'I consent',
          kind: 'consent',
          required: true,
          options: [],
          hint: null,
          max_length: null,
        },
      ],
    },
    estimate: {
      currency: 'USD',
      total_cents: 4000,
      lines: [{ label: 'Office visit copay', amount_cents: 4000 }],
      covered: true,
      coverage_status: 'active',
      coverage_message: 'Active PPO coverage · $40 copay',
      notes: ['This is an estimate.'],
      payment_method: 'Visa •••• 4242 · 12/2031',
      replaces_hold_cents: 4000,
    },
    answers: { reason: 'Sore throat' },
    answers_signer_name: null,
    answers_signed_at: null,
    financial_signer_name: null,
    financial_signed_at: null,
    payment: null,
    can_restart: false,
    ...extra,
  };
}

describe('CheckInPage (questionnaire + estimate, both signed)', () => {
  let current: CheckInSession;
  let result: CheckInSession;
  let submitted: CheckInSubmission[];
  let submitError: ApiError | null;
  let toasts: string[];

  beforeEach(() => {
    vi.useFakeTimers({ now: new Date('2027-06-07T08:20:00Z'), toFake: ['Date'] });
    current = session();
    result = session();
    submitted = [];
    submitError = null;
    toasts = [];
    TestBed.configureTestingModule({
      providers: [
        provideRouter([]),
        {
          provide: ToastService,
          useValue: {
            show: (m: string) => toasts.push(m),
            error: (m: string) => toasts.push('error: ' + m),
          },
        },
        {
          provide: CheckInService,
          useValue: {
            get: () => Promise.resolve(current),
            submit: (_t: string, body: CheckInSubmission) => {
              submitted.push(body);
              return submitError ? Promise.reject(submitError) : Promise.resolve(result);
            },
            restart: () => Promise.resolve({ token: 'dummy-next', url: 'x', check_in: null }),
          },
        },
      ],
    });
  });
  afterEach(() => vi.useRealTimers());

  async function mount() {
    const fixture = TestBed.createComponent(CheckInPage);
    fixture.componentRef.setInput('token', 'dummy-page');
    fixture.autoDetectChanges();
    for (let i = 0; i < 3; i++) {
      await new Promise((r) => setTimeout(r, 0));
      fixture.detectChanges();
    }
    return fixture;
  }
  const text = (el: Element, sel: string) => el.querySelector(sel)?.textContent?.trim() ?? '';
  const click = async (fixture: Awaited<ReturnType<typeof mount>>, sel: string) => {
    fixture.nativeElement.querySelector(sel).click();
    for (let i = 0; i < 3; i++) {
      await new Promise((r) => setTimeout(r, 0));
      fixture.detectChanges();
    }
  };

  function fill(fixture: Awaited<ReturnType<typeof mount>>) {
    const page = fixture.componentInstance;
    page.answers = { ...page.answers, fever: 'no', pain_level: 2, consent_treatment: true };
    page.answersSig.set(PNG);
    page.accept = true;
    page.financialSig.set(PNG);
  }

  it('shows the questionnaire, the estimate and where it is pre-authorized', async () => {
    const fixture = await mount();
    const el: HTMLElement = fixture.nativeElement;
    expect(text(el, '#checkin-deadline')).toContain('Please finish by 09:00 UTC (40 minutes left)');
    expect(el.querySelectorAll('.checkin-question')).toHaveLength(5);
    expect(el.querySelector<HTMLTextAreaElement>('#q-reason-input')).not.toBeNull();
    expect(el.querySelectorAll('#q-pain_level input[type=radio]')).toHaveLength(11);
    expect(el.querySelector('#q-fever-yes')).not.toBeNull();
    expect(el.querySelector('#q-consent_treatment-input')).not.toBeNull();
    expect(text(el, '#checkin-estimate-total')).toBe('$40.00');
    expect(text(el, '#checkin-estimate-method')).toContain('Visa •••• 4242');
    expect(text(el, '#checkin-estimate-method')).toContain('replaces the $40.00 hold');
    expect(el.querySelectorAll('app-signature-pad')).toHaveLength(2);
    // The names are prefilled with the patient's; the reason with the booking's.
    expect(fixture.componentInstance.answersName).toBe('Pat Dummy');
    expect(fixture.componentInstance.answers['reason']).toBe('Sore throat');
  });

  it('warns when there is no payment method to pre-authorize on', async () => {
    current = session({ estimate: { ...session().estimate!, payment_method: null } });
    const el: HTMLElement = (await mount()).nativeElement;
    expect(text(el, '#checkin-no-method')).toContain('without one the check-in fails');
  });

  it('asks for every answer and both signatures before sending', async () => {
    const fixture = await mount();
    const el: HTMLElement = fixture.nativeElement;
    await click(fixture, '#btn-checkin-submit');
    expect(text(el, '#checkin-form-error')).toContain('Please answer: Fever? · Pain · I consent');
    fixture.componentInstance.answers = {
      reason: 'x',
      fever: 'no',
      pain_level: 0,
      consent_treatment: true,
    };
    await click(fixture, '#btn-checkin-submit');
    expect(text(el, '#checkin-form-error')).toContain('sign your answers');
    fixture.componentInstance.answersSig.set(PNG);
    await click(fixture, '#btn-checkin-submit');
    expect(text(el, '#checkin-form-error')).toContain('responsible for the charges');
    fixture.componentInstance.accept = true;
    await click(fixture, '#btn-checkin-submit');
    expect(text(el, '#checkin-form-error')).toContain('sign the financial responsibility');
    expect(submitted).toHaveLength(0);
  });

  it('sends the signed page and shows "checked in" with the pre-authorization', async () => {
    result = session({
      status: 'completed',
      completed_at: '2027-06-07T08:21:00Z',
      answers_signer_name: 'Pat Dummy',
      financial_signer_name: 'Pat Dummy',
      staff_name: 'Dr. Alex Example',
      staff_notified_at: '2027-06-07T08:21:00Z',
      staff_notify_channels: ['email'],
      staff_notified_to: ['email:d•••@example.com'],
      payment: {
        id: 9,
        kind: 'check_in',
        status: 'authorized',
        amount_cents: 4000,
        refunded_cents: 0,
        currency: 'USD',
        description: 'Check-in pre-authorization · visit with Alex Example',
        message: '40.00 USD on hold.',
        method: { id: 1, kind: 'card', display: 'Visa •••• 4242 · 12/2031', removed: false },
        appointment: null,
        patient: null,
        reference: 'auth_fake_1',
        refund_reference: null,
        provider: 'fake',
        created_by: 'patient',
        created_at: '2027-06-07T08:21:00Z',
        captured_at: null,
        released_at: null,
        refunded_at: null,
      },
    });
    const fixture = await mount();
    fill(fixture);
    await click(fixture, '#btn-checkin-submit');
    expect(submitted).toHaveLength(1);
    expect(submitted[0]).toMatchObject({
      answers: { reason: 'Sore throat', fever: 'no', pain_level: 2, consent_treatment: true },
      answers_signature: { signer_name: 'Pat Dummy', image_png: PNG },
      estimate_cents: 4000,
      accept_financial_responsibility: true,
      financial_signature: { signer_name: 'Pat Dummy', image_png: PNG },
    });
    const el: HTMLElement = fixture.nativeElement;
    expect(text(el, '#checkin-done')).toContain('You are checked in');
    expect(text(el, '#checkin-done-payment')).toContain('$40.00 on Visa •••• 4242');
    expect(text(el, '#checkin-done-staff')).toBe(
      'Dr. Alex Example has been told that you checked in.',
    );
    expect(el.querySelector('#checkin-form')).toBeNull();
    expect(toasts.at(-1)).toContain('checked in');
  });

  it('shows why the check-in failed and starts again', async () => {
    result = session({
      status: 'failed',
      failure_code: 'payment_declined',
      failure_reason:
        'The pre-authorization of $40.00 on Visa •••• 4242 was declined: Insufficient funds.',
      can_restart: true,
    });
    const navigate = vi.spyOn(TestBed.inject(Router), 'navigate').mockResolvedValue(true);
    const fixture = await mount();
    fill(fixture);
    await click(fixture, '#btn-checkin-submit');
    const el: HTMLElement = fixture.nativeElement;
    expect(text(el, '#checkin-failure-reason')).toContain('was declined: Insufficient funds');
    await click(fixture, '#btn-checkin-restart');
    expect(navigate).toHaveBeenCalledWith(['/check-in', 'dummy-next']);
  });

  it('reloads when the estimate changed, and explains', async () => {
    submitError = new ApiError('The estimate changed to $150.00 since the page was opened.', 409);
    const fixture = await mount();
    fill(fixture);
    current = session({ estimate: { ...session().estimate!, total_cents: 15000 } });
    await click(fixture, '#btn-checkin-submit');
    const el: HTMLElement = fixture.nativeElement;
    expect(text(el, '#checkin-form-error')).toContain('estimate changed');
    expect(text(el, '#checkin-estimate-total')).toBe('$150.00');
  });

  it('a link the office sent, and pages that are over', async () => {
    current = session({ via: 'staff', link_sent_at: '2027-06-07T08:00:00Z' });
    let el: HTMLElement = (await mount()).nativeElement;
    expect(text(el, '#checkin-deadline')).toContain('the front desk sent you this page');

    current = session({ status: 'canceled', failure_reason: 'Replaced by a newer check-in.' });
    el = (await mount()).nativeElement;
    expect(text(el, '#checkin-canceled')).toContain('Replaced by a newer check-in.');
    expect(el.querySelector('#checkin-form')).toBeNull();
  });

  it('lists the unanswered required questions', () => {
    const q = session().questionnaire;
    expect(
      missingAnswers(q, { reason: ' ', fever: 'no', pain_level: 0, consent_treatment: false }),
    ).toEqual(['Main reason', 'I consent']);
    expect(
      missingAnswers(q, { reason: 'x', fever: 'yes', pain_level: 10, consent_treatment: true }),
    ).toEqual([]);
  });
});

describe('SignaturePad', () => {
  afterEach(() => vi.restoreAllMocks());

  it('emits the drawing after a stroke and null when cleared', () => {
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null);
    vi.spyOn(HTMLCanvasElement.prototype, 'toDataURL').mockReturnValue(PNG);
    const fixture = TestBed.createComponent(SignaturePad);
    fixture.componentRef.setInput('padId', 'sig-test');
    fixture.detectChanges();
    const emitted: (string | null)[] = [];
    fixture.componentInstance.signed.subscribe((v) => emitted.push(v));
    const canvas: HTMLCanvasElement = fixture.nativeElement.querySelector('#sig-test');
    const pointer = (type: string, x: number) =>
      canvas.dispatchEvent(
        Object.assign(new MouseEvent(type, { clientX: x, clientY: 20 }), { pointerId: 1 }),
      );
    pointer('pointerdown', 10);
    pointer('pointermove', 40);
    pointer('pointerup', 40);
    fixture.detectChanges();
    expect(emitted).toEqual([PNG]);
    expect(fixture.nativeElement.querySelector('.sig-pad').classList).toContain('signed');
    fixture.nativeElement.querySelector('#sig-test-clear').click();
    expect(emitted).toEqual([PNG, null]);
  });
});
