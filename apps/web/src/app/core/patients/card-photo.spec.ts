import {
  CardPhotoError,
  MAX_EDGE_PX,
  MAX_SOURCE_BYTES,
  dataUrlBytes,
  fitWithin,
  formatBytes,
  isSupportedImageType,
  prepareCardPhoto,
} from './card-photo';

describe('card-photo helpers', () => {
  it('accepts the image types a browser can decode and nothing else', () => {
    expect(isSupportedImageType('image/jpeg')).toBe(true);
    expect(isSupportedImageType('image/png')).toBe(true);
    expect(isSupportedImageType('image/webp')).toBe(true);
    expect(isSupportedImageType('image/heic')).toBe(true);
    expect(isSupportedImageType('application/pdf')).toBe(false);
    expect(isSupportedImageType('video/mp4')).toBe(false);
  });

  it('measures the payload behind a data URL', () => {
    expect(dataUrlBytes('data:image/jpeg;base64,' + btoa('abc'))).toBe(3);
    expect(dataUrlBytes('data:image/jpeg;base64,' + btoa('abcd'))).toBe(4);
    expect(dataUrlBytes('data:image/jpeg;base64,' + btoa('abcde'))).toBe(5);
    expect(dataUrlBytes('nonsense')).toBe(0);
  });

  it('formats sizes for the preview caption', () => {
    expect(formatBytes(512)).toBe('512 B');
    expect(formatBytes(200 * 1024)).toBe('200 KB');
    expect(formatBytes(2.5 * 1024 * 1024)).toBe('2.5 MB');
  });

  it('scales a photo down to the long edge, never up, keeping the proportions', () => {
    expect(fitWithin(4000, 3000)).toEqual({ width: MAX_EDGE_PX, height: 1200 });
    expect(fitWithin(3000, 4000)).toEqual({ width: 1200, height: MAX_EDGE_PX });
    expect(fitWithin(800, 500)).toEqual({ width: 800, height: 500 });
    expect(fitWithin(MAX_EDGE_PX, 1000)).toEqual({ width: MAX_EDGE_PX, height: 1000 });
  });

  it('refuses empty, oversized and non-image files before decoding', async () => {
    await expect(prepareCardPhoto(new Blob([], { type: 'image/jpeg' }), 'upload')).rejects.toThrow(
      CardPhotoError,
    );
    const huge = { size: MAX_SOURCE_BYTES + 1, type: 'image/jpeg' } as Blob;
    await expect(prepareCardPhoto(huge, 'upload')).rejects.toThrow(/too large/);
    await expect(
      prepareCardPhoto(new Blob(['%PDF-1.4'], { type: 'application/pdf' }), 'upload'),
    ).rejects.toThrow(/JPEG, PNG or WebP/);
  });
});
