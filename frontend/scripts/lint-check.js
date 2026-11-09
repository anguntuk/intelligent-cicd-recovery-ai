const fs = require('node:fs');
const path = require('node:path');

const htmlPath = path.join(__dirname, '..', 'src', 'index.html');
if (!fs.existsSync(htmlPath)) {
  console.error(`Lint check failed: missing source file ${htmlPath}`);
  process.exit(1);
}

const html = fs.readFileSync(htmlPath, 'utf8');
if (!html.includes('<!doctype html>')) {
  console.error('Lint check failed: HTML doctype is missing.');
  process.exit(1);
}

console.log('Lint check passed.');
