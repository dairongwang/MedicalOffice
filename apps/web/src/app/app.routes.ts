import { inject } from '@angular/core';
import { Router, type Routes } from '@angular/router';
import { AppointmentScheduler } from './features/patients/appointment-scheduler';
import { AppointmentView } from './features/patients/appointment-view';
import { CheckInPage } from './features/patients/check-in-page';
import { MedicinesPage } from './features/patients/medicines-page';
import { MyAppointments } from './features/patients/my-appointments';
import { PatientIntake } from './features/patients/patient-intake';
import { PatientsPage } from './features/patients/patients-page';
import { PaymentsPage } from './features/patients/payments-page';
import { ReportsPage } from './features/patients/reports-page';

export const routes: Routes = [
  {
    path: 'patients',
    component: PatientsPage,
    children: [
      {
        path: '',
        component: PatientIntake,
        pathMatch: 'full',
        title: 'Patient registration — Medic Office management',
      },
      {
        path: 'appointments',
        component: AppointmentScheduler,
        title: 'Appointments — Medic Office management',
      },
      {
        path: 'mine',
        component: MyAppointments,
        title: 'My appointments — Medic Office management',
      },
      {
        path: 'medicines',
        component: MedicinesPage,
        title: 'My medicines — Medic Office management',
      },
      {
        path: 'reports',
        component: ReportsPage,
        title: 'My reports — Medic Office management',
      },
      {
        // Payment methods and payment history, one tab.
        path: 'payments',
        component: PaymentsPage,
        title: 'Payments — Medic Office management',
      },
      // The former Payment history tab: now the second part of Payments.
      {
        path: 'history',
        redirectTo: () =>
          inject(Router).createUrlTree(['/patients/payments'], { fragment: 'payment-history' }),
      },
    ],
  },
  // The page behind the link a patient gets by e-mail / text after booking.
  {
    path: 'appointments/:token',
    component: AppointmentView,
    title: 'Your appointment — Medic Office management',
  },
  // The check-in page: questionnaire + signature, the estimate not covered by insurance + signature.
  // Opened by "Check in" in the app, or from the link the front desk sends (valid for 60 minutes).
  {
    path: 'check-in/:token',
    component: CheckInPage,
    title: 'Check in — Medic Office management',
  },
  // Old bookmarks of the registration page.
  { path: 'patients/new', redirectTo: 'patients' },
  // The patients tab is the home page (the former task dashboard was removed).
  { path: '', redirectTo: 'patients', pathMatch: 'full' },
  // Staff pages (job applications, onboarding, roster, availability) live in the separate staff
  // dashboard (apps/staff); the header links there (LinksService). Old bookmarks go home.
  { path: 'workers', redirectTo: 'patients' },
  { path: 'workers/**', redirectTo: 'patients' },
  { path: '**', redirectTo: 'patients' },
];
