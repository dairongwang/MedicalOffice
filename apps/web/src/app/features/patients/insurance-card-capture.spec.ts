import { TestBed } from '@angular/core/testing';
import { type CardPhoto, CardPhotoError } from '../../core/patients/card-photo';
import { CARD_PHOTO_PREPARER, InsuranceCardCapture } from './insurance-card-capture';

const PHOTO: CardPhoto = {
  dataUrl: 'data:image/jpeg;base64,' + btoa('x'.repeat(300)),
  width: 1600,
  height: 1009,
  bytes: 300,
  source: 'upload',
};

describe('InsuranceCardCapture (one side of the card)', () => {
  let prepared: { size: number; source: string }[];
  let fail: string | null;

  beforeEach(() => {
    prepared = [];
    fail = null;
    TestBed.configureTestingModule({
      providers: [
        {
          provide: CARD_PHOTO_PREPARER,
          useValue: (file: Blob, source: CardPhoto['source']) => {
            prepared.push({ size: file.size, source });
            return fail
              ? Promise.reject(new CardPhotoError(fail))
              : Promise.resolve({ ...PHOTO, source });
          },
        },
      ],
    });
  });

  async function mount(side: 'front' | 'back' = 'front') {
    const fixture = TestBed.createComponent(InsuranceCardCapture);
    fixture.componentRef.setInput('side', side);
    fixture.autoDetectChanges();
    await fixture.whenStable();
    return fixture;
  }
  const settle = async (fixture: { detectChanges(): void }) => {
    await new Promise((r) => setTimeout(r, 0));
    fixture.detectChanges();
  };
  const text = (el: Element, sel: string) => el.querySelector(sel)?.textContent?.trim() ?? '';

  function pick(input: HTMLInputElement, file: File) {
    Object.defineProperty(input, 'files', { value: [file], configurable: true });
    input.dispatchEvent(new Event('change'));
  }

  it('starts empty, shows the preview after an upload and can remove it', async () => {
    const fixture = await mount();
    const el: HTMLElement = fixture.nativeElement;
    expect(text(el, '#insurance-card-front-status')).toContain('No photo yet');
    expect(el.querySelector('#insurance-card-front-preview')).toBeNull();
    expect(el.querySelector('#btn-card-front-remove')).toBeNull();
    expect(text(el, '#btn-card-front-upload')).toContain('Upload photo');
    expect(text(el, '#btn-card-front-camera')).toContain('Take photo');

    pick(
      el.querySelector<HTMLInputElement>('#insurance-card-front-file')!,
      new File(['jpeg-bytes'], 'card.jpg', { type: 'image/jpeg' }),
    );
    await settle(fixture);
    expect(prepared).toEqual([{ size: 10, source: 'upload' }]);
    expect(fixture.componentInstance.photo()?.dataUrl).toBe(PHOTO.dataUrl);
    expect(el.querySelector<HTMLImageElement>('#insurance-card-front-preview')?.src).toBe(
      PHOTO.dataUrl,
    );
    expect(text(el, '#insurance-card-front-status')).toContain(
      'Photo uploaded · 1600×1009 · 300 B',
    );
    expect(text(el, '#btn-card-front-upload')).toContain('Replace photo');
    expect(text(el, '#btn-card-front-camera')).toContain('Retake photo');
    expect(
      el.classList.contains('has-photo') || el.querySelector('.card-capture.has-photo'),
    ).toBeTruthy();

    el.querySelector<HTMLButtonElement>('#btn-card-front-remove')!.click();
    await settle(fixture);
    expect(fixture.componentInstance.photo()).toBeNull();
    expect(el.querySelector('#insurance-card-front-preview')).toBeNull();
  });

  it('shows the reason when a file cannot be used and keeps the previous photo', async () => {
    const fixture = await mount('back');
    fixture.componentRef.setInput('photo', PHOTO);
    await settle(fixture);
    const el: HTMLElement = fixture.nativeElement;
    fail = 'This image format is not supported by your browser.';
    pick(
      el.querySelector<HTMLInputElement>('#insurance-card-back-file')!,
      new File(['heic'], 'card.heic', { type: 'image/heic' }),
    );
    await settle(fixture);
    expect(text(el, '#insurance-card-back-error')).toContain('not supported');
    expect(fixture.componentInstance.photo()).toEqual(PHOTO);
  });

  it('keeps, removes or replaces a photo already on file when updating', async () => {
    const fixture = await mount();
    fixture.componentRef.setInput('stored', {
      side: 'front',
      content_type: 'image/jpeg',
      size_bytes: 400,
      uploaded_at: '2027-07-15T09:00:00Z',
      url: '/api/v1/patients/7/insurance-card/front',
    });
    await settle(fixture);
    const el: HTMLElement = fixture.nativeElement;
    expect(text(el, '#insurance-card-front-status')).toMatch(/On file · uploaded .*2027/);
    expect(el.querySelector('.card-placeholder.on-file')).not.toBeNull();
    el.querySelector<HTMLButtonElement>('#btn-card-front-remove-file')!.click();
    await settle(fixture);
    expect(fixture.componentInstance.removed()).toBe(true);
    expect(text(el, '#insurance-card-front-status')).toContain('Will be removed');
    el.querySelector<HTMLButtonElement>('#btn-card-front-keep')!.click();
    await settle(fixture);
    expect(fixture.componentInstance.removed()).toBe(false);
    // A new photo replaces it — and cancels a pending removal.
    fixture.componentInstance.removed.set(true);
    pick(
      el.querySelector<HTMLInputElement>('#insurance-card-front-file')!,
      new File(['x'], 'new.jpg', { type: 'image/jpeg' }),
    );
    await settle(fixture);
    expect(fixture.componentInstance.removed()).toBe(false);
    expect(text(el, '#insurance-card-front-status')).toContain('replaces the photo on file');
  });

  it('falls back to the device camera picker when the browser has no in-app camera', async () => {
    // jsdom: no navigator.mediaDevices → the "Take photo" button opens <input capture="environment">.
    expect(InsuranceCardCapture.cameraAvailable()).toBe(false);
    const fixture = await mount();
    const el: HTMLElement = fixture.nativeElement;
    const capture = el.querySelector<HTMLInputElement>('#insurance-card-front-capture')!;
    expect(capture.getAttribute('capture')).toBe('environment');
    expect(capture.getAttribute('accept')).toBe('image/*');
    const clicked = vi.spyOn(capture, 'click');
    el.querySelector<HTMLButtonElement>('#btn-card-front-camera')!.click();
    await settle(fixture);
    expect(clicked).toHaveBeenCalledTimes(1);
    expect(el.querySelector<HTMLDialogElement>('#camera-dialog-front')!.open).toBe(false);

    pick(capture, new File(['shot'], 'shot.jpg', { type: 'image/jpeg' }));
    await settle(fixture);
    expect(prepared).toEqual([{ size: 4, source: 'camera' }]);
    expect(text(el, '#insurance-card-front-status')).toContain('Photo taken');
  });
});
