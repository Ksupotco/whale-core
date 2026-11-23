/* Static checks (run with run_javascript; uses fs only):
   - index.html: no inline script / inline handlers; loads /app/main.js (module) and /app/styles.css
   - app JS: no eval / new Function / innerHTML / dangerouslySetInnerHTML / document.write / string timers
   - every import resolves to a file and every imported name is exported there
   - CSS @import targets exist; files stay under 600 lines */
function staticChecks(fs, path, web) {
  const problems = [];
  const info = [];
  const read = (p) => fs.readFileSync(path.join(web, p), 'utf8');
  // index.html
  const index = read('index.html');
  const scripts = [...index.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/gi)];
  scripts.forEach((m) => {
    if (!/\bsrc=/.test(m[1])) problems.push('index.html: inline <script> found');
    if (m[2].trim()) problems.push('index.html: <script> with a body');
  });
  if (!/<script[^>]+type="module"[^>]+src="\/app\/main\.js"/.test(index)) problems.push('index.html: does not load /app/main.js as a module');
  if (!/href="\/app\/styles\.css"/.test(index)) problems.push('index.html: does not load /app/styles.css');
  if (/\son[a-z]+\s*=/i.test(index)) problems.push('index.html: inline event handler attribute');
  if (/javascript:/i.test(index)) problems.push('index.html: javascript: URL');
  info.push(`index.html: ${scripts.length} script tag(s), all external`);

  // JS modules
  const files = [];
  (function walk(dir) {
    for (const f of fs.readdirSync(path.join(web, dir)).sort()) {
      const rel = dir + '/' + f;
      if (fs.statSync(path.join(web, rel)).isDirectory()) walk(rel); else files.push(rel);
    }
  })('app');
  const js = files.filter((f) => f.endsWith('.js'));
  const css = files.filter((f) => f.endsWith('.css'));
  const exportsOf = {};
  const sources = {};
  for (const f of js) {
    const src = read(f);
    sources[f] = src;
    const names = new Set();
    for (const m of src.matchAll(/^export\s+(?:async\s+)?(?:function\*?|const|let|var|class)\s+(\w+)/gm)) names.add(m[1]);
    for (const m of src.matchAll(/^export\s*\{([^}]*)\}/gm)) m[1].split(',').map((s) => s.trim()).filter(Boolean).forEach((s) => names.add(s.includes(' as ') ? s.split(' as ')[1].trim() : s));
    exportsOf[f] = names;
  }
  const vendorExports = new Set(['h', 'html', 'render', 'Component', 'createContext', 'useState', 'useReducer', 'useEffect', 'useLayoutEffect', 'useRef', 'useImperativeHandle', 'useMemo', 'useCallback', 'useContext', 'useDebugValue', 'useErrorBoundary']);
  const vendorSrc = read('vendor/preact-htm.js');
  const vendorDecl = /export\{([^}]*)\}/.exec(vendorSrc);
  const actualVendor = new Set(vendorDecl ? vendorDecl[1].split(',').map((s) => s.split(' as ')[1].trim()) : []);
  vendorExports.forEach((n) => { if (!actualVendor.has(n)) problems.push(`vendor does not export ${n}`); });
  const FORBIDDEN = [[/\beval\s*\(/, 'eval('], [/new\s+Function\s*\(/, 'new Function'], [/\.innerHTML\s*=/, 'innerHTML ='], [/outerHTML\s*=/, 'outerHTML ='],
    [/dangerouslySetInnerHTML/, 'dangerouslySetInnerHTML'], [/document\.write\s*\(/, 'document.write'], [/set(?:Timeout|Interval)\(\s*['"`]/, 'string timer'],
    [/insertAdjacentHTML/, 'insertAdjacentHTML'], [/\bstyle="[^"$]*"/, 'static string style attribute in template']];
  let imports = 0;
  for (const f of js) {
    const src = sources[f];
    const lines = src.split('\n').length;
    if (lines > 600) problems.push(`${f}: ${lines} lines (> 600)`);
    FORBIDDEN.forEach(([re, label]) => { if (re.test(src)) problems.push(`${f}: forbidden ${label}`); });
    for (const m of src.matchAll(/^\s*import\s*\{([^}]*)\}\s*from\s*['"]([^'"]+)['"]/gm)) {
      imports++;
      const spec = m[2];
      let target;
      if (spec === '/vendor/preact-htm.js') target = 'vendor';
      else if (spec.startsWith('/app/')) target = spec.slice(1);
      else if (spec.startsWith('.')) target = path.posix.normalize(path.posix.join(path.posix.dirname(f), spec));
      else { problems.push(`${f}: bare import ${spec} (no bundler!)`); continue; }
      if (target !== 'vendor' && !exportsOf[target]) { problems.push(`${f}: import target missing ${spec}`); continue; }
      const avail = target === 'vendor' ? actualVendor : exportsOf[target];
      m[1].split(',').map((s) => s.trim()).filter(Boolean).forEach((s) => {
        const name = s.split(' as ')[0].trim();
        if (!avail.has(name)) problems.push(`${f}: '${name}' is not exported by ${spec}`);
      });
    }
    if (/^\s*import\s+\w+\s+from/m.test(src)) problems.push(`${f}: default import (modules use named exports)`);
    if (/\bimport\s*\(/.test(src)) info.push(`${f}: uses dynamic import()`);
  }
  // CSS
  const entry = read('app/styles.css');
  for (const m of entry.matchAll(/@import\s+url\("\.\/([^"]+)"\)/g)) if (!fs.existsSync(path.join(web, 'app', m[1]))) problems.push(`styles.css: missing ${m[1]}`);
  for (const f of css) {
    const src = read(f);
    const open = (src.match(/\{/g) || []).length; const close = (src.match(/\}/g) || []).length;
    if (open !== close) problems.push(`${f}: unbalanced braces ${open}/${close}`);
    if (src.split('\n').length > 600) problems.push(`${f}: > 600 lines`);
  }
  info.push(`${js.length} JS modules, ${imports} named imports checked, ${css.length} CSS files`);
  return { problems, info, files: js.map((f) => `${f} ${sources[f].split('\n').length}`) };
}
