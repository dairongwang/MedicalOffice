import { Directive, ElementRef, OnInit, Renderer2, inject, signal } from '@angular/core';

/**
 * `<input type="password" appPasswordToggle>` — adds a Show / Hide button to the field so the
 * user can see what they typed (a typo in a password you cannot see is the most common reason a
 * sign-in or a registration fails). The input is wrapped in `.password-field`; the button
 * toggles `type` between password and text and announces its state (`aria-pressed`).
 */
@Directive({ selector: 'input[appPasswordToggle]' })
export class PasswordToggleDirective implements OnInit {
  private readonly el = inject<ElementRef<HTMLInputElement>>(ElementRef);
  private readonly renderer = inject(Renderer2);
  readonly visible = signal(false);
  private button: HTMLButtonElement | null = null;

  ngOnInit(): void {
    const input = this.el.nativeElement;
    const parent = input.parentElement;
    if (!parent) return;
    const wrap = this.renderer.createElement('span') as HTMLSpanElement;
    wrap.className = 'password-field';
    this.renderer.insertBefore(parent, wrap, input);
    this.renderer.appendChild(wrap, input);
    const button = this.renderer.createElement('button') as HTMLButtonElement;
    button.type = 'button';
    button.className = 'password-toggle';
    button.tabIndex = 0;
    button.setAttribute(
      'data-tip',
      'Show or hide the password you typed. Nothing is sent until you submit.',
    );
    this.renderer.listen(button, 'click', () => this.toggle());
    this.renderer.appendChild(wrap, button);
    this.button = button;
    this.render();
  }

  toggle(): void {
    this.visible.update((v) => !v);
    this.render();
    this.el.nativeElement.focus();
  }

  private render(): void {
    const on = this.visible();
    this.el.nativeElement.type = on ? 'text' : 'password';
    if (this.button) {
      this.button.textContent = on ? 'Hide' : 'Show';
      this.button.setAttribute('aria-pressed', String(on));
      this.button.setAttribute('aria-label', on ? 'Hide password' : 'Show password');
    }
  }
}
