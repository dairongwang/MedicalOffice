import { Injectable, computed, effect, inject, signal, untracked } from '@angular/core';
import type { Priority, Task, TaskStatus } from '../db/models';
import { PRIORITY_RANK, newId, nowIso } from '../db/models';
import { MembershipService } from '../membership/membership.service';
import { StoreService } from './store.service';

export interface TaskInput {
  title: string;
  description: string | null;
  priority: Priority;
  status: TaskStatus;
  dueAt: string | null;
  tags: string[];
}

/** Saving would open one more active task than the membership allows. */
export class TaskLimitError extends Error {
  constructor(
    message: string,
    readonly limit: number | null,
  ) {
    super(message);
    this.name = 'TaskLimitError';
  }
}

/** "Active" = not done (what the dashboard calls *open*; what membership limits count). */
export function isActive(task: { status: TaskStatus }): boolean {
  return task.status !== 'done';
}

/**
 * Tasks on this device. The list is a signal kept in step with the local store (which also
 * changes through sync); filtering/sorting are computed from it. Membership limits are enforced
 * here for what the user does — never for data arriving through sync.
 */
@Injectable({ providedIn: 'root' })
export class TasksService {
  private readonly store = inject(StoreService);
  private readonly membership = inject(MembershipService);

  readonly all = signal<Task[]>([]);
  readonly loaded = signal(false);
  readonly search = signal('');
  readonly filterStatus = signal<TaskStatus | ''>('');

  readonly activeCount = computed(() => this.all().filter(isActive).length);
  /** May one more task be opened right now? (limit, remaining, reason) */
  readonly limit = computed(() => this.membership.strategy().checkActivateTask(this.activeCount()));
  readonly usageLabel = computed(() => this.membership.strategy().usageLabel(this.activeCount()));

  readonly visible = computed(() => {
    const q = this.search().trim().toLowerCase();
    const status = this.filterStatus();
    return this.all()
      .filter((t) => !status || t.status === status)
      .filter(
        (t) =>
          !q ||
          t.title.toLowerCase().includes(q) ||
          (t.description ?? '').toLowerCase().includes(q),
      )
      .sort(compareTasks);
  });

  constructor() {
    effect(() => {
      this.store.changed();
      untracked(() => void this.reload());
    });
  }

  async reload(): Promise<void> {
    this.all.set(await this.store.allTasks());
    this.loaded.set(true);
  }

  /** Throws TaskLimitError when opening one more task is not allowed for this membership. */
  assertCanActivate(): void {
    const check = this.limit();
    if (!check.allowed) {
      throw new TaskLimitError(
        check.reason ?? `You have reached the limit of ${check.limit} active tasks.`,
        check.limit,
      );
    }
  }

  async create(input: TaskInput): Promise<Task> {
    if (isActive(input)) this.assertCanActivate();
    const now = nowIso();
    const task: Task = {
      id: newId(),
      ...normalize(input),
      completedAt: input.status === 'done' ? now : null,
      createdAt: now,
      updatedAt: now,
      seq: 0,
    };
    await this.store.putTask(task);
    return task;
  }

  async update(task: Task, input: TaskInput): Promise<Task> {
    if (!isActive(task) && isActive(input)) this.assertCanActivate(); // re-opening a done task
    const now = nowIso();
    const next: Task = {
      ...task,
      ...normalize(input),
      completedAt:
        input.status === 'done' ? (task.status === 'done' ? task.completedAt : now) : null,
      updatedAt: now,
    };
    await this.store.putTask(next);
    return next;
  }

  async toggleDone(task: Task): Promise<Task> {
    const status: TaskStatus = task.status === 'done' ? 'todo' : 'done';
    return this.update(task, { ...task, status });
  }

  async remove(task: Task): Promise<void> {
    await this.store.deleteTask(task.id);
  }
}

function normalize(input: TaskInput) {
  return {
    title: input.title.trim(),
    description: input.description?.trim() || null,
    priority: input.priority,
    status: input.status,
    dueAt: input.dueAt || null,
    tags: Array.from(new Set(input.tags.map((t) => t.trim()).filter(Boolean))),
  };
}

/** Open before done; then urgency, then the nearest due date, then the most recently changed. */
function compareTasks(a: Task, b: Task): number {
  const doneA = a.status === 'done' ? 1 : 0;
  const doneB = b.status === 'done' ? 1 : 0;
  if (doneA !== doneB) return doneA - doneB;
  if (doneA) return b.updatedAt.localeCompare(a.updatedAt);
  const rank = PRIORITY_RANK[b.priority] - PRIORITY_RANK[a.priority];
  if (rank) return rank;
  if (a.dueAt && b.dueAt && a.dueAt !== b.dueAt) return a.dueAt.localeCompare(b.dueAt);
  if (!!a.dueAt !== !!b.dueAt) return a.dueAt ? -1 : 1;
  return b.updatedAt.localeCompare(a.updatedAt);
}
