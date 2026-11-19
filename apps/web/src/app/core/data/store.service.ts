import { Injectable, inject, signal } from '@angular/core';
import { DbService } from '../db/db.service';
import { type AnyOp, SCHEMA_VERSION, upcastOp } from '../db/migrations';
import type { CalendarEvent, Entity, Op, OutboxEntry, Task } from '../db/models';
import { newId, nowIso } from '../db/models';

/** The whole local data set — what the encrypted vault snapshot contains. */
export interface Snapshot {
  schema: number;
  exportedAt: string;
  tasks: Task[];
  events: CalendarEvent[];
}

/**
 * The device-local store: every change to a task or event is written to its table *and* recorded
 * as an op in the outbox (one IndexedDB transaction), so SyncService can replay it to the server
 * later. Remote ops arrive through `applyRemote` in the server's sequence order — the server is
 * the master of ordering, so the last op per row simply wins.
 *
 * Nothing here knows about accounts or encryption; it is the same with and without sync.
 */
@Injectable({ providedIn: 'root' })
export class StoreService {
  private readonly dbs = inject(DbService);

  /** Bumped whenever tasks/events changed (locally or through sync) — the data services reload. */
  readonly changed = signal(0);
  /** Bumped whenever something was added to the outbox — SyncService schedules a push. */
  readonly outboxChanged = signal(0);

  private get db() {
    return this.dbs.db;
  }

  // ---------------------------------------------------------------- reading

  allTasks(): Promise<Task[]> {
    return this.db.tasks.toArray();
  }

  allEvents(): Promise<CalendarEvent[]> {
    return this.db.events.toArray();
  }

  // ---------------------------------------------------------------- local mutations (→ outbox)

  async putTask(task: Task): Promise<void> {
    await this.write('task', task.id, 'put', task);
  }

  async deleteTask(id: string): Promise<void> {
    await this.write('task', id, 'delete', null);
  }

  async putEvent(event: CalendarEvent): Promise<void> {
    await this.write('event', event.id, 'put', event);
  }

  async deleteEvent(id: string): Promise<void> {
    await this.write('event', id, 'delete', null);
  }

  private async write(
    entity: Entity,
    entityId: string,
    kind: 'put' | 'delete',
    data: Task | CalendarEvent | null,
  ): Promise<void> {
    const op: Op = {
      opId: newId(),
      schema: SCHEMA_VERSION,
      entity,
      entityId,
      kind,
      data,
      ts: nowIso(),
    };
    const db = this.db;
    await db.transaction('rw', db.tasks, db.events, db.outbox, async () => {
      await this.applyOp(op);
      await db.outbox.add({ op });
    });
    this.changed.update((n) => n + 1);
    this.outboxChanged.update((n) => n + 1);
  }

  /** Write one op into the tables (no outbox). `seq` = server sequence, 0 for local-only. */
  private async applyOp(op: Op, seq = 0): Promise<void> {
    const table = op.entity === 'task' ? this.db.tasks : this.db.events;
    if (op.kind === 'delete' || !op.data) {
      await table.delete(op.entityId);
    } else {
      // Cast: the table is chosen by op.entity, so the row shape matches.
      await (table as typeof this.db.tasks).put({ ...(op.data as Task), seq });
    }
  }

  // ---------------------------------------------------------------- outbox (used by SyncService)

  outboxSize(): Promise<number> {
    return this.db.outbox.count();
  }

  pendingOps(limit: number): Promise<OutboxEntry[]> {
    return this.db.outbox.orderBy('localSeq').limit(limit).toArray();
  }

  /** Queue ops without touching the tables (first-sync adoption of existing local data). */
  async enqueue(ops: Op[]): Promise<void> {
    await this.db.outbox.bulkAdd(ops.map((op) => ({ op })));
    this.outboxChanged.update((n) => n + 1);
  }

  /** A `put` op for every local row — what a fresh account inherits from this device. */
  async opsForAllLocalData(): Promise<Op[]> {
    const [tasks, events] = await Promise.all([this.allTasks(), this.allEvents()]);
    const ts = nowIso();
    return [
      ...tasks.map<Op>((t) => ({
        opId: newId(),
        schema: SCHEMA_VERSION,
        entity: 'task',
        entityId: t.id,
        kind: 'put',
        data: t,
        ts,
      })),
      ...events.map<Op>((e) => ({
        opId: newId(),
        schema: SCHEMA_VERSION,
        entity: 'event',
        entityId: e.id,
        kind: 'put',
        data: e,
        ts,
      })),
    ];
  }

  /** After a snapshot import: re-apply whatever is still waiting to be pushed (local wins). */
  async reapplyOutbox(): Promise<void> {
    const entries = await this.db.outbox.orderBy('localSeq').toArray();
    if (!entries.length) return;
    const db = this.db;
    await db.transaction('rw', db.tasks, db.events, async () => {
      for (const { op } of entries) await this.applyOp(upcast(op));
    });
    this.changed.update((n) => n + 1);
  }

  /** The server accepted these: drop them from the outbox and stamp the rows with their seq. */
  async markPushed(entries: (OutboxEntry & { seq: number })[]): Promise<void> {
    const db = this.db;
    await db.transaction('rw', db.tasks, db.events, db.outbox, async () => {
      for (const e of entries) {
        if (e.localSeq !== undefined) await db.outbox.delete(e.localSeq);
        if (e.op.kind === 'put' && e.seq) {
          const table = e.op.entity === 'task' ? db.tasks : db.events;
          await (table as typeof db.tasks).update(e.op.entityId, { seq: e.seq });
        }
      }
    });
    this.outboxChanged.update((n) => n + 1);
  }

  // ---------------------------------------------------------------- remote ops & snapshots

  /** Ops from other devices, already decrypted, in server order. Older-schema ops are upcast. */
  async applyRemote(items: { seq: number; op: Op }[]): Promise<void> {
    if (!items.length) return;
    const db = this.db;
    await db.transaction('rw', db.tasks, db.events, async () => {
      for (const { seq, op } of items) await this.applyOp(upcast(op), seq);
    });
    this.changed.update((n) => n + 1);
  }

  async exportSnapshot(): Promise<Snapshot> {
    const [tasks, events] = await Promise.all([this.allTasks(), this.allEvents()]);
    return { schema: SCHEMA_VERSION, exportedAt: nowIso(), tasks, events };
  }

  /** Replace the local cache with the server's master copy. */
  async importSnapshot(data: Snapshot, seq: number): Promise<void> {
    const db = this.db;
    const tasks = (data.tasks ?? []).map((t) => upcastRow('task', t, data.schema)) as Task[];
    const events = (data.events ?? []).map((e) =>
      upcastRow('event', e, data.schema),
    ) as CalendarEvent[];
    await db.transaction('rw', db.tasks, db.events, async () => {
      await db.tasks.clear();
      await db.events.clear();
      await db.tasks.bulkPut(tasks.map((t) => ({ ...t, seq: t.seq || seq })));
      await db.events.bulkPut(events.map((e) => ({ ...e, seq: e.seq || seq })));
    });
    this.changed.update((n) => n + 1);
  }

  /** Drop everything local (reset from server, log out + forget). */
  async clearAll(): Promise<void> {
    const db = this.db;
    await db.transaction('rw', db.tasks, db.events, db.outbox, async () => {
      await db.tasks.clear();
      await db.events.clear();
      await db.outbox.clear();
    });
    this.changed.update((n) => n + 1);
    this.outboxChanged.update((n) => n + 1);
  }
}

/** Bring an op written by an older app up to the current schema (migrations.ts). */
function upcast(op: Op): Op {
  if (op.schema === SCHEMA_VERSION) return op;
  return upcastOp(op as unknown as AnyOp) as unknown as Op;
}

/** Rows in a snapshot written by an older app go through the same upcasters as ops. */
function upcastRow(entity: Entity, row: Task | CalendarEvent, schema: number) {
  if (!schema || schema >= SCHEMA_VERSION) return row;
  const op = upcast({ opId: '', schema, entity, entityId: row.id, kind: 'put', data: row, ts: '' });
  return op.data ?? row;
}
