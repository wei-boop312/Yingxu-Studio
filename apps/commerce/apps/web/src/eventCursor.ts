// Event IDs are PostgreSQL bigint values; keep them as strings in storage.
function eventId(value: unknown): bigint | undefined {
  if (typeof value !== 'string' || !/^(0|[1-9][0-9]{0,18})$/.test(value)) return;
  const id = BigInt(value);
  if (id <= 9223372036854775807n) return id;
}

export class EventCursor {
  readonly key: string;
  private id = 0n;
  private revision: number;
  private taskId: string;
  private storage: Pick<Storage, 'getItem' | 'setItem' | 'removeItem'> | undefined;

  constructor(userId: string, taskId: string, revision: number, storage?: Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>) {
    this.key = 'visual:event-cursor:v1:' + encodeURIComponent(userId) + ':' + encodeURIComponent(taskId);
    this.taskId = taskId;
    this.revision = revision;
    this.storage = storage;
    try { this.id = eventId(storage?.getItem(this.key)) ?? 0n; } catch { /* Memory-only when storage is unavailable. */ }
  }

  url() {
    return '/api/v1/tasks/' + encodeURIComponent(this.taskId) + '/events' + (this.id ? '?cursor=' + this.id : '');
  }

  observeSnapshot(revision: number) {
    this.revision = Math.max(this.revision, revision);
  }

  accept(lastEventId: string, data: string): boolean {
    const id = eventId(lastEventId);
    if (id === undefined || id === 0n || id <= this.id) return false;
    let payload;
    try { payload = JSON.parse(data); } catch { return false; }
    if (!payload || payload.task_id !== this.taskId || !Number.isSafeInteger(payload.revision) || payload.revision < 1) return false;
    this.id = id;
    try { this.storage?.setItem(this.key, String(id)); } catch { /* Keep in-memory deduplication. */ }
    if (payload.revision <= this.revision) return false;
    this.revision = payload.revision;
    return true;
  }

  reset(revision: number) {
    this.id = 0n;
    this.revision = revision;
    try { this.storage?.removeItem(this.key); } catch { /* Memory-only fallback. */ }
  }
}

export function sessionEventStorage() {
  try { return window.sessionStorage; } catch { return undefined; }
}
