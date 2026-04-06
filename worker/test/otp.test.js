import test from 'node:test';
import assert from 'node:assert/strict';

import {
  buildStorageKey,
  extractOtpFromText,
  loadConfig,
  senderAllowed,
  stripHtml,
} from '../src/index.js';

test('stripHtml removes tags and preserves visible text', () => {
  assert.equal(stripHtml('<div>Your code is <b>123456</b></div>'), 'Your code is 123456');
});

test('sender allowlist supports wildcards', () => {
  assert.equal(senderAllowed('no-reply@accounts.example.com', ['*@accounts.example.com']), true);
  assert.equal(senderAllowed('no-reply@other.com', ['*@accounts.example.com']), false);
});

test('extractOtpFromText prefers keyword-adjacent code', () => {
  const code = extractOtpFromText(['Verification code: 482911. Ignore 1234'], loadConfig({ API_TOKEN: 'x' }));
  assert.equal(code, '482911');
});

test('extractOtpFromText falls back to generic numeric regex', () => {
  const code = extractOtpFromText(['Please use 778899 to continue'], loadConfig({ API_TOKEN: 'x' }));
  assert.equal(code, '778899');
});

test('buildStorageKey normalizes email casing', () => {
  assert.equal(buildStorageKey('Foo+bar@Example.com'), 'otp:foo+bar@example.com');
});
