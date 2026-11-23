import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequestSequence } from '../src/api/requestSequence.js';

test('a newer series navigation invalidates the previous detail request', () => {
  const requests = createRequestSequence();
  const danDaDanRequest = requests.begin();
  const drStoneRequest = requests.begin();

  assert.equal(requests.isCurrent(danDaDanRequest), false);
  assert.equal(requests.isCurrent(drStoneRequest), true);
});

test('reopening the same series still invalidates an older response', () => {
  const requests = createRequestSequence();
  const firstRequest = requests.begin();
  const retryRequest = requests.begin();

  assert.equal(requests.isCurrent(firstRequest), false);
  assert.equal(requests.isCurrent(retryRequest), true);
});


test('refreshes during one selection share the same selection token', () => {
  const requests = createRequestSequence();
  const selectedSeriesToken = requests.begin();
  const backgroundRefreshToken = requests.current();

  assert.equal(backgroundRefreshToken, selectedSeriesToken);
  assert.equal(requests.isCurrent(backgroundRefreshToken), true);
});
