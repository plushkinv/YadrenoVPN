// Local build-time declarations. This module is never a public code-upload API.
import { existsSync, lstatSync, readFileSync, readdirSync, realpathSync } from 'node:fs';
import { resolve, relative, sep, extname, dirname, isAbsolute } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseSync } from 'rolldown/utils';

const declarations = JSON.parse(readFileSync(new URL('./src/runtime/view-registry.json', import.meta.url), 'utf8'));
const componentIds = new Set(declarations.components.map(item => item.id));
const baseIds = new Set(declarations.pages.map(item => item.id));
const allowed = new Set(['format_version', 'version', 'api', 'frontend_api', 'environment_contract', 'styles', 'assets', 'components', 'pages', 'navigation', 'modules']);
const identifier = /^[a-z][a-z0-9_]{0,63}$/;
const route = /^[a-z][a-z0-9_.-]{0,79}$/;
const sharedImports = ['react', 'react/jsx-runtime', 'react/jsx-dev-runtime', 'lucide-react', '@ui/sdk'];
const stockRoot = fileURLToPath(new URL('./src', import.meta.url));
const packages = Object.keys(JSON.parse(readFileSync(new URL('./package.json', import.meta.url), 'utf8')).dependencies);
const within = (root, file) => { const name = relative(root, file); return name !== '..' && !name.startsWith('..' + sep) && !isAbsolute(name); };
const clientImport = name => !name.split('/').includes('..') && packages.some(pkg => name === pkg || name.startsWith(pkg + '/'));
const object = value => value && typeof value === 'object' && !Array.isArray(value);
function fields(value, names, description) {
  if (!object(value) || Object.keys(value).some(key => !names.includes(key))) throw new Error('Invalid ' + description + ' fields');
}
function list(value, name) { if (!Array.isArray(value)) throw new Error(name + ' must be an array'); return value; }
function apiRange(value, name) {
  fields(value, ['min', 'max'], name);
  if (!Number.isInteger(value.min) || !Number.isInteger(value.max) || value.min < 1 || value.min > 1 || value.max < 1) throw new Error('Incompatible ' + name);
  return value;
}
function checkTree(root) {
  // Check imported helpers/assets too, before either TypeScript or Vite reads them.
  for (const entry of readdirSync(root, { withFileTypes: true })) {
    const file = resolve(root, entry.name);
    const info = lstatSync(file);
    if (info.isSymbolicLink() || info.isFile() && info.nlink !== 1 || !info.isFile() && !info.isDirectory()) throw new Error('Custom UI requires local regular files: ' + file);
    if (process.platform !== 'win32' && (info.uid !== process.getuid() || info.mode & 0o022)) throw new Error('Custom UI files must be administrator-owned and not writable by others: ' + file);
    if (info.isDirectory()) checkTree(file);
  }
}
function path(root, name, extensions) {
  if (typeof name !== 'string' || !name || /[\\:\x00-\x1f]/.test(name) || name.split('/').some(p => !p || p.startsWith('.')) || !extensions.includes(extname(name))) throw new Error('Invalid custom UI file: ' + name);
  let part = root;
  for (const value of name.split('/')) { part = resolve(part, value); if (lstatSync(part).isSymbolicLink()) throw new Error('Custom UI symlink: ' + name); }
  if (relative(root, realpathSync(part)).startsWith('..' + sep) || !lstatSync(part).isFile()) throw new Error('Custom UI file escapes root');
  return part;
}
export function readCustomization(root) {
  root = resolve(root);
  if (!existsSync(root)) return { version: 'base', api: {min:1,max:1}, requirements: {frontend_api:{min:1,max:1},modules:[]}, styles: [], assets: [], components: [], pages: [], modules: [] };
  if (lstatSync(root).isSymbolicLink() || !lstatSync(root).isDirectory()) throw new Error('Custom UI root must be a local directory');
  checkTree(root);
  const manifest = JSON.parse(readFileSync(path(root, 'manifest.json', ['.json']), 'utf8'));
  fields(manifest, [...allowed], 'manifest');
  if (manifest.format_version !== 1 || manifest.environment_contract !== 1 || !/^\d+\.\d+\.\d+$/.test(manifest.version)) throw new Error('Incompatible custom UI format/environment/version');
  apiRange(manifest.api, 'custom UI API');
  const frontend = apiRange(manifest.frontend_api ?? {min:1,max:1}, 'frontend API');
  const styles = list(manifest.styles ?? [], 'styles').map(name => path(root, name, ['.css']));
  const assets = list(manifest.assets ?? [], 'assets').map(name => ({ name, file: path(root, name, ['.svg', '.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp', '.ico', '.woff2']) }));
  if (new Set(assets.map(asset => asset.name)).size !== assets.length) throw new Error('Duplicate asset');
  const modules = list(manifest.modules ?? [], 'modules');
  const moduleIds = new Set();
  for (const module of modules) {
    fields(module, ['id', 'version', 'api_version', 'core_api', 'frontend_api', 'environment_contract'], 'module');
    if (!identifier.test(module.id) || moduleIds.has(module.id) || !/^\d+\.\d+\.\d+$/.test(module.version) || module.api_version !== 1) throw new Error('Invalid/duplicate/incompatible module declaration');
    moduleIds.add(module.id);
    apiRange(module.core_api ?? {min:1,max:1}, 'module core API');
    apiRange(module.frontend_api ?? {min:1,max:1}, 'module frontend API');
    if ((module.environment_contract ?? 1) !== 1) throw new Error('Incompatible module environment');
  }
  const owners = new Set();
  const entries = (name, valid) => list(manifest[name] ?? [], name).map(item => {
    fields(item, name === 'pages' ? ['id', 'file', 'title', 'module_id'] : ['id', 'file'], name);
    if (typeof item.id !== 'string' || !route.test(item.id) || !valid(item)) throw new Error('Invalid ' + name + ' target: ' + item.id);
    const key = name + '.' + item.id;
    if (owners.has(key)) throw new Error('Conflicting replacements: ' + key);
    owners.add(key);
    return { ...item, file: path(root, item.file, ['.tsx', '.ts']) };
  });
  const components = entries('components', item => componentIds.has(item.id));
  const pages = entries('pages', item => baseIds.has(item.id) ? !item.module_id :
    (item.module_id === undefined || moduleIds.has(item.module_id) && item.id.startsWith(item.module_id + '.')) && typeof item.title === 'string' && item.title.length > 0 && item.title.length <= 100);
  const known = new Set([...baseIds, ...pages.map(page => page.id)]);
  let navigation;
  if (manifest.navigation !== undefined) {
    const used = new Set();
    navigation = list(manifest.navigation, 'navigation').map(item => {
      fields(item, ['route', 'label'], 'navigation');
      if (!known.has(item.route) || used.has(item.route) || typeof item.label !== 'string' || !item.label || item.label.length > 60) throw new Error('Invalid/duplicate navigation route');
      used.add(item.route); return item;
    });
    if (!navigation.length) throw new Error('Navigation must have at least one entry');
  }
  const requirements = {frontend_api:frontend, modules:modules.map(module => ({...module,
    core_api:module.core_api ?? {min:1,max:1}, frontend_api:module.frontend_api ?? {min:1,max:1},
    environment_contract:module.environment_contract ?? 1}))};
  return { version: manifest.version, api:manifest.api, requirements, components, pages, styles, assets, modules, navigation };
}

export function customizationPlugin(root) {
  root = resolve(root);
  const mirrorRoot = resolve(root, 'src');
  // Resolve the same logical frontend module once, custom first. Missing local
  // dependencies fall back to stock src, never to installation/backend files.
  function mirrored(file) {
    const base = within(stockRoot, file) ? stockRoot : within(mirrorRoot, file) ? mirrorRoot : null;
    if (!base) return null;
    const name = relative(base, file);
    for (const suffix of ['', '.ts', '.tsx', '.js', '.json', '/index.ts', '/index.tsx']) {
      for (const folder of [mirrorRoot, stockRoot]) {
        const candidate = resolve(folder, name + suffix);
        if (existsSync(candidate) && lstatSync(candidate).isFile()) return candidate;
      }
    }
    return null;
  }
  const id = 'virtual:yadreno-customization', resolved = '\0' + id;
  let declaration, resolvedPublic = [];
  return {
    name: 'yadreno-customization', enforce: 'pre',
    configResolved(config) {
      declaration = readCustomization(root);
      resolvedPublic = sharedImports.flatMap(name => {
        const alias = config.resolve.alias.find(item => typeof item.find === 'string' && (name === item.find || name.startsWith(item.find + '/')));
        return alias ? [resolve(alias.replacement, name.slice(alias.find.length + 1))] : [];
      });
    },
    buildStart() { for (const asset of declaration.assets) this.emitFile({ type: 'asset', fileName: 'assets/' + asset.name, source: readFileSync(asset.file) }); },
    async resolveId(source, importer) {
      if (source === id) return resolved;
      if (isAbsolute(source)) {
        const target = mirrored(source);
        if (target && target !== source) return target;
      }
      const origin = importer?.split('?')[0];
      if (origin && (within(stockRoot, origin) || within(mirrorRoot, origin)) && source.startsWith('.')) {
        const target = mirrored(resolve(dirname(origin), source));
        if (target) return target;
      }
      // Vite resolves trusted aliases before this hook; normalize Windows paths.
      if (isAbsolute(source) && resolvedPublic.some(file => resolve(source) === file || resolve(source) === file + '.ts')) return;
      if (!importer || importer.startsWith('\0')) return;
      const from = relative(resolve(root), importer.split('?')[0]);
      if (!from || from.startsWith('..')) return;
      if (within(mirrorRoot, origin) && clientImport(source)) {
        return this.resolve(source, resolve(stockRoot, relative(mirrorRoot, origin)), { skipSelf: true });
      }
      if (!sharedImports.includes(source)) {
        if (!source.startsWith('.') && !isAbsolute(source)) throw new Error('Unsupported custom UI import: ' + source);
        const target = await this.resolve(source, importer, { skipSelf: true });
        if (target && relative(resolve(root), target.id.split('?')[0]).startsWith('..')) throw new Error('Custom UI imports must stay within custom_web or use @ui/sdk');
        return target;
      }
    },
    load(source) {
      if (source !== resolved) return;
      const imports = [];
      const components = declaration.components.map((item, index) => { imports.push(`import Component${index} from ${JSON.stringify(item.file)};`); return `${JSON.stringify(item.id)}: Component${index}`; });
      const pages = declaration.pages.map((item, index) => { imports.push(`import Page${index} from ${JSON.stringify(item.file)};`); return `{...${JSON.stringify({ id: item.id, title: item.title, module_id: item.module_id })}, component: Page${index}}`; });
      declaration.styles.forEach(file => imports.push(`import ${JSON.stringify(file)};`));
      return imports.join('\n') + `\nexport default {version:${JSON.stringify(declaration.version)}, build_version:${JSON.stringify(process.env.YADRENO_UI_BUILD ?? 'development')}, instance_id:${JSON.stringify(process.env.YADRENO_UI_INSTANCE ?? 'development')}, asset_base:${JSON.stringify(process.env.YADRENO_UI_BASE ?? '/')}, components:{${components}}, pages:[${pages}], modules:${JSON.stringify(declaration.modules)}, navigation:${JSON.stringify(declaration.navigation)}};`;
    },
    async transform(source, id) {
      const filename = id.split('?')[0];
      const rel = relative(resolve(root), filename);
      if (!rel || rel.startsWith('..') || filename.startsWith('\0')) return;
      if (extname(filename) === '.css') {
        const mirror = within(mirrorRoot, filename);
        if ((!mirror && /@import\b/i.test(source)) || /url\(\s*['"]?\s*(?:https?:|\/\/)/i.test(source)) throw new Error('Custom UI CSS must use local bundled assets');
        for (const match of source.matchAll(/url\(\s*['"]?\s*([^'"\s)]+)/g)) {
          if (!match[1].startsWith('#') && !match[1].startsWith('data:') &&
              (match[1].startsWith('/') || relative(resolve(root), resolve(dirname(filename), match[1])).startsWith('..')))
            throw new Error('Custom UI CSS assets must stay within custom_web');
        }
        if (mirror) {
          for (const match of source.matchAll(/@import\s+(?:url\(\s*)?['"]([^'"]+)['"]/g)) {
            const name = match[1];
            const target = clientImport(name)
              ? (await this.resolve(name, resolve(stockRoot, relative(mirrorRoot, filename)), { skipSelf: true }))?.id
              : name.startsWith('.') ? mirrored(resolve(dirname(filename), name)) : null;
            if (!target) throw new Error('Unsupported mirrored stylesheet import: ' + name);
            source = source.replace(match[0], match[0].replace(name, target.replaceAll('\\', '/')));
          }
        }
        return { code: mirror ? source : '@layer custom {\n' + source + '\n}', map: null };
      }
      // Source imports are administrator-owned code, but must not accidentally
      // embed installation files or node capabilities in a client bundle.
      if (extname(filename) === '.ts' || extname(filename) === '.tsx') {
        const imports = [];
        const syntax = parseSync(filename, source);
        if (syntax.errors.length) throw new Error('Invalid custom UI source syntax');
        function visit(node) {
          if (!node || typeof node !== 'object') return;
          const value = ['ImportDeclaration', 'ExportNamedDeclaration', 'ExportAllDeclaration', 'ImportExpression'].includes(node.type) ? node.source : null;
          if (typeof value?.value === 'string') imports.push(value.value);
          for (const child of Object.values(node)) {
            if (Array.isArray(child)) child.forEach(visit);
            else if (child && typeof child === 'object' && typeof child.type === 'string') visit(child);
          }
        }
        visit(syntax.program);
        for (const name of imports) {
          if (name.startsWith('.')) {
            const target = resolve(dirname(filename), name);
            if (relative(resolve(root), target).startsWith('..')) throw new Error('Custom UI imports must stay within custom_web or use @ui/sdk');
          } else if (!sharedImports.includes(name) && !(within(mirrorRoot, filename) && clientImport(name))) throw new Error('Unsupported custom UI import: ' + name);
        }
      }
    },
  };
}
