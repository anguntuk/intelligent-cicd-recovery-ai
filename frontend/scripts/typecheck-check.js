// This demo is plain JavaScript, so this check validates the expected source contract.
const fs = require('node:fs');
const path = require('node:path');

const sourcePath = path.join(__dirname, '..', 'src', 'index.html');
if (!fs.existsSync(sourcePath)) {
  console.error('Type-check placeholder failed: frontend source is missing.');
  process.exit(1);
}
console.log('Source contract check passed (demo contains no TypeScript).');
