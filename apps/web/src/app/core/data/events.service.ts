import { Injectable, computed, effect, inject, signal, untracked } from '@angular/core';
import type { CalendarEvent } from '../db/models';
import { newId, nowIso } from '../db/models';
import { StoreService } from './store.service';

export interface EventInput {
  title: string;
  startAt: string;
  endAt: string;
  location: string | null;
  description: string | null;
}

/** Calendar events on this device, kept in step with the local store (see TasksService). */
@Injectable({ providedIn: 'root' })
export class EventsService {
  private readonly store = inject(StoreService);

  readonly all = signal<CalendarEvent[]>([]);
  readonly loaded = signal(false);
  readonly sorted = computed(() =>
    [...this.all()].sort((a, b) => a.startAt.localeCompare(b.startAt)),
  );

  constructor() {
    effect(() => {
      this.store.changed();
      untracked(() => void this.reload());
    });
  }

  async reload(): Promise<void> {
    this.all.set(await this.store.allEvents());
    this.loaded.set(true);
  }

  async create(input: EventInput): Promise<CalendarEvent> {
    const now = nowIso();
    const event: CalendarEvent = {
      id: newId(),
      ...normalize(input),
      createdAt: now,
      updatedAt: now,
      seq: 0,
    };
    await this.store.putEvent(event);
    return event;
  }

  async update(event: CalendarEvent, input: EventInput): Promise<CalendarEvent> {
    const next: CalendarEvent = { ...event, ...normalize(input), updatedAt: nowIso() };
    await this.store.putEvent(next);
    return next;
  }

  async remove(event: CalendarEvent): Promise<void> {
    await this.store.deleteEvent(event.id);
  }
}

function normalize(input: EventInput) {
  const title = input.title.trim();
  if (!title) throw new Error('Please give the event a title.');
  const start = new Date(input.startAt);
  const end = new Date(input.endAt);
  if (isNaN(+start) || isNaN(+end)) throw new Error('Please pick a start and an end time.');
  if (end < start) throw new Error('The event cannot end before it starts.');
  return {
    title,
    startAt: start.toISOString(),
    endAt: end.toISOString(),
    location: input.location?.trim() || null,
    description: input.description?.trim() || null,
  };
}
