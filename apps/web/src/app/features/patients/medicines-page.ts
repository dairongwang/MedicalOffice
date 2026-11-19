import { Component, inject } from '@angular/core';
import { AccountService } from '../../core/account/account.service';
import { ShellService } from '../../core/ui/shell.service';
import { MedicinesPanel } from './medicines-panel';

/**
 * My medicines (/patients/medicines) — next to My appointments and My reports in the Patients
 * tab: for each patient record on the signed-in account (yourself, a child, …) one tab with the
 * prescription medicines to take, the dosing, how to take them and the doctor's instructions
 * (`MedicinesPanel`, `GET /me/medications`). Without an account the page offers to log in /
 * register (the app shell's account dialog).
 */
@Component({
  selector: 'app-medicines-page',
  imports: [MedicinesPanel],
  template: `
    <section class="page intake mine" aria-labelledby="medicines-title">
      <header class="page-header">
        <div>
          <h2 id="medicines-title">My medicines</h2>
          <p class="muted small">
            The prescription medicines to take — how much, how and when, and what your doctor wants
            you to know — with a tab for each person on your account.
          </p>
        </div>
      </header>

      @if (!account.signedIn()) {
        <section class="card intake-card" id="medicines-signed-out">
          <h3>Sign in to see your medicines</h3>
          <p class="small muted">
            Your medicines are part of your patient account — yours and your family's, once their
            records are attached (My appointments → Attach a record).
          </p>
          <div class="dialog-actions">
            <span class="spacer"></span>
            <button
              class="btn"
              type="button"
              id="btn-medicines-register"
              (click)="shell.openAccount('register')"
            >
              Register
            </button>
            <button
              class="btn primary"
              type="button"
              id="btn-medicines-login"
              (click)="shell.openAccount('login')"
            >
              Log in
            </button>
          </div>
        </section>
      } @else {
        <app-my-medicines />
      }
    </section>
  `,
})
export class MedicinesPage {
  readonly account = inject(AccountService);
  readonly shell = inject(ShellService);
}
