// Validate project metadata. Source resolution is ordinary Vite resolution.
import { readFileSync, readdirSync, lstatSync } from 'node:fs';
import { resolve } from 'node:path';

export function readCustomization(root) {
  root = resolve(root);
  const check = folder => {
    for (const entry of readdirSync(folder, { withFileTypes: true })) {
      if (['node_modules', '.git', '__pycache__', '.cache', 'dist'].includes(entry.name)) continue;
      const path = resolve(folder, entry.name), info = lstatSync(path);
      if (info.isSymbolicLink() || info.isFile() && info.nlink !== 1 || !info.isDirectory() && !info.isFile())
        throw new Error('Source must be a local regular file: ' + path);
      if (info.isDirectory()) check(path);
    }
  };
  check(root);
  const manifest = JSON.parse(readFileSync(resolve(root, 'manifest.json'), 'utf8'));
  if (manifest.format_version !== 2 || manifest.environment_contract !== 1 || !/^\d+\.\d+\.\d+$/.test(manifest.version))
    throw new Error('manifest.json: expected format_version=2, environment_contract=1 and a semantic version');
  if (Object.keys(manifest).some(key => !['format_version', 'version', 'api', 'frontend_api', 'environment_contract', 'modules'].includes(key)))
    throw new Error('manifest.json: source replacements, styles and assets lists are no longer supported; use ordinary imports');
  for (const key of ['api', 'frontend_api']) {
    if (!Number.isInteger(manifest[key]?.min) || !Number.isInteger(manifest[key]?.max) || manifest[key].min !== 1 || manifest[key].max < 1)
      throw new Error('manifest.json: incompatible ' + key + ' range');
  }
  if (!Array.isArray(manifest.modules)) throw new Error('manifest.json: modules must be an array');
  return { version: manifest.version, api: manifest.api,
    requirements: { frontend_api: manifest.frontend_api, modules: manifest.modules }, pages: [], components: [] };
}
