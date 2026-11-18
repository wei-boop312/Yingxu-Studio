import assert from 'node:assert/strict';
import test from 'node:test';
import {EventCursor} from '../src/eventCursor.ts';

function storage() {
  const values = new Map();
  return {getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key)};
}
const data = (revision, task_id = 'task') => JSON.stringify({task_id, revision});

test('deduplicates event IDs without losing PostgreSQL bigint precision', () => {
  const cursor = new EventCursor('user', 'task', 1, storage());
  assert.equal(cursor.accept('9007199254740992', data(2)), true);
  assert.equal(cursor.accept('9007199254740992', data(3)), false);
  assert.equal(cursor.accept('9007199254740991', data(3)), false);
  assert.equal(cursor.accept('9007199254740993', data(3)), true);
  assert.match(cursor.url(), /cursor=9007199254740993$/);
});

test('advances replay cursor for stale revisions without requesting another snapshot', () => {
  const cursor = new EventCursor('user', 'task', 5, storage());
  assert.equal(cursor.accept('10', data(4)), false);
  assert.match(cursor.url(), /cursor=10$/);
  assert.equal(cursor.accept('11', data(6)), true);
  cursor.observeSnapshot(8);
  assert.equal(cursor.accept('12', data(7)), false);
  assert.equal(cursor.accept('13', data(9)), true);
});

test('restores across instances and isolates both user and task namespaces', () => {
  const store = storage();
  const first = new EventCursor('user', 'task', 1, store);
  first.accept('42', data(2));
  assert.match(new EventCursor('user', 'task', 2, store).url(), /cursor=42$/);
  assert.equal(new EventCursor('other', 'task', 1, store).url().includes('?'), false);
  assert.equal(new EventCursor('user', 'other', 1, store).url().includes('?'), false);
  assert.notEqual(new EventCursor('a:b', 'c', 1).key, new EventCursor('a', 'b:c', 1).key);
});

test('rejects malformed IDs and cross-task or invalid payloads without poisoning the cursor', () => {
  const cursor = new EventCursor('user', 'task', 1, storage());
  for (const id of ['-1', '0', '01', '1.5', '9223372036854775808', 'bad']) assert.equal(cursor.accept(id, data(2)), false);
  for (const payload of ['null', '{', data(2, 'other'), data('2'), data(0), data(9007199254740992)]) assert.equal(cursor.accept('42', payload), false);
  assert.equal(cursor.url().includes('?'), false);
  assert.equal(cursor.accept('9223372036854775807', data(2)), true);
});

test('retention reset clears persisted state and accepts a lower replacement event ID', () => {
  const store = storage();
  const cursor = new EventCursor('user', 'task', 1, store);
  cursor.accept('100', data(2));
  cursor.reset(2);
  assert.equal(new EventCursor('user', 'task', 2, store).url().includes('?'), false);
  assert.equal(cursor.accept('10', data(3)), true);
});

test('unavailable storage preserves in-memory deduplication', () => {
  const denied = () => { throw new Error('storage denied'); };
  const cursor = new EventCursor('user', 'task', 1, {getItem: denied, setItem: denied, removeItem: denied});
  assert.equal(cursor.accept('1', data(2)), true);
  assert.equal(cursor.accept('1', data(2)), false);
  cursor.reset(2);
  assert.equal(cursor.accept('1', data(3)), true);
});
