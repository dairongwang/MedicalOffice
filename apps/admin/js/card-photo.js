import { button, h, render } from "./ui.js";

/**
 * Insurance card photos for the office administration dashboard — the same behaviour as the
 * patients' registration page (apps/web: core/patients/card-photo.ts and
 * features/patients/insurance-card-capture.ts), without a framework:
 *
 *   prepareCardPhoto(file)  decode (EXIF orientation applied), scale to ≤ 1600 px, re-encode as
 *                           JPEG → { dataUrl, width, height, bytes }. The API accepts JPEG / PNG /
 *                           WebP up to 5 MB; this keeps a phone photo at ~150–400 KB.
 *   cardCapture({ side, stored })  one side of the card: preview, Take photo (the camera inside
 *                           the dashboard — getUserMedia with a card-shaped guide; falls back to
 *                           the device's camera app / file picker), Upload photo, Remove. With a
 *                           photo on file (`stored`): keep / replace / remove from file.
 */

export const MAX_EDGE_PX = 1600;
export const JPEG_QUALITY = 0.85;
export const MAX_SOURCE_BYTES = 25 * 1024 * 1024;
export const MAX_UPLOAD_BYTES = 5 * 1024 * 1024;
const ACCEPTED = /^image\/(jpeg|jpg|png|webp|heic|heif|gif|bmp|avif)$/i;

export class CardPhotoError extends Error {}

export function dataUrlBytes(dataUrl) {
  const comma = dataUrl.indexOf(",");
  if (comma < 0) return 0;
  const b64 = dataUrl.slice(comma + 1);
  const padding = b64.endsWith("==") ? 2 : b64.endsWith("=") ? 1 : 0;
  return Math.max(0, Math.floor((b64.length * 3) / 4) - padding);
}

export function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function fitWithin(width, height, maxEdge = MAX_EDGE_PX) {
  const longest = Math.max(width, height);
  if (longest <= maxEdge) return { width, height };
  const scale = maxEdge / longest;
  return { width: Math.max(1, Math.round(width * scale)), height: Math.max(1, Math.round(height * scale)) };
}

async function decode(file) {
  if (typeof createImageBitmap === "function") {
    try {
      const bitmap = await createImageBitmap(file, { imageOrientation: "from-image" });
      return { source: bitmap, width: bitmap.width, height: bitmap.height, release: () => bitmap.close() };
    } catch {
      /* unsupported — try an image element */
    }
  }
  const url = URL.createObjectURL(file);
  try {
    const img = await new Promise((resolve, reject) => {
      const el = new Image();
      el.onload = () => resolve(el);
      el.onerror = () => reject(new Error("decode failed"));
      el.src = url;
    });
    if (!img.naturalWidth) throw new Error("decode failed");
    return { source: img, width: img.naturalWidth, height: img.naturalHeight, release: () => URL.revokeObjectURL(url) };
  } catch {
    URL.revokeObjectURL(url);
    throw new CardPhotoError(
      "This image format is not supported by the browser. Use a JPEG or PNG, or take the photo here.",
    );
  }
}

export async function prepareCardPhoto(file, source = "upload") {
  if (!file || file.size === 0) throw new CardPhotoError("That file is empty. Please choose another photo.");
  if (file.size > MAX_SOURCE_BYTES)
    throw new CardPhotoError("That file is too large. Please choose a photo under 25 MB.");
  if (file.type && !ACCEPTED.test(file.type))
    throw new CardPhotoError("Please choose a photo (JPEG, PNG or WebP) — not a document or video.");
  const image = await decode(file);
  try {
    const { width, height } = fitWithin(image.width, image.height);
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new CardPhotoError("The browser could not process the photo.");
    ctx.fillStyle = "#fff";
    ctx.fillRect(0, 0, width, height);
    ctx.drawImage(image.source, 0, 0, width, height);
    const dataUrl = canvas.toDataURL("image/jpeg", JPEG_QUALITY);
    if (!dataUrl.startsWith("data:image/jpeg")) throw new CardPhotoError("The browser could not process the photo.");
    const bytes = dataUrlBytes(dataUrl);
    if (bytes > MAX_UPLOAD_BYTES) throw new CardPhotoError("The photo is still too large after resizing.");
    return { dataUrl, width, height, bytes, source };
  } finally {
    image.release();
  }
}

export function cameraAvailable() {
  return !!navigator.mediaDevices?.getUserMedia && window.isSecureContext !== false;
}

function describeCameraError(e) {
  switch (e?.name) {
    case "NotAllowedError":
    case "SecurityError":
      return "Camera access was not allowed. Allow it in the browser, or use the device's camera app instead.";
    case "NotFoundError":
    case "OverconstrainedError":
      return "No camera was found on this device.";
    case "NotReadableError":
      return "The camera is in use by another app.";
    default:
      return e?.message || "The camera is not available on this device.";
  }
}

/**
 * The in-dashboard camera: a <dialog> with the live view, a card-shaped guide, Capture and
 * Switch camera. Resolves with a Blob (JPEG), null when canceled, or rejects with the reason
 * the camera could not start (the caller then falls back to the device picker).
 */
export function takePhotoWithCamera(title) {
  return new Promise((resolve, reject) => {
    let stream = null;
    let facing = "environment";
    const video = h("video", { autoplay: true, playsinline: true, muted: true });
    const stage = h(
      "div",
      { class: "camera-stage" },
      video,
      h("div", { class: "camera-guide", "aria-hidden": "true" }),
    );
    const status = h("p", { class: "muted small" }, "Starting the camera…");
    const capture = button("📷 Capture", { class: "btn primary", id: "btn-camera-capture", disabled: true });
    const switchBtn = button("⟲ Switch camera", { class: "btn", id: "btn-camera-switch", hidden: true });
    const cancel = button("Cancel", { class: "btn", id: "btn-camera-cancel" });
    const dialog = h(
      "dialog",
      { class: "camera-dialog", "aria-label": title },
      h(
        "div",
        { class: "camera" },
        h("h3", {}, title),
        stage,
        status,
        h("div", { class: "actions" }, cancel, switchBtn, h("span", { class: "spacer" }), capture),
      ),
    );
    const stop = () => {
      if (stream) for (const t of stream.getTracks()) t.stop();
      stream = null;
      video.srcObject = null;
    };
    const finish = (value, error) => {
      stop();
      if (dialog.open) dialog.close();
      dialog.remove();
      error ? reject(error) : resolve(value);
    };
    const start = async () => {
      stop();
      capture.disabled = true;
      stage.classList.remove("ready");
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          audio: false,
          video: { facingMode: { ideal: facing }, width: { ideal: 1920 }, height: { ideal: 1080 } },
        });
        video.srcObject = stream;
        await video.play().catch(() => undefined);
        stage.classList.add("ready");
        capture.disabled = false;
        status.textContent = "Lay the card flat, fill the frame with it, avoid reflections, then press Capture.";
        try {
          const devices = await navigator.mediaDevices.enumerateDevices();
          switchBtn.hidden = devices.filter((d) => d.kind === "videoinput").length < 2;
        } catch {
          switchBtn.hidden = true;
        }
      } catch (e) {
        finish(null, new CardPhotoError(describeCameraError(e)));
      }
    };
    cancel.addEventListener("click", () => finish(null));
    dialog.addEventListener("click", (e) => e.target === dialog && finish(null));
    dialog.addEventListener("close", () => finish(null));
    switchBtn.addEventListener("click", () => {
      facing = facing === "environment" ? "user" : "environment";
      void start();
    });
    capture.addEventListener("click", () => {
      if (!video.videoWidth) return;
      const canvas = document.createElement("canvas");
      canvas.width = video.videoWidth;
      canvas.height = video.videoHeight;
      canvas.getContext("2d").drawImage(video, 0, 0);
      canvas.toBlob((blob) => finish(blob || null), "image/jpeg", 0.92);
    });
    document.body.append(dialog);
    dialog.showModal();
    void start();
  });
}

/**
 * One side of the insurance card in the edit form. Returns { el, photo(), removed() }:
 * `photo()` is the new photo's data URL (or null = nothing new), `removed()` whether the photo
 * on file should be dropped.
 */
export function cardCapture({ side, stored = null }) {
  let photo = null;
  let removed = false;
  let busy = false;
  const title = side === "front" ? "Front of the card" : "Back of the card";
  const status = h("span", { class: "muted small", id: `card-${side}-status` });
  const preview = h("div", { class: "card-preview-slot" });
  const error = h("p", { class: "small error-text", id: `card-${side}-error`, hidden: true });
  const actions = h("div", { class: "card-capture-actions" });
  const fileInput = h("input", { type: "file", accept: "image/*", hidden: true, id: `card-${side}-file` });
  const captureInput = h("input", {
    type: "file",
    accept: "image/*",
    capture: "environment",
    hidden: true,
    id: `card-${side}-capture`,
  });
  const el = h(
    "div",
    { class: "card-capture", id: `card-${side}`, dataset: { side } },
    h("div", { class: "card-capture-head" }, h("strong", {}, title), status),
    preview,
    error,
    actions,
    fileInput,
    captureInput,
  );
  const fail = (message) => {
    error.textContent = message;
    error.hidden = false;
  };
  const accept = async (file, source) => {
    busy = true;
    error.hidden = true;
    status.textContent = "Preparing the photo…";
    try {
      photo = await prepareCardPhoto(file, source);
      removed = false;
    } catch (e) {
      fail(e instanceof CardPhotoError ? e.message : "The photo could not be read. Please try another one.");
    } finally {
      busy = false;
      paint();
    }
  };
  const takePhoto = async () => {
    error.hidden = true;
    if (!cameraAvailable()) return captureInput.click();
    try {
      const blob = await takePhotoWithCamera(`${title} — take a photo`);
      if (blob) await accept(blob, "camera");
    } catch (e) {
      fail(`${e.message} Opening the device's camera app / file picker instead.`);
      captureInput.click();
    }
  };
  for (const [input, source] of [
    [fileInput, "upload"],
    [captureInput, "camera"],
  ]) {
    input.addEventListener("change", () => {
      const file = input.files?.[0];
      input.value = "";
      if (file) void accept(file, source);
    });
  }
  function paint() {
    el.classList.toggle("has-photo", !!photo);
    const kept = !photo && !removed ? stored : null;
    if (photo) {
      status.textContent = `${photo.source === "camera" ? "Photo taken" : "Photo uploaded"} · ${photo.width}×${photo.height} · ${formatBytes(photo.bytes)}${stored ? " · replaces the photo on file" : ""}`;
      preview.replaceChildren(h("img", { class: "card-preview", src: photo.dataUrl, alt: `${side} of the card` }));
    } else if (kept) {
      status.textContent = `On file · uploaded ${new Date(kept.uploaded_at).toLocaleDateString()}`;
      preview.replaceChildren(
        h(
          "div",
          { class: "card-placeholder on-file" },
          h("span", { class: "card-placeholder-icon" }, "✓"),
          h("span", {}, "Photo on file (shown on the record)"),
        ),
      );
    } else {
      status.textContent = removed ? "Will be removed when you save" : "No photo";
      preview.replaceChildren(
        h(
          "div",
          { class: "card-placeholder" },
          h("span", { class: "card-placeholder-icon" }, "🪪"),
          h("span", {}, title),
        ),
      );
    }
    render(
      actions,
      button(photo ? "📷 Retake" : "📷 Take photo", {
        class: "btn primary",
        id: `btn-card-${side}-camera`,
        disabled: busy,
        onclick: takePhoto,
      }),
      button(photo ? "⬆ Replace" : "⬆ Upload photo", {
        class: "btn",
        id: `btn-card-${side}-upload`,
        disabled: busy,
        onclick: () => fileInput.click(),
      }),
      photo
        ? button("Remove", {
            class: "btn danger",
            id: `btn-card-${side}-remove`,
            onclick: () => {
              photo = null;
              paint();
            },
          })
        : kept
          ? button("Remove from file", {
              class: "btn danger",
              id: `btn-card-${side}-remove-file`,
              onclick: () => {
                removed = true;
                paint();
              },
            })
          : removed
            ? button("Keep it", {
                class: "btn",
                id: `btn-card-${side}-keep`,
                onclick: () => {
                  removed = false;
                  paint();
                },
              })
            : null,
    );
  }
  paint();
  return { el, photo: () => photo?.dataUrl ?? null, removed: () => removed && !photo };
}
