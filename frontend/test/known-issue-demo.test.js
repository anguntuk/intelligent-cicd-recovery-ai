
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

test('known issue demo: frontend title expectation mismatch', () => {
  const page = fs.readFileSync(
    path.join(__dirname, '..', 'src', 'index.html'),
    'utf8'
  );
  const actualTitle = page.match(/<title>(.*?)<\/title>/)?.[1] ?? null;
  const expectedTitle = 'CI/CD Recovery Dashboard';

  assert.equal(
    actualTitle,
    expectedTitle,
    'Frontend title does not match the expected product title'
  );
});
