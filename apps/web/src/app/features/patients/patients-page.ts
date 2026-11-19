import { Component } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { TipDirective } from '../../core/ui/tip.directive';

/**
 * The Patients tab (/patients): a small sub-navigation between what a patient does here —
 * register (or confirm they are known), book an appointment, and — with a patient account —
 * see all their appointments, the medicines to take (a tab per family member), ask for reports signed by a doctor (immunization record, annual health
 * examination) and — under Payments — manage the payment methods on file and see the payment history.
 */
@Component({
  selector: 'app-patients-page',
  imports: [RouterOutlet, RouterLink, RouterLinkActive, TipDirective],
  template: `
    <nav class="subnav" aria-label="Patient services">
      <a
        class="subnav-link"
        id="subnav-register"
        routerLink="/patients"
        routerLinkActive="active"
        [routerLinkActiveOptions]="{ exact: true }"
        appTip="Patient registration: find out whether you already have a record with this office and register if you are new."
        >Registration</a
      >
      <a
        class="subnav-link"
        id="subnav-appointments"
        routerLink="/patients/appointments"
        routerLinkActive="active"
        appTip="Book, see or cancel your appointments with a doctor or nurse of this office. You need to be registered first."
        >Appointments</a
      >
      <a
        class="subnav-link"
        id="subnav-mine"
        routerLink="/patients/mine"
        routerLinkActive="active"
        appTip="Your patient account: every appointment on your records, upcoming and past. Sign in or create a patient account."
        >My appointments</a
      >
      <a
        class="subnav-link"
        id="subnav-medicines"
        routerLink="/patients/medicines"
        routerLinkActive="active"
        appTip="The prescription medicines to take — dosing, how to take them and your doctor's instructions — a tab for each person on your account."
        >My medicines</a
      >
      <a
        class="subnav-link"
        id="subnav-reports"
        routerLink="/patients/reports"
        routerLinkActive="active"
        appTip="Ask for your immunization record or your annual health examination report — signed by a doctor — and download them."
        >My reports</a
      >
      <a
        class="subnav-link"
        id="subnav-payments"
        routerLink="/patients/payments"
        routerLinkActive="active"
        appTip="Your payment methods (a card, Stripe, PayPal or a bank account for copays and self-pay visits) and every payment on your account: copays and fees, statements, refunds, validations."
        >Payments</a
      >
    </nav>
    <router-outlet />
  `,
})
export class PatientsPage {}
