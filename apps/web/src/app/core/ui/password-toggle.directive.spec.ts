import { Component } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { PasswordToggleDirective } from './password-toggle.directive';

@Component({
  imports: [PasswordToggleDirective],
  template: `<div class="field"><input id="pw" type="password" appPasswordToggle /></div>`,
})
class Host {}

describe('PasswordToggleDirective', () => {
  it('adds a Show / Hide button that reveals what was typed', () => {
    const fixture = TestBed.createComponent(Host);
    fixture.detectChanges();
    const el: HTMLElement = fixture.nativeElement;
    const input = el.querySelector<HTMLInputElement>('#pw')!;
    const button = el.querySelector<HTMLButtonElement>('.password-field > .password-toggle')!;
    expect(input.parentElement?.className).toBe('password-field');
    expect(input.type).toBe('password');
    expect(button.textContent).toBe('Show');
    expect(button.getAttribute('aria-pressed')).toBe('false');
    button.click();
    expect(input.type).toBe('text');
    expect(button.textContent).toBe('Hide');
    expect(button.getAttribute('aria-pressed')).toBe('true');
    expect(button.getAttribute('aria-label')).toBe('Hide password');
    button.click();
    expect(input.type).toBe('password');
  });
});
