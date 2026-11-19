import { Injectable, computed, inject, signal } from '@angular/core';
import type { CalendarEvent, Task } from '../db/models';
import { sameDay } from '../ui/format';
import { EventsService } from './events.service';
import { TasksService } from './tasks.service';

export interface TodaySummary {
  /** Tasks not done (all dates). */
  totalOpen: number;
  /** Open tasks due today (local time). */
  dueToday: Task[];
  /** Open tasks whose due date has passed. */
  overdue: Task[];
  /** Tasks marked done today. */
  completedToday: number;
  /** Today's events (also those already running), soonest first. */
  upcomingEvents: CalendarEvent[];
}

/** The dashboard's daily summary, derived from the task and event lists; re-evaluated every minute. */
@Injectable({ providedIn: 'root' })
export class TodayService {
  private readonly tasks = inject(TasksService);
  private readonly events = inject(EventsService);

  /** Ticks once a minute so "today"/"overdue" stay right while the app is open. */
  private readonly clock = signal(Date.now());

  readonly summary = computed<TodaySummary>(() => {
    const now = new Date(this.clock());
    const open = this.tasks.all().filter((t) => t.status !== 'done');
    const overdue = open
      .filter((t) => t.dueAt && new Date(t.dueAt) < now)
      .sort((a, b) => a.dueAt!.localeCompare(b.dueAt!));
    const dueToday = open
      .filter((t) => t.dueAt && sameDay(new Date(t.dueAt), now))
      .sort((a, b) => a.dueAt!.localeCompare(b.dueAt!));
    const completedToday = this.tasks
      .all()
      .filter(
        (t) => t.status === 'done' && sameDay(new Date(t.completedAt ?? t.updatedAt), now),
      ).length;
    const upcomingEvents = this.events.sorted().filter((e) => {
      const start = new Date(e.startAt);
      const end = new Date(e.endAt);
      return sameDay(start, now) || (start <= now && end >= now);
    });
    return { totalOpen: open.length, dueToday, overdue, completedToday, upcomingEvents };
  });

  constructor() {
    setInterval(() => this.clock.set(Date.now()), 60_000);
  }
}
