import { Injectable, computed, inject, signal } from '@angular/core';
import { DbService } from '../db/db.service';
import type { MembershipType } from '../membership/membership';
import { RELEASE } from '../release-info';
import { ApiError, ApiService } from './api.service';
import {
  type DerivedKeys,
  KDF_PARAMS,
  deriveKeys,
  generateDataKey,
  unwrapDataKey,
  wrapDataKey,
} from '../sync/crypto';

export interface Session {
  token: string;
  account_id: string;
  device_id: string;
  email: string;
  wrapped_data_key: string;
  kdf_params: { kdf: string; iterations: number };
  vault_enabled: boolean;
  /** False (with an empty wrapped_data_key) until a sign-in-with-provider account chose its passphrase. */
  has_password: boolean;
}

/** "Sign in with …" providers the server offers (Google, Facebook). */
export type OAuthProviderName = 'google' | 'facebook' | 'apple';

export interface OAuthProvider {
  name: OAuthProviderName;
  label: string;
  available: boolean;
  /** The server's built-in fake consent screen stands in (no credentials configured). */
  fake: boolean;
}

export interface OAuthIdentity {
  provider: OAuthProviderName;
  email: string;
  linked_at: string;
  last_login_at: string | null;
}

/** What the provider round trip left in the URL fragment when the app reloaded. */
export type OAuthReturn =
  | { kind: 'ticket'; ticket: string; provider: string }
  | { kind: 'linked'; provider: string }
  | { kind: 'error'; message: string };

/** The three second factors: authenticator app, code by text message, code by e-mail. */
export type TwoFactorMethod = 'totp' | 'sms' | 'email';
/** The two that deliver a code. */
export type CodeChannel = 'sms' | 'email';

/** A code was sent by text message or e-mail. */
export interface CodeDelivery {
  method: CodeChannel;
  /** Masked: "+•••••••0100" · "a•••@example.com" */
  destination: string;
  expires_in: number;
  resend_after: number;
  /** The server's built-in fake took it (no provider configured): where the message can be read. */
  fake_outbox_url: string | null;
}

/** `202` from /auth/login: the password was right, the account wants a second factor. */
export interface TwoFactorChallenge {
  two_factor_required: true;
  challenge: string;
  expires_in: number;
  /** Enabled methods, + "recovery" while recovery codes are left. */
  methods: (TwoFactorMethod | 'recovery')[];
  default_method: TwoFactorMethod;
  destinations: Partial<Record<CodeChannel, string>>;
  /** The default method sends codes: one is on its way … */
  sent: CodeDelivery | null;
  /** … or could not be sent (pick another method or a recovery code). */
  send_error: string | null;
}

export interface TwoFactorStatus {
  enabled: boolean;
  enabled_at: string | null;
  recovery_codes_left: number;
  /** Authenticator set-up started (QR code shown) but no code confirmed yet. */
  pending: boolean;
  /** Enabled methods in preference order. */
  methods: TwoFactorMethod[];
  /** Masked phone number while text-message codes are on. */
  phone: string | null;
  email: string | null;
  /** What this server can set up (text/e-mail need a provider — or the built-in fake). */
  available: TwoFactorMethod[];
  /** Channels answered by the built-in fake (messages go to the test inbox). */
  fake_channels: CodeChannel[];
}

/** totp: what the authenticator app needs (the QR code encodes `otpauth_uri`, `secret` is for
 *  typing it in). sms/email: `delivery` — a code is on its way to the number/address being added. */
export interface TwoFactorSetup {
  method: TwoFactorMethod;
  secret: string;
  otpauth_uri: string;
  issuer: string;
  account: string;
  digits: number;
  period: number;
  algorithm: string;
  delivery: CodeDelivery | null;
  /** The method was already on: confirming the code replaces the old app / number / address. */
  replacing: boolean;
}

/** Shown once — the server keeps hashes only. */
export interface TwoFactorRecoveryCodes {
  recovery_codes: string[];
  status: TwoFactorStatus;
}

/** Outcome of a sign-in step: done, the second factor is needed, or (after a provider) the
 *  encryption passphrase has to be created / entered. */
export type LoginResult = 'signed-in' | 'two-factor' | 'passphrase';

export interface Entitlements {
  plan: string;
  status: string;
  features: string[];
  current_period_end: string | null;
  cancel_at_period_end: boolean;
  trial_available: boolean;
  /** Payment strategy holding the subscription: stripe | paypal | seed | admin | … */
  provider: string;
  /** Until when the paid membership lasts as things stand (null = Lifetime / nothing paid). */
  access_until: string | null;
  /** Renews automatically at access_until. */
  renews: boolean;
  /** POST /billing/cancel ends the membership now and refunds (GET /billing/cancel/preview). */
  can_cancel: boolean;
  /** The provider has a self-service page for this subscription (POST /billing/manage). */
  can_manage: boolean;
  /** The most recent charge — paid, failed or refunded. */
  last_payment: PaymentOut | null;
  /** Membership type resolved by the server (strategy key): free | paid | admin. */
  membership: MembershipType;
  membership_label: string;
  /** null = unlimited */
  max_active_tasks: number | null;
}

export type Plan = 'pro_monthly' | 'pro_yearly' | 'lifetime';

/** One way of paying, as the server's payment strategies describe themselves (/billing/methods). */
export interface PaymentMethod {
  /** Strategy key: stripe | paypal */
  name: string;
  label: string;
  description: string;
  plans: Plan[];
  /** Nothing is charged (the provider's test mode or the built-in fake). */
  sandbox: boolean;
  /** The server's built-in fake of this provider answers (no credentials configured). */
  fake: boolean;
  /** False = the server has no credentials for it yet (checkout answers 503 with the reason). */
  available: boolean;
}

/** One paid membership as sold (price from the server's PRICE_* settings, refund rule). */
export interface PlanInfo {
  id: Plan;
  title: string;
  /** Decimal string, e.g. "1.00" */
  amount: string;
  amount_cents: number;
  currency: string;
  interval: 'month' | 'year' | null;
  period_days: number | null;
  refund_rule: string;
}

export interface PaymentMethods {
  methods: PaymentMethod[];
  default: string | null;
  plans: PlanInfo[];
  currency: string;
}

/** One charge from the ledger: what was paid, failed or refunded. */
export interface PaymentOut {
  id: number;
  provider: string;
  plan: Plan;
  amount: string;
  currency: string;
  status: 'succeeded' | 'failed' | 'refunded' | 'partially_refunded';
  refunded: string;
  refund_id: string | null;
  failure_reason: string | null;
  period_start: string | null;
  period_end: string | null;
  paid_at: string;
}

/** What cancelling right now gives back. */
export interface RefundPreview {
  amount: string;
  amount_cents: number;
  currency: string;
  basis: 'prorated' | 'flat' | 'none';
  days_left: number | null;
  days_total: number | null;
  paid: string | null;
  note: string;
}

export interface CancelResult {
  entitlements: Entitlements;
  refund: RefundPreview;
  refund_id: string | null;
  refunded_via: 'provider' | 'recorded' | 'none';
}

export interface DeviceOut {
  id: string;
  platform: string;
  client_version: string;
  schema_version: number;
  cursor: number;
  last_seen_at: string;
  is_current: boolean;
}

export interface Me {
  account_id: string;
  email: string;
  role: 'user' | 'admin';
  created_at: string;
  vault_enabled: boolean;
  devices: DeviceOut[];
  entitlements: Entitlements;
  two_factor: TwoFactorStatus;
  /** Ways to sign in: the password (passphrase) and linked providers. */
  has_password: boolean;
  identities: OAuthIdentity[];
}

export interface StoredSession {
  accountId: string;
  deviceId: string;
  email: string;
  vaultEnabled: boolean;
  /** Last membership the server reported — applies while offline (MembershipService). */
  membership?: MembershipType;
  maxActiveTasks?: number | null;
}

function platformName(): string {
  const ua = navigator.userAgent;
  if (/iPhone|iPad|iPod/.test(ua)) return 'ios-web';
  if (/Android/.test(ua)) return 'android-web';
  if (/Windows/.test(ua)) return 'windows-web';
  if (/Mac OS/.test(ua)) return 'macos-web';
  if (/Linux/.test(ua)) return 'linux-web';
  return 'web';
}

/**
 * Optional account: everything works without one. With one, the device holds a non-extractable
 * data key (IndexedDB) and an access token; the server knows the e-mail, a verifier, the
 * wrapped key and the plan — nothing else.
 */
@Injectable({ providedIn: 'root' })
export class AccountService {
  private readonly api = inject(ApiService);
  private readonly dbs = inject(DbService);

  readonly session = signal<StoredSession | null>(null);
  readonly dataKey = signal<CryptoKey | null>(null);
  readonly me = signal<Me | null>(null);
  readonly ready = signal(false);
  readonly busy = signal(false);
  /** A sign-in passed the password step and now waits for the second factor (code). */
  readonly twoFactorPending = signal<TwoFactorChallenge | null>(null);
  /** The latest code sent during that sign-in (text/e-mail), or null. */
  readonly twoFactorSent = signal<CodeDelivery | null>(null);
  /** After "Sign in with Google/Facebook": the session exists, the data key is still locked —
   *  'create' a passphrase (new account) or 'unlock' with the existing one (new device). */
  readonly passphraseStep = signal<'create' | 'unlock' | null>(null);
  /** The provider round trip failed ("you cancelled", "no e-mail shared", …). */
  readonly oauthError = signal<string | null>(null);
  /** Which provider the pending sign-in came from (for the passphrase step's wording). */
  readonly oauthProvider = signal<string | null>(null);
  private pendingSession: Session | null = null;
  /** What the password step keeps in memory until the code step completes (never stored). */
  private pendingLogin: { email: string; password: string; keys: DerivedKeys } | null = null;

  readonly signedIn = computed(() => !!this.session() && !!this.dataKey());
  readonly entitlements = computed(() => this.me()?.entitlements ?? null);
  readonly twoFactor = computed(() => this.me()?.two_factor ?? null);
  readonly canSync = computed(
    () => this.signedIn() && (this.entitlements()?.features.includes('sync') ?? false),
  );
  readonly vaultEnabled = computed(
    () => this.me()?.vault_enabled ?? this.session()?.vaultEnabled ?? true,
  );

  async init(): Promise<void> {
    const db = this.dbs.db;
    const [session, key] = await Promise.all([
      db.getMeta<StoredSession>('session'),
      db.getMeta<CryptoKey>('dataKey'),
    ]);
    if (session && key && this.api.token()) {
      this.session.set(session);
      this.dataKey.set(key);
      void this.refreshMe();
    }
    // Membership can change behind the app's back (subscription renewed/lapsed, an administrator
    // granted Pro): re-read /me when the user returns to the tab, at most once a minute.
    document.addEventListener('visibilitychange', () => {
      if (document.hidden || !this.signedIn()) return;
      if (Date.now() - this.lastRefresh < 60_000) return;
      void this.refreshMe();
    });
    this.ready.set(true);
  }

  private lastRefresh = 0;

  async refreshMe(): Promise<Me | null> {
    if (!this.api.token()) return null;
    this.lastRefresh = Date.now();
    try {
      const me = await this.api.get<Me>('/me');
      this.me.set(me);
      await this.rememberMembership(me);
      return me;
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) await this.forget();
      return null;
    }
  }

  /** Cache the membership with the session so the right limits apply when the app starts offline. */
  private async rememberMembership(me: Me): Promise<void> {
    const s = this.session();
    if (!s || s.accountId !== me.account_id) return;
    const next: StoredSession = {
      ...s,
      email: me.email,
      vaultEnabled: me.vault_enabled,
      membership: me.entitlements.membership,
      maxActiveTasks: me.entitlements.max_active_tasks,
    };
    if (
      next.membership !== s.membership ||
      next.maxActiveTasks !== s.maxActiveTasks ||
      next.vaultEnabled !== s.vaultEnabled
    ) {
      this.session.set(next);
      await this.dbs.db.setMeta('session', next);
    }
  }

  private deviceInfo() {
    return {
      platform: platformName(),
      client_version: RELEASE.version,
      schema_version: RELEASE.schemaVersion,
    };
  }

  async register(email: string, password: string): Promise<void> {
    this.busy.set(true);
    try {
      const keys = await deriveKeys(password, email);
      const dataKey = await generateDataKey();
      const wrapped = await wrapDataKey(dataKey, keys.wrapKey);
      const session = await this.api.post<Session>(
        '/auth/register',
        {
          email,
          auth_key: keys.authKey,
          wrapped_data_key: wrapped,
          kdf_params: KDF_PARAMS,
          device: this.deviceInfo(),
        },
        { auth: false },
      );
      // Re-import as non-extractable for storage.
      await this.adopt(session, await unwrapDataKey(wrapped, keys.wrapKey));
    } finally {
      this.busy.set(false);
    }
  }

  /**
   * Password step. Accounts with two-factor authentication answer with a challenge instead of
   * a session (`'two-factor'`): the keys stay in memory and `completeLogin(code)` finishes.
   */
  async login(email: string, password: string): Promise<LoginResult> {
    this.busy.set(true);
    try {
      this.cancelLogin();
      const keys = await deriveKeys(password, email); // default params; re-derive if the account pins others
      const res = await this.api.post<Session | TwoFactorChallenge>(
        '/auth/login',
        { email, auth_key: keys.authKey, device: this.deviceInfo() },
        { auth: false },
      );
      if ('challenge' in res) {
        this.pendingLogin = { email, password, keys };
        this.twoFactorPending.set(res);
        this.twoFactorSent.set(res.sent);
        return 'two-factor';
      }
      await this.adoptLogin(res, email, password, keys);
      return 'signed-in';
    } finally {
      this.busy.set(false);
    }
  }

  /** Second step: the authenticator's 6-digit code or a recovery code. After a password the
   *  session is adopted; after a provider the passphrase step follows. */
  async completeLogin(code: string): Promise<LoginResult> {
    const pending = this.pendingLogin;
    const challenge = this.twoFactorPending();
    if (!challenge) throw new Error('Sign in first.');
    this.busy.set(true);
    try {
      const session = await this.api.post<Session>(
        '/auth/login/2fa',
        { challenge: challenge.challenge, code: code.trim(), device: this.deviceInfo() },
        { auth: false },
      );
      this.twoFactorPending.set(null);
      this.twoFactorSent.set(null);
      if (!pending) return this.afterProviderSession(session);
      await this.adoptLogin(session, pending.email, pending.password, pending.keys);
      this.cancelLogin();
      return 'signed-in';
    } catch (e) {
      // An expired challenge (or a second factor reset meanwhile): back to the password step.
      if (e instanceof ApiError && e.status === 401 && /no longer valid/i.test(e.message))
        this.cancelLogin();
      throw e;
    } finally {
      this.busy.set(false);
    }
  }

  /** Sign-in: send (or resend) a code by text message or e-mail. */
  async sendLoginCode(method: CodeChannel): Promise<CodeDelivery> {
    const challenge = this.twoFactorPending();
    if (!challenge) throw new Error('Sign in with your e-mail and password first.');
    const sent = await this.api.post<CodeDelivery>(
      '/auth/login/2fa/send',
      { challenge: challenge.challenge, method },
      { auth: false },
    );
    this.twoFactorSent.set(sent);
    return sent;
  }

  /** Drop a half-done sign-in (the password never outlives the dialog). A session a provider
   *  already issued is revoked again — nothing half-signed-in stays behind. */
  cancelLogin(): void {
    this.pendingLogin = null;
    this.twoFactorPending.set(null);
    this.twoFactorSent.set(null);
    this.passphraseStep.set(null);
    this.oauthError.set(null);
    this.oauthProvider.set(null);
    const session = this.pendingSession;
    this.pendingSession = null;
    if (session && !this.api.token()) {
      this.api.setToken(session.token);
      void this.api
        .post('/auth/logout')
        .catch(() => undefined)
        .finally(() => this.api.setToken(null));
    }
  }

  // ---- Sign in with Google / Facebook ----

  /** The providers this server offers (configured, or the built-in fake standing in). */
  async oauthProviders(): Promise<OAuthProvider[]> {
    const res = await this.api.get<{ providers: OAuthProvider[] }>('/auth/oauth/providers', {
      auth: false,
    });
    return res.providers.filter((p) => p.available);
  }

  /** Where to send the browser to sign in with a provider; it comes back to this page. */
  oauthStartUrl(provider: OAuthProviderName): string {
    return `${this.api.base}/auth/oauth/${provider}/start?return_to=${encodeURIComponent(this.returnTo())}`;
  }

  private returnTo(): string {
    return location.origin + location.pathname;
  }

  /** The provider sent the browser back (URL fragment): read and clear it. */
  consumeOAuthReturn(): OAuthReturn | null {
    const hash = location.hash.replace(/^#/, '');
    if (!hash) return null;
    const params = new URLSearchParams(hash);
    let out: OAuthReturn | null = null;
    if (params.get('oauth'))
      out = {
        kind: 'ticket',
        ticket: params.get('oauth')!,
        provider: params.get('provider') ?? '',
      };
    else if (params.get('oauth_linked'))
      out = { kind: 'linked', provider: params.get('oauth_linked')! };
    else if (params.get('oauth_error'))
      out = { kind: 'error', message: params.get('oauth_error')! };
    if (out) history.replaceState(null, '', location.pathname + location.search);
    return out;
  }

  /** Exchange the provider's ticket for a session; then the second factor or the passphrase. */
  async completeOAuth(ticket: string, provider = ''): Promise<LoginResult> {
    this.busy.set(true);
    try {
      this.cancelLogin();
      this.oauthProvider.set(provider || null);
      const res = await this.api.post<Session | TwoFactorChallenge>(
        '/auth/oauth/complete',
        { ticket, device: this.deviceInfo() },
        { auth: false },
      );
      if ('challenge' in res) {
        this.twoFactorPending.set(res);
        this.twoFactorSent.set(res.sent);
        return 'two-factor';
      }
      return this.afterProviderSession(res);
    } finally {
      this.busy.set(false);
    }
  }

  /** A provider signed us in — the data key still needs the passphrase (never the provider's business). */
  private afterProviderSession(session: Session): LoginResult {
    this.pendingSession = session;
    this.passphraseStep.set(session.has_password && session.wrapped_data_key ? 'unlock' : 'create');
    return 'passphrase';
  }

  /** New provider account: the passphrase wraps a fresh data key and becomes the password too. */
  async createPassphrase(passphrase: string): Promise<void> {
    const session = this.pendingSession;
    if (!session) throw new Error('Sign in first.');
    this.busy.set(true);
    try {
      const keys = await deriveKeys(passphrase, session.email);
      const dataKey = await generateDataKey();
      const wrapped = await wrapDataKey(dataKey, keys.wrapKey);
      this.api.setToken(session.token);
      await this.api.post('/me/passphrase', {
        auth_key: keys.authKey,
        wrapped_data_key: wrapped,
        kdf_params: KDF_PARAMS,
      });
      this.pendingSession = null;
      this.passphraseStep.set(null);
      await this.adopt(
        { ...session, wrapped_data_key: wrapped, has_password: true },
        await unwrapDataKey(wrapped, keys.wrapKey),
      );
    } catch (e) {
      if (!this.session()) this.api.setToken(null);
      throw e;
    } finally {
      this.busy.set(false);
    }
  }

  /** Existing account on a new device: the passphrase (= the account password) unlocks the data key. */
  async unlockWithPassphrase(passphrase: string): Promise<void> {
    const session = this.pendingSession;
    if (!session) throw new Error('Sign in first.');
    this.busy.set(true);
    try {
      const params =
        session.kdf_params?.iterations && session.kdf_params.iterations !== KDF_PARAMS.iterations
          ? session.kdf_params
          : KDF_PARAMS;
      const keys = await deriveKeys(passphrase, session.email, params);
      let dataKey: CryptoKey;
      try {
        dataKey = await unwrapDataKey(session.wrapped_data_key, keys.wrapKey);
      } catch {
        throw new Error(
          'That passphrase is not right. It is the password you use for this account.',
        );
      }
      this.pendingSession = null;
      this.passphraseStep.set(null);
      await this.adopt(session, dataKey);
    } finally {
      this.busy.set(false);
    }
  }

  /** Add a provider as a way to sign in: the browser goes there and comes back to this page. */
  async linkProvider(provider: OAuthProviderName): Promise<string> {
    const res = await this.api.post<{ url: string }>(`/me/oauth/${provider}/link`, {
      return_to: this.returnTo(),
    });
    return this.absolute(res.url);
  }

  async unlinkProvider(provider: OAuthProviderName): Promise<void> {
    this.me.set(await this.api.delete<Me>(`/me/oauth/${provider}`));
  }

  /** Pages the API hosts itself (the built-in fakes' test inbox, checkout pages) as absolute URLs. */
  apiPage(path: string): string {
    return this.absolute(path);
  }

  private async adoptLogin(session: Session, email: string, password: string, keys: DerivedKeys) {
    let wrapKey = keys.wrapKey;
    if (session.kdf_params?.iterations && session.kdf_params.iterations !== KDF_PARAMS.iterations) {
      wrapKey = (await deriveKeys(password, email, session.kdf_params)).wrapKey;
    }
    await this.adopt(session, await unwrapDataKey(session.wrapped_data_key, wrapKey));
  }

  // ---- Two-factor authentication (authenticator app + recovery codes) ----

  /** Sensitive changes re-prove the password: derived on the device, exactly like at sign-in. */
  private async authKey(password: string): Promise<string> {
    const email = this.session()?.email;
    if (!email) throw new Error('Please sign in first.');
    return (await deriveKeys(password, email)).authKey;
  }

  /**
   * Step 1 of turning a method on — or of changing one that is on (then `code`, a current code
   * from any method or a recovery code, is required besides the password): a new secret (QR
   * code) for the authenticator app, or a code sent to the phone number / e-mail address being
   * added. Nothing changes until `twoFactorEnable` confirms it.
   */
  async twoFactorSetup(
    password: string,
    method: TwoFactorMethod = 'totp',
    opts: { phone?: string; email?: string; code?: string } = {},
  ): Promise<TwoFactorSetup> {
    this.busy.set(true);
    try {
      return await this.api.post<TwoFactorSetup>('/me/2fa/setup', {
        auth_key: await this.authKey(password),
        method,
        phone: opts.phone?.trim() || null,
        email: opts.email?.trim() || null,
        code: opts.code?.trim() || null,
      });
    } finally {
      this.busy.set(false);
    }
  }

  /** "Send a new code" on the confirmation step of a set-up / change. */
  async twoFactorResend(): Promise<CodeDelivery> {
    return this.api.post<CodeDelivery>('/me/2fa/resend');
  }

  /** Step 2: the right code activates the method; the first method returns the recovery codes (shown once). */
  async twoFactorEnable(
    code: string,
    method: TwoFactorMethod = 'totp',
  ): Promise<TwoFactorRecoveryCodes> {
    const out = await this.api.post<TwoFactorRecoveryCodes>('/me/2fa/enable', {
      code: code.trim(),
      method,
    });
    await this.refreshMe();
    return out;
  }

  /** Signed in: a code by text/e-mail to confirm a sensitive change (remove, turn off, new codes). */
  async twoFactorSend(method: CodeChannel): Promise<CodeDelivery> {
    return this.api.post<CodeDelivery>('/me/2fa/send', { method });
  }

  /** Remove one method, or (no `method`) turn it off entirely: password and a current code. */
  async twoFactorDisable(
    password: string,
    code: string,
    method?: TwoFactorMethod,
  ): Promise<TwoFactorStatus> {
    this.busy.set(true);
    try {
      const out = await this.api.post<TwoFactorStatus>('/me/2fa/disable', {
        auth_key: await this.authKey(password),
        code: code.trim(),
        method: method ?? null,
      });
      await this.refreshMe();
      return out;
    } finally {
      this.busy.set(false);
    }
  }

  /** A fresh set of recovery codes (password + code); the old ones stop working. */
  async twoFactorRecoveryCodes(password: string, code: string): Promise<TwoFactorRecoveryCodes> {
    this.busy.set(true);
    try {
      const out = await this.api.post<TwoFactorRecoveryCodes>('/me/2fa/recovery-codes', {
        auth_key: await this.authKey(password),
        code: code.trim(),
      });
      await this.refreshMe();
      return out;
    } finally {
      this.busy.set(false);
    }
  }

  private async adopt(session: Session, dataKey: CryptoKey) {
    const stored: StoredSession = {
      accountId: session.account_id,
      deviceId: session.device_id,
      email: session.email,
      vaultEnabled: session.vault_enabled,
    };
    this.api.setToken(session.token);
    await this.dbs.db.setMeta('session', stored);
    await this.dbs.db.setMeta('dataKey', dataKey);
    this.session.set(stored);
    this.dataKey.set(dataKey);
    await this.refreshMe();
  }

  async logout(): Promise<void> {
    try {
      await this.api.post('/auth/logout');
    } catch {
      /* offline: the token is dropped locally; the server revokes it on expiry */
    }
    await this.forget();
  }

  async forget(): Promise<void> {
    this.pendingSession = null; // a session being forgotten is not "half-done"
    this.cancelLogin();
    this.api.setToken(null);
    await this.dbs.db.meta.bulkDelete(['session', 'dataKey', 'cursor', 'lastSyncAt']);
    this.session.set(null);
    this.dataKey.set(null);
    this.me.set(null);
  }

  async startTrial(): Promise<Entitlements> {
    const ent = await this.api.post<Entitlements>('/billing/trial');
    await this.refreshMe();
    return ent;
  }

  /** The payment methods the server offers right now (none = billing is not configured). */
  async paymentMethods(): Promise<PaymentMethods> {
    return this.api.get<PaymentMethods>('/billing/methods', { auth: false });
  }

  /** Starts a hosted checkout with the chosen payment method (undefined = the server's default)
   *  and returns the page to send the browser to. */
  async checkout(plan: Plan, method?: string): Promise<string> {
    const res = await this.api.post<{ checkout_url: string }>('/billing/checkout', {
      plan,
      method: method ?? null,
      return_url: location.origin + '/?billing=done',
    });
    return this.absolute(res.checkout_url);
  }

  /** What cancelling right now would refund (shown before asking for confirmation). */
  async cancelPreview(): Promise<RefundPreview> {
    return this.api.get<RefundPreview>('/billing/cancel/preview');
  }

  /** Ends the paid membership now and refunds (unused days of the period / the flat Lifetime amount). */
  async cancelSubscription(): Promise<CancelResult> {
    const result = await this.api.post<CancelResult>('/billing/cancel');
    await this.refreshMe();
    return result;
  }

  /** This account's charges, failures and refunds, newest first. */
  async payments(): Promise<PaymentOut[]> {
    return this.api.get<PaymentOut[]>('/billing/payments');
  }

  /** The provider's self-service page (payment method, invoices) for this subscription. */
  async manageUrl(): Promise<string> {
    const res = await this.api.post<{ url: string }>('/billing/manage', {
      return_url: location.origin + '/?billing=managed',
    });
    return this.absolute(res.url);
  }

  /** Pages the API hosts itself (built-in provider fakes) come back as paths on the API origin. */
  private absolute(url: string): string {
    return url.startsWith('/') ? this.api.base.replace(/\/api\/v1$/, '') + url : url;
  }

  async setVault(enabled: boolean): Promise<void> {
    const me = await this.api.patch<Me>('/me/settings', { vault_enabled: enabled });
    this.me.set(me);
    const s = this.session();
    if (s) {
      const next = { ...s, vaultEnabled: enabled };
      this.session.set(next);
      await this.dbs.db.setMeta('session', next);
    }
  }

  async revokeDevice(id: string): Promise<void> {
    await this.api.delete(`/me/devices/${id}`);
    await this.refreshMe();
  }

  async deleteAccount(): Promise<void> {
    await this.api.delete('/me');
    await this.forget();
  }
}
