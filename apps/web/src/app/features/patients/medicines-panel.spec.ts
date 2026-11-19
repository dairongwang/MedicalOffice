import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { routes } from '../../app.routes';
import { AccountService } from '../../core/account/account.service';
import { ApiError } from '../../core/account/api.service';
import {
  MedicationsService,
  type MedicationEntry,
  type MyMedications,
  shortDate,
  supplyText,
} from '../../core/medications/medications.service';
import { ShellService } from '../../core/ui/shell.service';
import { MedicinesPage } from './medicines-page';
import { MedicinesPanel } from './medicines-panel';
import { PatientsPage } from './patients-page';

const LISINOPRIL: MedicationEntry = {
  name: 'Lisinopril',
  strength: '10 mg',
  form: 'tablet',
  line: 'Lisinopril 10 mg tablet',
  directions: 'Take 1 tablet by mouth once daily',
  patient_instructions: 'Take it at the same time every day. (Dummy.)',
  indication: 'Essential hypertension',
  status: 'current',
  prescribed: true,
  prescription_id: 1,
  prescription_number: 'RX-000001',
  prescribed_on: '2027-03-01T10:20:00Z',
  prescriber_name: 'Dr. Alex Example',
  quantity: 90,
  quantity_unit: 'tablets',
  refills: 3,
  sent_to_pharmacy: 'Example Pharmacy (dummy)',
  note: null,
  updated_at: null,
};

const DATA: MyMedications = {
  office_name: 'Medic Office',
  office_phone: '+1 555 010 0000',
  generated_at: '2027-06-01T00:00:00Z',
  records: [
    {
      patient_id: 1,
      patient_name: 'Pat Dummy',
      first_name: 'Pat',
      mrn: 'P-000001',
      date_of_birth: '1985-03-14',
      allergies: [{ name: 'Penicillin', detail: 'rash' }],
      current: [
        LISINOPRIL,
        {
          ...LISINOPRIL,
          name: 'Atorvastatin',
          line: 'Atorvastatin',
          strength: null,
          form: null,
          directions: '20 mg at night',
          patient_instructions: null,
          indication: null,
          prescribed: false,
          prescription_id: null,
          prescription_number: null,
          prescribed_on: null,
          prescriber_name: null,
          quantity: null,
          quantity_unit: null,
          refills: null,
          sent_to_pharmacy: null,
        },
      ],
      stopped: [],
      history: [
        {
          prescription_id: 1,
          prescription_number: 'RX-000001',
          prescribed_on: '2027-03-01T10:20:00Z',
          prescriber_name: 'Dr. Alex Example',
          line: 'Lisinopril 10 mg tablet',
          directions: 'Take 1 tablet by mouth once daily',
          patient_instructions: 'Take it at the same time every day. (Dummy.)',
          quantity: 90,
          quantity_unit: 'tablets',
          refills: 3,
          indication: 'Essential hypertension',
        },
      ],
      pharmacy_name: null,
      pharmacy_phone: null,
      last_reviewed_at: null,
    },
    {
      patient_id: 5,
      patient_name: 'Sky Dummy',
      first_name: 'Sky',
      mrn: 'P-000005',
      date_of_birth: '2017-04-09',
      allergies: [],
      current: [
        {
          ...LISINOPRIL,
          name: 'Albuterol HFA',
          line: 'Albuterol HFA 90 mcg/puff inhaler',
          directions: 'Inhale 2 puffs by mouth every 4 to 6 hours as needed for wheezing',
          patient_instructions: 'Use it with the spacer. (Dummy.)',
          indication: 'Mild intermittent asthma, uncomplicated',
          prescription_number: 'RX-000002',
          quantity: 1,
          quantity_unit: 'inhaler',
          refills: 2,
        },
      ],
      stopped: [
        {
          ...LISINOPRIL,
          name: 'Amoxicillin',
          line: 'Amoxicillin',
          status: 'stopped',
          prescribed: false,
          directions: '5 mL by mouth twice daily',
          note: 'Stopped after three days — hives. (Dummy.)',
        },
      ],
      history: [],
      pharmacy_name: 'Example Pharmacy (dummy)',
      pharmacy_phone: '+1 555 010 0400',
      last_reviewed_at: null,
    },
  ],
};

describe('MedicinesPanel — My medicines, one tab per patient record', () => {
  let answer: () => Promise<MyMedications>;

  beforeEach(() => {
    answer = () => Promise.resolve(DATA);
    TestBed.configureTestingModule({
      providers: [{ provide: MedicationsService, useValue: { mine: () => answer() } }],
    });
  });

  const settle = async (fixture: { detectChanges(): void }, n = 3) => {
    for (let i = 0; i < n; i++) {
      await new Promise((r) => setTimeout(r, 0));
      fixture.detectChanges();
    }
  };

  it('shows a tab per record with the medicines, dosing and instructions', async () => {
    const fixture = TestBed.createComponent(MedicinesPanel);
    fixture.autoDetectChanges();
    await settle(fixture);
    const el: HTMLElement = fixture.nativeElement;

    const tabs = [...el.querySelectorAll<HTMLButtonElement>('[role="tab"]')];
    expect(tabs.map((t) => t.textContent!.replace(/\s+/g, ' ').trim())).toEqual(['Pat 2', 'Sky 1']);
    expect(tabs[0].getAttribute('aria-selected')).toBe('true');
    expect(tabs[1].getAttribute('aria-selected')).toBe('false');

    const pat = el.querySelector('#meds-panel-1')!;
    expect(pat.getAttribute('role')).toBe('tabpanel');
    expect(pat.getAttribute('aria-labelledby')).toBe('meds-tab-1');
    const first = pat.querySelector('#med-1-0')!.textContent!;
    expect(first).toContain('Lisinopril 10 mg tablet');
    expect(first).toContain('Take 1 tablet by mouth once daily');
    expect(first).toContain('Take it at the same time every day. (Dummy.)');
    expect(first).toContain('Essential hypertension');
    expect(first).toContain('RX-000001');
    expect(first).toContain('90 tablets · 3 refills');
    expect(first).toContain('sent to Example Pharmacy (dummy)');
    expect(pat.querySelector('#med-1-1')!.textContent).toContain('from your list');
    expect(pat.querySelector('#meds-allergies-1')!.textContent).toContain('Penicillin (rash)');
    expect(pat.querySelector('#meds-history-1 summary')!.textContent).toContain('(1)');
    expect(pat.textContent).toContain('No pharmacy on file yet');
    expect(pat.textContent).toContain('+1 555 010 0000');

    tabs[1].click();
    await settle(fixture);
    expect(el.querySelector('#meds-panel-1')).toBeNull();
    const sky = el.querySelector('#meds-panel-5')!;
    expect(tabs[1].getAttribute('aria-selected')).toBe('true');
    expect(sky.textContent).toContain('Albuterol HFA 90 mcg/puff inhaler');
    expect(sky.textContent).toContain('Use it with the spacer. (Dummy.)');
    expect(sky.textContent).toContain('1 inhaler · 2 refills');
    expect(sky.querySelector('#meds-allergies-5')!.textContent).toContain('No allergies on file.');
    expect(sky.querySelector('#meds-stopped-5')!.textContent).toContain('hives');
    expect(sky.querySelector('#meds-history-5')).toBeNull();
    expect(sky.textContent).toContain('Your pharmacy: Example Pharmacy (dummy)');
  });

  it('moves between the tabs with the arrow keys', async () => {
    const fixture = TestBed.createComponent(MedicinesPanel);
    fixture.autoDetectChanges();
    await settle(fixture);
    const el: HTMLElement = fixture.nativeElement;
    const tab = (id: number) => el.querySelector<HTMLButtonElement>(`#meds-tab-${id}`)!;
    tab(1).dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    await settle(fixture);
    expect(tab(5).getAttribute('aria-selected')).toBe('true');
    expect(tab(1).tabIndex).toBe(-1);
    tab(5).dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    await settle(fixture);
    expect(tab(1).getAttribute('aria-selected')).toBe('true'); // wraps around
    tab(1).dispatchEvent(new KeyboardEvent('keydown', { key: 'End', bubbles: true }));
    await settle(fixture);
    expect(tab(5).getAttribute('aria-selected')).toBe('true');
  });

  it('explains when nothing is linked and when loading fails', async () => {
    answer = () => Promise.resolve({ ...DATA, records: [] });
    let fixture = TestBed.createComponent(MedicinesPanel);
    fixture.autoDetectChanges();
    await settle(fixture);
    expect(fixture.nativeElement.querySelector('#meds-no-records')).not.toBeNull();

    answer = () => Promise.reject(new ApiError('Please sign in again.', 401));
    fixture = TestBed.createComponent(MedicinesPanel);
    fixture.autoDetectChanges();
    await settle(fixture);
    expect(fixture.nativeElement.querySelector('#meds-error')!.textContent).toContain(
      'Please sign in again.',
    );
  });

  it('formats the supply and the dates', () => {
    expect(supplyText({ quantity: 150, quantity_unit: 'mL', refills: 0 })).toBe(
      '150 mL · no refills',
    );
    expect(supplyText({ quantity: 30, quantity_unit: 'tablets', refills: 1 })).toBe(
      '30 tablets · 1 refill',
    );
    expect(supplyText({ quantity: null, quantity_unit: null, refills: null })).toBe('');
    expect(shortDate('2017-04-09')).toBe('Apr 9, 2017');
    expect(shortDate(null)).toBe('');
  });
});

describe('MedicinesPage — My medicines, next to My reports in the Patients tab', () => {
  const signedIn = signal(false);
  let opened: string[];

  beforeEach(() => {
    opened = [];
    signedIn.set(false);
    TestBed.configureTestingModule({
      providers: [
        provideRouter(routes),
        { provide: AccountService, useValue: { signedIn } },
        { provide: ShellService, useValue: { openAccount: (m: string) => opened.push(m) } },
        { provide: MedicationsService, useValue: { mine: () => Promise.resolve(DATA) } },
      ],
    });
  });

  const settle = async (fixture: { detectChanges(): void }, n = 3) => {
    for (let i = 0; i < n; i++) {
      await new Promise((r) => setTimeout(r, 0));
      fixture.detectChanges();
    }
  };

  it('is a top-level link of the Patients tab, next to My reports', () => {
    expect(routes[0].children!.find((r) => r.path === 'medicines')?.component).toBe(MedicinesPage);
    const fixture = TestBed.createComponent(PatientsPage);
    fixture.detectChanges();
    const links = [...(fixture.nativeElement as HTMLElement).querySelectorAll('.subnav-link')];
    const ids = links.map((a) => a.id);
    expect(ids).toContain('subnav-medicines');
    expect(ids.indexOf('subnav-reports')).toBe(ids.indexOf('subnav-medicines') + 1);
    const link = links.find((a) => a.id === 'subnav-medicines')!;
    expect(link.getAttribute('href')).toBe('/patients/medicines');
    expect(link.textContent!.trim()).toBe('My medicines');
  });

  it('asks to sign in first, then shows a tab per record', async () => {
    const fixture = TestBed.createComponent(MedicinesPage);
    fixture.autoDetectChanges();
    await settle(fixture);
    const el: HTMLElement = fixture.nativeElement;
    expect(el.querySelector('#medicines-signed-out')).not.toBeNull();
    expect(el.querySelector('[role="tab"]')).toBeNull();
    el.querySelector<HTMLButtonElement>('#btn-medicines-login')!.click();
    expect(opened).toEqual(['login']);

    signedIn.set(true);
    await settle(fixture);
    expect(el.querySelector('#medicines-signed-out')).toBeNull();
    expect(el.querySelectorAll('[role="tab"]').length).toBe(2);
    expect(el.querySelector('#meds-panel-1')!.textContent).toContain('Lisinopril 10 mg tablet');
  });
});
