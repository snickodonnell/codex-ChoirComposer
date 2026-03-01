import { mkdir } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';

const rootDir = path.dirname(fileURLToPath(import.meta.url));
const outDir = path.join(rootDir, 'app', 'static', 'vendor');

await mkdir(outDir, { recursive: true });

await build({
  stdin: {
    contents: "export { Canvg, presets } from 'canvg'; export const CANVG_VERSION = '4.0.3';",
    resolveDir: rootDir,
    sourcefile: 'canvg-entry.js',
    loader: 'js',
  },
  bundle: true,
  format: 'esm',
  target: ['es2020'],
  platform: 'browser',
  sourcemap: false,
  outfile: path.join(outDir, 'canvg.browser.js'),
  logLevel: 'info',
});

console.log(`Bundled canvg to ${path.relative(rootDir, path.join(outDir, 'canvg.browser.js'))}`);
