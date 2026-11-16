const test = require('node:test');
const assert = require('node:assert/strict');

test('known issue demo: frontend title expectation mismatch', () => {
  const actualTitle = 'CI/CD Recovery Demo';
  const expectedTitle = 'CI/CD Recovery Dashboard';

  assert.equal(
    actualTitle,
    expectedTitle,
    'Frontend title does not match the expected product title'
  );
});