import {
  Component,
  DestroyRef,
  ElementRef,
  InjectionToken,
  computed,
  inject,
  input,
  model,
  signal,
  viewChild,
} from '@angular/core';
import {
  type CardPhoto,
  CardPhotoError,
  formatBytes,
  prepareCardPhoto,
} from '../../core/patients/card-photo';
import type { InsuranceCardInfo, InsuranceCardSide } from '../../core/patients/patients.service';
import { DialogDirective } from '../../core/ui/dialog.directive';
import { TipDirective } from '../../core/ui/tip.directive';

/** How a picked or captured image becomes a `CardPhoto` (swapped out in unit tests: jsdom has no canvas). */
export const CARD_PHOTO_PREPARER = new InjectionToken<typeof prepareCardPhoto>(
  'CARD_PHOTO_PREPARER',
  {
    providedIn: 'root',
    factory: () => prepareCardPhoto,
  },
);

/**
 * One side of the insurance card on the registration page: a preview of the photo on file,
 * **Take photo** and **Upload photo** buttons, and **Remove**.
 *
 * Take photo opens the camera *inside the app* (getUserMedia: a live view with a card-shaped
 * guide, Capture, and Switch camera on devices with more than one). Where that is not
 * available — no camera, permission refused, or a page that is not served over HTTPS — the
 * same button falls back to the browser's own camera/file picker (`<input type="file"
 * capture="environment">`, which on phones opens the camera app directly). Upload photo opens
 * the gallery / file picker. Either way the image is shrunk and re-encoded on the device
 * (card-photo.ts) before it goes anywhere.
 *
 * Two-way bound: `[(photo)]="front"`. When the patient is *updating* their record, `stored`
 * describes the photo already on file (the image itself is only visible to the office); the
 * patient can keep it, replace it (a new photo) or remove it (`[(removed)]`).
 */
@Component({
  selector: 'app-insurance-card-capture',
  imports: [DialogDirective, TipDirective],
  template: `
    <div class="card-capture" [class.has-photo]="!!photo()" [id]="'insurance-card-' + side()">
      <div class="card-capture-head">
        <strong>{{ title() }}</strong>
        <span class="muted small" [id]="'insurance-card-' + side() + '-status'">
          @if (photo(); as p) {
            {{ p.source === 'camera' ? 'Photo taken' : 'Photo uploaded' }} · {{ p.width }}×{{
              p.height
            }}
            · {{ size(p) }}{{ stored() ? ' · replaces the photo on file' : '' }}
          } @else if (busy()) {
            Preparing the photo…
          } @else if (kept(); as f) {
            On file · uploaded {{ uploadedAt(f) }}
          } @else if (removed()) {
            Will be removed when you save
          } @else {
            No photo yet (optional)
          }
        </span>
      </div>

      @if (photo(); as p) {
        <img
          class="card-preview"
          [id]="'insurance-card-' + side() + '-preview'"
          [src]="p.dataUrl"
          [alt]="'Photo of the ' + side() + ' of your insurance card'"
        />
      } @else if (kept()) {
        <div class="card-placeholder on-file" aria-hidden="true">
          <span class="card-placeholder-icon">✓</span>
          <span>Photo on file — only the office can view it</span>
        </div>
      } @else {
        <div class="card-placeholder" aria-hidden="true">
          <span class="card-placeholder-icon">🪪</span>
          <span>{{ side() === 'front' ? 'Front of the card' : 'Back of the card' }}</span>
        </div>
      }

      @if (error(); as message) {
        <p class="small error-text" role="alert" [id]="'insurance-card-' + side() + '-error'">
          {{ message }}
        </p>
      }

      <div class="card-capture-actions">
        <button
          type="button"
          class="btn primary"
          [id]="'btn-card-' + side() + '-camera'"
          [disabled]="busy() || disabled()"
          (click)="openCamera()"
          [appTip]="
            'Take a photo of the ' +
            side() +
            ' of your insurance card with this device\\'s camera. Fill the frame with the card and avoid glare.'
          "
        >
          📷 {{ photo() ? 'Retake photo' : 'Take photo' }}
        </button>
        <button
          type="button"
          class="btn"
          [id]="'btn-card-' + side() + '-upload'"
          [disabled]="busy() || disabled()"
          (click)="pickFile()"
          [appTip]="
            'Choose a photo of the ' +
            side() +
            ' of your insurance card from your phone\\'s gallery or your computer (JPEG, PNG or WebP).'
          "
        >
          ⬆ {{ photo() ? 'Replace photo' : 'Upload photo' }}
        </button>
        @if (photo()) {
          <button
            type="button"
            class="btn ghost danger"
            [id]="'btn-card-' + side() + '-remove'"
            [disabled]="busy() || disabled()"
            (click)="remove()"
            appTip="Remove this photo. Nothing has been sent yet — the photos go to the office only when you press Register / Save."
          >
            Remove
          </button>
        } @else if (kept()) {
          <button
            type="button"
            class="btn ghost danger"
            [id]="'btn-card-' + side() + '-remove-file'"
            [disabled]="busy() || disabled()"
            (click)="removed.set(true)"
            appTip="Remove the photo on file when you save."
          >
            Remove from file
          </button>
        } @else if (removed()) {
          <button
            type="button"
            class="btn ghost"
            [id]="'btn-card-' + side() + '-keep'"
            [disabled]="busy() || disabled()"
            (click)="removed.set(false)"
            appTip="Keep the photo on file after all."
          >
            Keep it
          </button>
        }
      </div>

      <input
        #fileInput
        type="file"
        accept="image/*"
        hidden
        [id]="'insurance-card-' + side() + '-file'"
        (change)="onFile($event, 'upload')"
      />
      <input
        #captureInput
        type="file"
        accept="image/*"
        capture="environment"
        hidden
        [id]="'insurance-card-' + side() + '-capture'"
        (change)="onFile($event, 'camera')"
      />
    </div>

    <dialog
      appDialog
      class="camera-dialog"
      [id]="'camera-dialog-' + side()"
      [attr.aria-label]="'Take a photo of the ' + side() + ' of your insurance card'"
      (closed)="stopStream()"
    >
      <div class="camera">
        <h3>{{ title() }} — take a photo</h3>
        <div class="camera-stage" [class.ready]="streaming()">
          <video #video autoplay playsinline muted></video>
          <div class="camera-guide" aria-hidden="true"></div>
          @if (!streaming() && !cameraError()) {
            <p class="camera-hint">Starting the camera…</p>
          }
        </div>
        @if (cameraError(); as message) {
          <div class="callout error" role="alert">
            <strong>The camera could not be started</strong>
            <span>{{ message }}</span>
          </div>
        } @else {
          <p class="small muted">
            Lay the card flat, fill the frame with it and avoid reflections. Hold still, then press
            <em>Capture</em>.
          </p>
        }
        <div class="dialog-actions">
          <button
            type="button"
            class="btn"
            [id]="'btn-camera-' + side() + '-cancel'"
            (click)="closeCamera()"
          >
            Cancel
          </button>
          @if (canSwitch()) {
            <button
              type="button"
              class="btn"
              [id]="'btn-camera-' + side() + '-switch'"
              [disabled]="!streaming()"
              (click)="switchCamera()"
              appTip="Use the other camera (front / back)."
            >
              ⟲ Switch camera
            </button>
          }
          <span class="spacer"></span>
          @if (cameraError()) {
            <button
              type="button"
              class="btn primary"
              [id]="'btn-camera-' + side() + '-fallback'"
              (click)="useDevicePicker()"
            >
              Use the phone's camera app instead
            </button>
          } @else {
            <button
              type="button"
              class="btn primary"
              [id]="'btn-camera-' + side() + '-capture'"
              [disabled]="!streaming() || busy()"
              (click)="capture()"
            >
              {{ busy() ? 'Saving…' : '📷 Capture' }}
            </button>
          }
        </div>
      </div>
    </dialog>
  `,
})
export class InsuranceCardCapture {
  private readonly prepare = inject(CARD_PHOTO_PREPARER);
  private readonly destroyRef = inject(DestroyRef);

  readonly side = input.required<InsuranceCardSide>();
  readonly disabled = input(false);
  /** The photo picked or taken on this device (two-way). */
  readonly photo = model<CardPhoto | null>(null);
  /** Updating a record: the photo already on file for this side, if any. */
  readonly stored = input<InsuranceCardInfo | null>(null);
  /** Updating a record: the patient asked to drop the stored photo (two-way). */
  readonly removed = model(false);

  readonly busy = signal(false);
  readonly error = signal<string | null>(null);
  readonly streaming = signal(false);
  readonly cameraError = signal<string | null>(null);
  readonly canSwitch = signal(false);

  readonly title = computed(() =>
    this.side() === 'front' ? 'Front of the card' : 'Back of the card',
  );
  /** The stored photo that stays unless replaced or removed. */
  readonly kept = computed(() => (this.photo() || this.removed() ? null : this.stored()));

  uploadedAt(f: InsuranceCardInfo): string {
    const d = new Date(f.uploaded_at);
    return isNaN(+d)
      ? ''
      : d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
  }

  readonly fileInput = viewChild.required<ElementRef<HTMLInputElement>>('fileInput');
  readonly captureInput = viewChild.required<ElementRef<HTMLInputElement>>('captureInput');
  readonly video = viewChild.required<ElementRef<HTMLVideoElement>>('video');
  readonly dialog = viewChild.required(DialogDirective);

  private stream: MediaStream | null = null;
  private facing: 'environment' | 'user' = 'environment';

  constructor() {
    this.destroyRef.onDestroy(() => this.stopStream());
  }

  size(p: CardPhoto): string {
    return formatBytes(p.bytes);
  }

  /** In-app camera when the browser offers one (HTTPS / localhost), otherwise the device's picker. */
  static cameraAvailable(): boolean {
    return (
      typeof navigator !== 'undefined' &&
      !!navigator.mediaDevices?.getUserMedia &&
      (typeof window === 'undefined' || window.isSecureContext !== false)
    );
  }

  async openCamera(): Promise<void> {
    this.error.set(null);
    if (!InsuranceCardCapture.cameraAvailable()) {
      this.useDevicePicker();
      return;
    }
    this.cameraError.set(null);
    this.dialog().open();
    await this.startStream();
  }

  /** The gallery / file picker. */
  pickFile(): void {
    this.error.set(null);
    this.fileInput().nativeElement.click();
  }

  /** The browser's own camera / file picker (`capture="environment"` opens the camera app on phones). */
  useDevicePicker(): void {
    this.closeCamera();
    this.captureInput().nativeElement.click();
  }

  closeCamera(): void {
    this.stopStream();
    this.dialog().close();
  }

  async switchCamera(): Promise<void> {
    this.facing = this.facing === 'environment' ? 'user' : 'environment';
    await this.startStream();
  }

  private async startStream(): Promise<void> {
    this.stopStream();
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: false,
        video: {
          facingMode: { ideal: this.facing },
          width: { ideal: 1920 },
          height: { ideal: 1080 },
        },
      });
      this.stream = stream;
      const video = this.video().nativeElement;
      video.srcObject = stream;
      await video.play().catch(() => undefined);
      this.streaming.set(true);
      void this.detectCameras();
    } catch (e) {
      this.streaming.set(false);
      this.cameraError.set(describeCameraError(e));
    }
  }

  private async detectCameras(): Promise<void> {
    try {
      const devices = await navigator.mediaDevices.enumerateDevices();
      this.canSwitch.set(devices.filter((d) => d.kind === 'videoinput').length > 1);
    } catch {
      this.canSwitch.set(false);
    }
  }

  stopStream(): void {
    if (this.stream) {
      for (const track of this.stream.getTracks()) track.stop();
      this.stream = null;
    }
    this.streaming.set(false);
    const video = this.video().nativeElement;
    if (video.srcObject) video.srcObject = null;
  }

  /** Grab the current frame of the live view and keep it as the photo. */
  async capture(): Promise<void> {
    const video = this.video().nativeElement;
    if (!this.streaming() || !video.videoWidth) return;
    const canvas = document.createElement('canvas');
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    const ctx = canvas.getContext('2d');
    if (!ctx) {
      this.cameraError.set('Your browser could not process the picture.');
      return;
    }
    ctx.drawImage(video, 0, 0);
    const blob = await new Promise<Blob | null>((resolve) =>
      canvas.toBlob(resolve, 'image/jpeg', 0.92),
    );
    if (!blob) {
      this.cameraError.set('Your browser could not process the picture.');
      return;
    }
    await this.accept(blob, 'camera');
    this.closeCamera();
  }

  async onFile(event: Event, source: CardPhoto['source']): Promise<void> {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    input.value = ''; // so picking the same file again fires `change`
    if (!file) return;
    await this.accept(file, source);
  }

  remove(): void {
    this.photo.set(null);
    this.error.set(null);
  }

  private async accept(file: Blob, source: CardPhoto['source']): Promise<void> {
    this.busy.set(true);
    this.error.set(null);
    try {
      this.photo.set(await this.prepare(file, source));
      this.removed.set(false); // a new photo supersedes "remove the one on file"
    } catch (e) {
      this.error.set(
        e instanceof CardPhotoError
          ? e.message
          : 'The photo could not be read. Please try another one or take a new photo.',
      );
    } finally {
      this.busy.set(false);
    }
  }
}

function describeCameraError(e: unknown): string {
  const name = (e as { name?: string } | null)?.name ?? '';
  switch (name) {
    case 'NotAllowedError':
    case 'SecurityError':
      return "Camera access was not allowed. You can allow it in your browser settings, or use your phone's camera app instead.";
    case 'NotFoundError':
    case 'OverconstrainedError':
      return 'No camera was found on this device.';
    case 'NotReadableError':
      return 'The camera is in use by another app. Close it and try again.';
    default:
      return e instanceof Error && e.message
        ? e.message
        : 'The camera is not available on this device.';
  }
}
