import { TestBed } from '@angular/core/testing';
import { AssistantDb } from '../db/db';
import { DbService } from '../db/db.service';
import { SCHEMA_VERSION } from '../db/migrations';
import type { Op, Task } from '../db/models';
import { StoreService } from './store.service';
import { TaskLimitError, TasksService } from './tasks.service';

function task(over: Partial<Task> = {}): Task {
  const now = new Date().toISOString();
  return {
    id: over.id ?? crypto.randomUUID(),
    title: 'A task',
    description: null,
    priority: 'medium',
    status: 'todo',
    dueAt: null,
    tags: [],
    completedAt: null,
    createdAt: now,
    updatedAt: now,
    seq: 0,
    ...over,
  };
}

describe('StoreService', () => {
  let store: StoreService;
  let db: AssistantDb;

  beforeEach(() => {
    TestBed.configureTestingModule({});
    db = new AssistantDb(`store-test-${Math.random()}`);
    TestBed.inject(DbService).use(db);
    store = TestBed.inject(StoreService);
  });

  afterEach(() => db.close());

  it('writes rows and queues an op for each local change', async () => {
    const t = task({ title: 'Buy milk' });
    await store.putTask(t);
    expect((await store.allTasks()).map((x) => x.title)).toEqual(['Buy milk']);
    expect(await store.outboxSize()).toBe(1);
    const [entry] = await store.pendingOps(10);
    expect(entry.op).toMatchObject({ entity: 'task', kind: 'put', entityId: t.id });

    await store.deleteTask(t.id);
    expect(await store.allTasks()).toEqual([]);
    expect(await store.outboxSize()).toBe(2);
  });

  it('marks pushed ops: drops them from the outbox and stamps the server seq', async () => {
    const t = task();
    await store.putTask(t);
    const [entry] = await store.pendingOps(10);
    await store.markPushed([{ ...entry, seq: 42 }]);
    expect(await store.outboxSize()).toBe(0);
    expect((await db.tasks.get(t.id))?.seq).toBe(42);
  });

  it('applies remote ops in server order (last one wins) and up-casts old schemas', async () => {
    const id = 'remote-1';
    const v1: Op = {
      opId: 'a',
      schema: 1,
      entity: 'task',
      entityId: id,
      kind: 'put',
      data: { id, title: 'Old', status: 'done', updatedAt: '2025-01-01T00:00:00Z' } as never,
      ts: '',
    };
    const v2: Op = {
      opId: 'b',
      schema: SCHEMA_VERSION,
      entity: 'task',
      entityId: id,
      kind: 'put',
      data: task({ id, title: 'New' }),
      ts: '',
    };
    await store.applyRemote([{ seq: 1, op: v1 }]);
    let row = await db.tasks.get(id);
    expect(row?.title).toBe('Old');
    expect(row?.tags).toEqual([]); // v1 → v2 upcast
    expect(row?.completedAt).toBe('2025-01-01T00:00:00Z');
    await store.applyRemote([{ seq: 2, op: v2 }]);
    row = await db.tasks.get(id);
    expect(row?.title).toBe('New');
    expect(row?.seq).toBe(2);
    expect(await store.outboxSize()).toBe(0); // remote data is never re-uploaded
  });

  it('exports and imports snapshots; the outbox is re-applied on top', async () => {
    await store.putTask(task({ id: 'local', title: 'Mine' }));
    const snapshot = {
      schema: SCHEMA_VERSION,
      exportedAt: '',
      tasks: [task({ id: 'server', title: 'From server', seq: 7 })],
      events: [],
    };
    await store.importSnapshot(snapshot, 7);
    expect((await store.allTasks()).map((t) => t.id)).toEqual(['server']);
    await store.reapplyOutbox();
    expect((await store.allTasks()).map((t) => t.id).sort()).toEqual(['local', 'server']);
    const exported = await store.exportSnapshot();
    expect(exported.schema).toBe(SCHEMA_VERSION);
    expect(exported.tasks).toHaveLength(2);
  });
});

describe('TasksService', () => {
  let tasks: TasksService;
  let db: AssistantDb;

  beforeEach(() => {
    TestBed.configureTestingModule({});
    db = new AssistantDb(`tasks-test-${Math.random()}`);
    TestBed.inject(DbService).use(db);
    tasks = TestBed.inject(TasksService);
  });

  afterEach(() => db.close());

  const input = (title: string, status: Task['status'] = 'todo') => ({
    title,
    description: null,
    priority: 'medium' as const,
    status,
    dueAt: null,
    tags: [],
  });

  it('enforces the active-task limit of the anonymous membership (3), but not for done tasks', async () => {
    await tasks.create(input('1'));
    await tasks.create(input('2'));
    await tasks.create(input('3'));
    await tasks.reload();
    expect(tasks.activeCount()).toBe(3);
    expect(tasks.limit().allowed).toBe(false);
    await expect(tasks.create(input('4'))).rejects.toBeInstanceOf(TaskLimitError);
    await expect(tasks.create(input('done', 'done'))).resolves.toBeTruthy();
    // Finishing one frees a slot
    const [first] = tasks.all().filter((t) => t.status !== 'done');
    await tasks.toggleDone(first);
    await tasks.reload();
    expect(tasks.limit().allowed).toBe(true);
    expect(tasks.all().find((t) => t.id === first.id)?.completedAt).toBeTruthy();
  });

  it('filters and sorts the visible list', async () => {
    await tasks.create({ ...input('Low'), priority: 'low' });
    await tasks.create({ ...input('Urgent'), priority: 'urgent' });
    await tasks.create(input('Finished', 'done'));
    await tasks.reload();
    expect(tasks.visible().map((t) => t.title)).toEqual(['Urgent', 'Low', 'Finished']);
    tasks.search.set('urg');
    expect(tasks.visible().map((t) => t.title)).toEqual(['Urgent']);
    tasks.search.set('');
    tasks.filterStatus.set('done');
    expect(tasks.visible().map((t) => t.title)).toEqual(['Finished']);
  });
});
