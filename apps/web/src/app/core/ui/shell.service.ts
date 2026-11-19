import { Injectable } from '@angular/core';

/** What the app shell (App) can do for routed pages: open the app-wide dialogs. */
export interface ShellHandlers {
  openAccount(mode?: 'login' | 'register'): void;
  openAdInfo(): void;
}

/**
 * Bridge between routed pages (patient registration, appointments, …) and the shell that owns
 * the dialogs, toast and tooltips. App registers its handlers once; pages just call.
 */
@Injectable({ providedIn: 'root' })
export class ShellService implements ShellHandlers {
  private handlers: ShellHandlers | null = null;

  register(handlers: ShellHandlers): void {
    this.handlers = handlers;
  }

  openAccount(mode?: 'login' | 'register'): void {
    this.handlers?.openAccount(mode);
  }
  openAdInfo(): void {
    this.handlers?.openAdInfo();
  }
}
