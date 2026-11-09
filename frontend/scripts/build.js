const fs = require('node:fs');
const path = require('node:path');

const sourceDir = path.join(__dirname, '..', 'src');
const outputDir = path.join(__dirname, '..', 'dist');

fs.rmSync(outputDir, { recursive: true, force: true });
fs.mkdirSync(outputDir, { recursive: true });
fs.copyFileSync(path.join(sourceDir, 'index.html'), path.join(outputDir, 'index.html'));
console.log(`Frontend build complete: ${outputDir}`);
