import { Injectable, inject, signal } from '@angular/core';
import { ApiService } from './account/api.service';

/**
 * Where the other, separately deployed front-ends live (GET /api/v1/links). The staff dashboard
 * (apps/staff) and the office administration dashboard (apps/admin) have their own
 * origins/containers, so their URLs are configured on the server (STAFF_URL,
 * ADMIN_DASHBOARD_URL) rather than baked into this build. Empty = no link shown.
 */
@Injectable({ providedIn: 'root' })
export class LinksService {
  private readonly api = inject(ApiService);
  readonly staffUrl = signal<string | null>(null);
  /** The office administration dashboard (apps/admin) — not linked from this patient-facing app,
   *  kept for completeness (the API reports it). */
  readonly adminUrl = signal<string | null>(null);

  async load(): Promise<void> {
    try {
      const links = await this.api.get<{ staff_url?: string; admin_url?: string }>('/links', {
        auth: false,
      });
      this.staffUrl.set(links.staff_url?.trim() || null);
      this.adminUrl.set(links.admin_url?.trim() || null);
    } catch {
      this.staffUrl.set(null); // offline or older API: simply no link
      this.adminUrl.set(null);
    }
  }
}
