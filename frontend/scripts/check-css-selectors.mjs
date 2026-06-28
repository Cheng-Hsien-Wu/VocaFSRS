import { readdir, readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = fileURLToPath(new URL('../src/', import.meta.url));
const DYNAMIC_CLASS_PREFIXES = [
  'pill-',
  'home-action-',
  'placement-action-',
  'study-option-',
  'study-tts-',
];

async function filesUnder(directory, extensionPattern) {
  const entries = await readdir(directory, { withFileTypes: true });
  const nested = await Promise.all(entries.map(async entry => {
    const filePath = path.join(directory, entry.name);
    if (entry.isDirectory()) return filesUnder(filePath, extensionPattern);
    return extensionPattern.test(entry.name) ? [filePath] : [];
  }));
  return nested.flat();
}

const [cssFiles, sourceFiles] = await Promise.all([
  filesUnder(path.join(ROOT, 'styles'), /\.css$/),
  filesUnder(ROOT, /\.(?:ts|tsx)$/),
]);

const source = (
  await Promise.all(sourceFiles.map(file => readFile(file, 'utf8')))
).join('\n');

const unused = new Map();
for (const file of cssFiles) {
  const css = await readFile(file, 'utf8');
  const classNames = [...css.matchAll(/\.([A-Za-z_][\w-]*)/g)]
    .map(match => match[1]);

  for (const className of new Set(classNames)) {
    const isDynamic = DYNAMIC_CLASS_PREFIXES.some(prefix => className.startsWith(prefix));
    if (!isDynamic && !source.includes(className)) {
      const relativeFile = path.relative(path.dirname(ROOT), file);
      const files = unused.get(className) ?? [];
      files.push(relativeFile);
      unused.set(className, files);
    }
  }
}

if (unused.size > 0) {
  console.error('Potentially unused CSS selectors:');
  for (const [className, files] of unused) {
    console.error(`- .${className} (${files.join(', ')})`);
  }
  console.error('Verify state-generated selectors in browser flows before deleting them.');
  process.exitCode = 1;
} else {
  console.log('CSS selector check passed.');
}
