/* Test bundler (Node, no dependencies) — the JavaScript twin of tests/bundle.py.
 *
 * The verification sandbox can run a script file but has no vm / eval / new Function, so ES
 * modules cannot be loaded dynamically. This turns every module under web/app into a factory
 * inside ONE script (tests/out/<name>), with:
 *   - /vendor/preact-htm.js replaced by a React shim: hooks + `html = htm.bind(h)`, where the
 *     htm parser is extracted verbatim from the vendored file and `h` wraps React.createElement
 *     (Preact props like class / for / stroke-width are mapped to React names);
 *   - top-level import/export rewritten; exports become live getters (ES semantics kept);
 *   - tests/out/linemap.json mapping bundle lines back to source files.
 * A syntax error anywhere fails compilation of the bundle, so running it is also the syntax check.
 *
 * Usage (run_javascript):  const b = require(WEB + '/tests/bundle.js') is not possible (no require of
 * workspace files), so the runner script inlines this file's text: see tests/README in web/README.md.
 */
function makeBundler(fs, path) {
  const IMPORT_NAMED = /^\s*import\s*\{([^}]*)\}\s*from\s*['"]([^'"]+)['"];?/gm;
  const IMPORT_STAR = /^\s*import\s+\*\s+as\s+(\w+)\s+from\s*['"]([^'"]+)['"];?/gm;
  const IMPORT_DEFAULT = /^\s*import\s+(\w+)\s+from\s*['"]([^'"]+)['"];?/gm;
  const IMPORT_BARE = /^\s*import\s*['"]([^'"]+)['"];?/gm;
  const EXPORT_DECL = /^export\s+(async\s+function\*?|function\*?|const|let|var|class)\s+(\w+)/gm;
  const EXPORT_LIST = /^export\s*\{([^}]*)\};?/gm;
  const EXPORT_DEFAULT = /^export\s+default\s+/gm;

  function resolve(spec, importer) {
    if (spec.startsWith('/vendor/')) return 'vendor';
    if (spec.startsWith('/app/')) return spec.slice(1);
    if (spec.startsWith('.')) return path.posix.normalize(path.posix.join(path.posix.dirname(importer), spec));
    return 'node:' + spec;
  }

  function transform(src, id) {
    const exports = [];
    src = src.replace(IMPORT_NAMED, (m, names, spec) => {
      const parts = names.split(',').map((s) => s.trim()).filter(Boolean).map((s) => (s.includes(' as ') ? s.split(' as ').map((x) => x.trim()).join(': ') : s));
      return `const { ${parts.join(', ')} } = __req(${JSON.stringify(resolve(spec, id))});` + '\n'.repeat((m.match(/\n/g) || []).length);
    });
    src = src.replace(IMPORT_STAR, (m, name, spec) => `const ${name} = __req(${JSON.stringify(resolve(spec, id))});`);
    src = src.replace(IMPORT_DEFAULT, (m, name, spec) => `const ${name} = __req(${JSON.stringify(resolve(spec, id))}).default;`);
    src = src.replace(IMPORT_BARE, (m, spec) => `__req(${JSON.stringify(resolve(spec, id))});`);
    src = src.replace(EXPORT_DECL, (m, kw, name) => { exports.push([name, name]); return `${kw} ${name}`; });
    src = src.replace(EXPORT_LIST, (m, list) => {
      list.split(',').map((s) => s.trim()).filter(Boolean).forEach((s) => {
        const [a, b] = s.includes(' as ') ? s.split(' as ').map((x) => x.trim()) : [s, s];
        exports.push([b, a]);
      });
      return '';
    });
    src = src.replace(EXPORT_DEFAULT, '__exports.default = ');
    const bad = src.match(/^\s*(import|export)\s.*$/m);
    if (bad) throw new SyntaxError(`${id}: unsupported module syntax: ${bad[0].slice(0, 80)}`);
    const getters = exports.map(([n, l]) => `Object.defineProperty(__exports, ${JSON.stringify(n)}, { enumerable: true, get: () => ${l} });`).join('');
    return getters + '\n' + src;
  }

  function htmSource(vendor) {
    const start = vendor.indexOf('var ce=function');
    const end = vendor.indexOf('}.bind(a);', start);
    if (start < 0 || end < 0) throw new Error('htm parser not found in vendor file');
    return vendor.slice(start, end + 1) + ';';
  }

  const SHIM = `
const React = require('react');
const MAP = { class: 'className', for: 'htmlFor', tabindex: 'tabIndex', readonly: 'readOnly', maxlength: 'maxLength',
  minlength: 'minLength', autocomplete: 'autoComplete', autofocus: 'autoFocus', spellcheck: 'spellCheck',
  enterkeyhint: 'enterKeyHint', inputmode: 'inputMode', colspan: 'colSpan', rowspan: 'rowSpan', onDblClick: 'onDoubleClick',
  crossorigin: 'crossOrigin', srcset: 'srcSet', contenteditable: 'contentEditable' };
function fix(props) {
  if (!props) return props;
  const out = {};
  for (const k in props) {
    let n = MAP[k] || k;
    if (!MAP[k] && k.includes('-') && !k.startsWith('aria-') && !k.startsWith('data-')) n = k.replace(/-([a-z])/g, (_, c) => c.toUpperCase());
    out[n] = props[k];
  }
  if (typeof out.style === 'string') throw new Error('string style is not portable (use an object): ' + out.style);
  return out;
}
function h(type, props, ...children) {
  if (type === undefined || type === null) throw new Error('h(): component is ' + type + ' (bad import?) props=' + JSON.stringify(Object.keys(props || {})));
  __stats.elements++;
  return React.createElement(type, fix(props), ...children);
}
__HTM__
const html = fe.bind(h);
Object.assign(__exports, {
  h, html, render() {}, Component: React.Component, createContext: React.createContext,
  useState: React.useState, useReducer: React.useReducer, useEffect: React.useEffect,
  useLayoutEffect: React.useEffect, useRef: React.useRef, useImperativeHandle: React.useImperativeHandle,
  useMemo: React.useMemo, useCallback: React.useCallback, useContext: React.useContext,
  useDebugValue: React.useDebugValue, useErrorBoundary: () => [undefined, () => {}],
});
`;

  const PRELUDE = `/* generated by tests/bundle.js — do not edit */
var __stats = { elements: 0 };
var __modules = Object.create(null);
var __cache = Object.create(null);
var __overrides = Object.create(null);
function __req(id) {
  if (id.startsWith('node:')) return require(id.slice(5));
  if (__overrides[id]) return __overrides[id];
  if (__cache[id]) return __cache[id];
  const f = __modules[id];
  if (!f) throw new Error('module not found: ' + id);
  const ex = {};
  __cache[id] = ex;
  f(ex);
  return ex;
}
`;

  function walk(dir, base, out) {
    for (const f of fs.readdirSync(dir).sort()) {
      const full = path.join(dir, f);
      if (fs.statSync(full).isDirectory()) walk(full, base, out);
      else if (f.endsWith('.js')) out.push(path.relative(base, full).split(path.sep).join('/'));
    }
    return out;
  }

  return function build(web, tests, outName) {
    const vendor = fs.readFileSync(path.join(web, 'vendor', 'preact-htm.js'), 'utf8');
    const modules = walk(path.join(web, 'app'), web, []);
    let text = PRELUDE + `__modules['vendor'] = function (__exports) {\n${SHIM.replace('__HTM__', () => htmSource(vendor))}\n};\n`;
    const linemap = [];
    const lineCount = (s) => (s.match(/\n/g) || []).length;
    for (const id of modules) {
      const body = transform(fs.readFileSync(path.join(web, id), 'utf8'), id);
      text += `__modules[${JSON.stringify(id)}] = function (__exports) { 'use strict';\n`;
      const start = lineCount(text) + 2; // getters line, then source line 1
      text += body + '\n};\n';
      linemap.push({ module: id, start, end: start + lineCount(body) });
    }
    for (const t of tests || []) {
      const src = fs.readFileSync(path.join(web, t), 'utf8');
      const start = lineCount(text) + 1;
      text += src + '\n';
      linemap.push({ module: t, start, end: start + lineCount(src) });
    }
    const outDir = path.join(web, 'tests', 'out');
    fs.mkdirSync(outDir, { recursive: true });
    fs.writeFileSync(path.join(outDir, outName), text);
    fs.writeFileSync(path.join(outDir, 'linemap.json'), JSON.stringify(linemap, null, 1));
    return { modules, lines: lineCount(text), locate: (n) => { const e = linemap.find((x) => n >= x.start && n <= x.end); return e ? `${e.module}:${n - e.start + 1}` : `bundle:${n}`; } };
  };
}
