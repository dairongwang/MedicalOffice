import { Component, inject } from '@angular/core';
import { RouterLink } from '@angular/router';
import { AccountService } from '../../core/account/account.service';
import { ShellService } from '../../core/ui/shell.service';
import { TipDirective } from '../../core/ui/tip.directive';
import { PaymentHistoryPanel } from './payment-history';
import { PaymentMethodsPanel } from './payment-methods';

/**
 * Payments (/patients/payments) — one tab of the Patients area for everything about paying the
 * office: the **payment methods** on the account (`PaymentMethodsPanel`: add, validate, make
 * default, remove) and the **payment history** (`PaymentHistoryPanel`: holds, charges, refunds,
 * validations). The old address /patients/history leads here (to #payment-history). Signed out,
 * the page says what it is for and offers to log in / register (the app shell's account dialog).
 */
@Component({
  selector: 'app-payments-page',
  imports: [RouterLink, TipDirective, PaymentMethodsPanel, PaymentHistoryPanel],
  template: `
    <section class="page intake mine" aria-labelledby="payments-title">
      <header class="page-header">
        <div>
          <h2 id="payments-title">Payments</h2>
          <p class="muted small">
            Your payment methods and every payment on your account. A validated payment method is
            needed to book: the copay your insurance names (or the self-pay visit fee) is put on
            hold when you book, charged after the visit and released if you cancel.
          </p>
        </div>
      </header>

      @if (!account.signedIn()) {
        <section class="card intake-card" id="payments-signed-out">
          <h3>Sign in to see your payments</h3>
          <p class="small muted">
            Payment methods and payments belong to your patient account. Sign in, or create an
            account with your e-mail address.
          </p>
          <div class="dialog-actions">
            <a
              class="btn"
              routerLink="/patients/appointments"
              appTip="Book or see appointments with your name and date of birth."
              >Appointments</a
            >
            <span class="spacer"></span>
            <button
              class="btn"
              type="button"
              id="btn-payments-register"
              (click)="shell.openAccount('register')"
              appTip="Create a patient account with your e-mail address."
            >
              Register
            </button>
            <button
              class="btn primary"
              type="button"
              id="btn-payments-login"
              (click)="shell.openAccount('login')"
              appTip="Sign in to your patient account."
            >
              Log in
            </button>
          </div>
        </section>
      } @else {
        <nav class="payments-jump small" aria-label="On this page">
          <a href="#pay-methods" (click)="jump($event, 'pay-methods')">Payment methods</a>
          ·
          <a href="#payment-history" (click)="jump($event, 'payment-history')">Payment history</a>
        </nav>
        <app-payment-methods />
        <app-payment-history />
      }
    </section>
  `,
})
export class PaymentsPage {
  readonly account = inject(AccountService);
  readonly shell = inject(ShellService);

  /** In-page links without leaving the route (the router would treat a bare #… as a new URL). */
  jump(event: Event, id: string): void {
    event.preventDefault();
    document.getElementById(id)?.scrollIntoView?.({ behavior: 'smooth', block: 'start' });
  }
}
