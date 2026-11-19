import { button, h } from "../ui.js";

/**
 * A small signature pad (canvas, pointer events → works with finger, stylus and mouse).
 * Returns { el, isEmpty(), toDataUrl(), clear() }. The PNG has a white background so it reads
 * well in any document viewer.
 */
export function signaturePad({ width = 520, height = 180 } = {}) {
  const canvas = h("canvas", {
    class: "sig-canvas",
    width,
    height,
    "aria-label": "Signature area — draw your signature",
  });
  const ctx = canvas.getContext("2d");
  let strokes = 0;
  let drawing = false;

  function reset() {
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.strokeStyle = "#111827";
    ctx.lineWidth = 2.4;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    // baseline
    ctx.save();
    ctx.strokeStyle = "#d1d5db";
    ctx.setLineDash([4, 6]);
    ctx.beginPath();
    ctx.moveTo(24, canvas.height - 40);
    ctx.lineTo(canvas.width - 24, canvas.height - 40);
    ctx.stroke();
    ctx.restore();
    strokes = 0;
    hint.hidden = false;
  }

  function point(e) {
    const r = canvas.getBoundingClientRect();
    return {
      x: ((e.clientX - r.left) / r.width) * canvas.width,
      y: ((e.clientY - r.top) / r.height) * canvas.height,
    };
  }

  canvas.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    canvas.setPointerCapture(e.pointerId);
    drawing = true;
    const p = point(e);
    ctx.beginPath();
    ctx.moveTo(p.x, p.y);
    ctx.lineTo(p.x + 0.1, p.y + 0.1);
    ctx.stroke();
    strokes += 1;
    hint.hidden = true;
  });
  canvas.addEventListener("pointermove", (e) => {
    if (!drawing) return;
    e.preventDefault();
    const p = point(e);
    ctx.lineTo(p.x, p.y);
    ctx.stroke();
  });
  const stop = () => {
    drawing = false;
  };
  canvas.addEventListener("pointerup", stop);
  canvas.addEventListener("pointercancel", stop);
  canvas.addEventListener("pointerleave", stop);

  const hint = h("span", { class: "sig-hint" }, "Sign here with your finger or mouse");
  const clearBtn = button("Clear", { class: "btn ghost small-btn", onclick: reset });
  const el = h(
    "div",
    { class: "sig-pad" },
    h("div", { class: "sig-wrap" }, canvas, hint),
    h("div", { class: "sig-tools" }, clearBtn),
  );
  reset();
  return {
    el,
    isEmpty: () => strokes === 0,
    toDataUrl: () => canvas.toDataURL("image/png"),
    clear: reset,
  };
}
