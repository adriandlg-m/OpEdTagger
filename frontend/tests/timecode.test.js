import test from 'node:test';
import assert from 'node:assert/strict';

import {
  formatEditableTimestamp,
  parseEditableTimestamp,
} from '../src/api/client.js';

test('formats timestamps below one hour as MM:SS.mmm', () => {
  assert.equal(formatEditableTimestamp(1315.64263), '21:55.643');
});

test('formats timestamps of one hour or more as HH:MM:SS.mmm', () => {
  assert.equal(formatEditableTimestamp(3723.456), '01:02:03.456');
});

test('parses MM:SS.mmm and preserves millisecond precision', () => {
  assert.equal(parseEditableTimestamp('21:55.642'), 1315.642);
});

test('parses HH:MM:SS.mmm', () => {
  assert.equal(parseEditableTimestamp('01:02:03.456'), 3723.456);
});

test('accepts a comma decimal separator for locale-aware manual entry', () => {
  assert.equal(parseEditableTimestamp('21:55,642'), 1315.642);
});

test('accepts timecodes without milliseconds', () => {
  assert.equal(parseEditableTimestamp('21:55'), 1315);
  assert.equal(parseEditableTimestamp('00:21:55'), 1315);
});

test('rejects invalid seconds, minutes and malformed values', () => {
  assert.equal(parseEditableTimestamp('21:60.000'), null);
  assert.equal(parseEditableTimestamp('00:60:00.000'), null);
  assert.equal(parseEditableTimestamp('21.55.642'), null);
  assert.equal(parseEditableTimestamp('-01:00'), null);
  assert.equal(parseEditableTimestamp(''), null);
});

test('returns an empty string for missing or invalid format input', () => {
  assert.equal(formatEditableTimestamp(null), '');
  assert.equal(formatEditableTimestamp(Number.NaN), '');
});
