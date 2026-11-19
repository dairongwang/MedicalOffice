/**
 * Photos of an insurance card, made ready for the registration request.
 *
 * A phone camera produces 3–12 MB images; a card is perfectly readable at ~1600 px on the long
 * edge. `prepareCardPhoto` decodes whatever the patient picked or shot (JPEG, PNG, WebP — and
 * HEIC on devices whose browser can decode it), applies the EXIF orientation, scales it down
 * and re-encodes it as JPEG, so the request stays small (typically 150–400 KB per side) and
 * the server only ever sees JPEG. The result is a data URL, which is what `PatientForm`
 * carries (`insurance_card_front` / `insurance_card_back`).
 *
 * The server's own cap is 5 MB per side (services/account/insurance.py); the page's limit on
 * the *source* file is more generous only because the photo is shrunk before upload.
 */

export interface CardPhoto {
  /** data:image/jpeg;base64,… */
  dataUrl: string;
  width: number;
  height: number;
  /** Size of the encoded image (what the request will carry, before base64). */
  bytes: number;
  /** Where it came from — for the preview's caption. */
  source: 'upload' | 'camera';
}

export class CardPhotoError extends Error {}

/** Long edge of the stored photo: enough to read every line of a card, small to upload. */
export const MAX_EDGE_PX = 1600;
export const JPEG_QUALITY = 0.85;
/** A source file larger than this is refused before decoding (a 50 MB "photo" is not a card). */
export const MAX_SOURCE_BYTES = 25 * 1024 * 1024;
/** What the server accepts once encoded (insurance.MAX_CARD_BYTES). */
export const MAX_UPLOAD_BYTES = 5 * 1024 * 1024;

/** Image types the browser's decoder handles everywhere; HEIC/HEIF is tried (Safari decodes it). */
const ACCEPTED = /^image\/(jpeg|jpg|png|webp|heic|heif|gif|bmp|avif)$/i;

export function isSupportedImageType(type: string): boolean {
  return ACCEPTED.test(type);
}

/** Bytes of the binary payload behind a base64 data URL (without the header). */
export function dataUrlBytes(dataUrl: string): number {
  const comma = dataUrl.indexOf(',');
  if (comma < 0) return 0;
  const b64 = dataUrl.slice(comma + 1);
  const padding = b64.endsWith('==') ? 2 : b64.endsWith('=') ? 1 : 0;
  return Math.max(0, Math.floor((b64.length * 3) / 4) - padding);
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** The size the photo is scaled to: never up, at most MAX_EDGE_PX on the long edge. */
export function fitWithin(
  width: number,
  height: number,
  maxEdge = MAX_EDGE_PX,
): { width: number; height: number } {
  const longest = Math.max(width, height);
  if (longest <= maxEdge) return { width, height };
  const scale = maxEdge / longest;
  return {
    width: Math.max(1, Math.round(width * scale)),
    height: Math.max(1, Math.round(height * scale)),
  };
}

/**
 * Decode, orient, downsize and re-encode a photo as JPEG. Throws `CardPhotoError` with a
 * message fit for the patient when the file is not an image the browser can read.
 */
export async function prepareCardPhoto(
  file: Blob,
  source: CardPhoto['source'],
  opts: { maxEdge?: number; quality?: number } = {},
): Promise<CardPhoto> {
  if (file.size === 0) throw new CardPhotoError('That file is empty. Please choose another photo.');
  if (file.size > MAX_SOURCE_BYTES)
    throw new CardPhotoError('That file is too large. Please choose a photo under 25 MB.');
  if (file.type && !isSupportedImageType(file.type))
    throw new CardPhotoError(
      'Please choose a photo (JPEG, PNG or WebP) — not a document or video.',
    );

  const image = await decode(file);
  try {
    const { width, height } = fitWithin(image.width, image.height, opts.maxEdge ?? MAX_EDGE_PX);
    const canvas = document.createElement('canvas');
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext('2d');
    if (!ctx) throw new CardPhotoError('Your browser could not process the photo.');
    ctx.fillStyle = '#fff'; // transparent PNG → white, not black, once it is a JPEG
    ctx.fillRect(0, 0, width, height);
    ctx.drawImage(image.source, 0, 0, width, height);
    const dataUrl = canvas.toDataURL('image/jpeg', opts.quality ?? JPEG_QUALITY);
    if (!dataUrl.startsWith('data:image/jpeg'))
      throw new CardPhotoError('Your browser could not process the photo.');
    const bytes = dataUrlBytes(dataUrl);
    if (bytes > MAX_UPLOAD_BYTES)
      throw new CardPhotoError(
        'The photo is still too large after resizing. Please try another one.',
      );
    return { dataUrl, width, height, bytes, source };
  } finally {
    image.release();
  }
}

interface Decoded {
  source: CanvasImageSource;
  width: number;
  height: number;
  release(): void;
}

/** `createImageBitmap` (fast, honours EXIF orientation) with an `<img>` fallback. */
async function decode(file: Blob): Promise<Decoded> {
  if (typeof createImageBitmap === 'function') {
    try {
      const bitmap = await createImageBitmap(file, { imageOrientation: 'from-image' });
      return {
        source: bitmap,
        width: bitmap.width,
        height: bitmap.height,
        release: () => bitmap.close(),
      };
    } catch {
      /* unsupported format or options — try the image element */
    }
  }
  const url = URL.createObjectURL(file);
  try {
    const img = await new Promise<HTMLImageElement>((resolve, reject) => {
      const el = new Image();
      el.onload = () => resolve(el);
      el.onerror = () => reject(new Error('decode failed'));
      el.src = url;
    });
    if (!img.naturalWidth || !img.naturalHeight) throw new Error('decode failed');
    return {
      source: img,
      width: img.naturalWidth,
      height: img.naturalHeight,
      release: () => URL.revokeObjectURL(url),
    };
  } catch {
    URL.revokeObjectURL(url);
    throw new CardPhotoError(
      'This image format is not supported by your browser. Please use a JPEG or PNG photo, or take the photo with the camera here.',
    );
  }
}
