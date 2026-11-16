const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

test('frontend entry page exists and has a title', () => {
  const page = fs.readFileSync(path.join(__dirname, '..', 'src', 'index.html'), 'utf8');
  assert.match(page, /<title>CI\/CD Recovery Dashboard<\/title>/);
});
