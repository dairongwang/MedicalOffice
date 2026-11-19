import {
  AfterViewInit,
  Component,
  ElementRef,
  input,
  output,
  signal,
  viewChild,
} from '@angular/core';

/**
 * A signature pad: draw with a finger, stylus or mouse (pointer events). Emits `signed` with the
 * drawing as a PNG data URL after every stroke, or `null` when cleared. White background and a
 * dotted baseline, like the staff dashboard's pad (apps/staff/js/views/signature-pad.js).
 */
@Component({
  selector: 'app-signature-pad',
  template: `
    <div class="sig-pad" [class.signed]="hasInk()">
      <div class="sig-wrap">
        <canvas
          #canvas
          class="sig-canvas"
          [id]="padId()"
          [attr.width]="width"
          [attr.height]="height"
          role="img"
          [attr.aria-label]="label()"
          (pointerdown)="down($event)"
          (pointermove)="move($event)"
          (pointerup)="up()"
          (pointercancel)="up()"
          (pointerleave)="up()"
        ></canvas>
        @if (!hasInk()) {
          <span class="sig-hint" aria-hidden="true">Sign here with your finger or mouse</span>
        }
      </div>
      <button class="btn small sig-clear" type="button" [id]="padId() + '-clear'" (click)="clear()">
        Clear
      </button>
    </div>
  `,
})
export class SignaturePad implements AfterViewInit {
  readonly padId = input('signature');
  readonly label = input('Signature area — draw your signature');
  readonly signed = output<string | null>();

  readonly width = 520;
  readonly height = 160;
  readonly hasInk = signal(false);
  private readonly canvas = viewChild.required<ElementRef<HTMLCanvasElement>>('canvas');
  private ctx: CanvasRenderingContext2D | null = null;
  private drawing = false;

  ngAfterViewInit(): void {
    try {
      this.ctx = this.canvas().nativeElement.getContext('2d');
    } catch {
      this.ctx = null; // no canvas support (e.g. a test DOM) — strokes still count
    }
    this.paintBackground();
  }

  clear(): void {
    this.paintBackground();
    this.hasInk.set(false);
    this.signed.emit(null);
  }

  down(e: PointerEvent): void {
    e.preventDefault();
    const el = this.canvas().nativeElement;
    try {
      el.setPointerCapture(e.pointerId);
    } catch {
      /* synthetic events */
    }
    this.drawing = true;
    const p = this.point(e);
    this.ctx?.beginPath();
    this.ctx?.moveTo(p.x, p.y);
    this.ctx?.lineTo(p.x + 0.1, p.y + 0.1);
    this.ctx?.stroke();
  }

  move(e: PointerEvent): void {
    if (!this.drawing) return;
    e.preventDefault();
    const p = this.point(e);
    this.ctx?.lineTo(p.x, p.y);
    this.ctx?.stroke();
  }

  up(): void {
    if (!this.drawing) return;
    this.drawing = false;
    this.hasInk.set(true);
    this.signed.emit(this.dataUrl());
  }

  private dataUrl(): string | null {
    try {
      const url = this.canvas().nativeElement.toDataURL('image/png');
      return url && url.startsWith('data:image/png') ? url : null;
    } catch {
      return null;
    }
  }

  private point(e: PointerEvent): { x: number; y: number } {
    const el = this.canvas().nativeElement;
    const r = el.getBoundingClientRect();
    return {
      x: r.width ? ((e.clientX - r.left) / r.width) * this.width : 0,
      y: r.height ? ((e.clientY - r.top) / r.height) * this.height : 0,
    };
  }

  private paintBackground(): void {
    const ctx = this.ctx;
    if (!ctx) return;
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, this.width, this.height);
    ctx.save();
    ctx.strokeStyle = '#d1d5db';
    ctx.setLineDash([4, 6]);
    ctx.beginPath();
    ctx.moveTo(24, this.height - 36);
    ctx.lineTo(this.width - 24, this.height - 36);
    ctx.stroke();
    ctx.restore();
    ctx.strokeStyle = '#111827';
    ctx.lineWidth = 2.4;
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
  }
}
