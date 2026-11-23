"""Test bundler for the Whale web console (no node/npm needed).

The verification sandbox can run one JavaScript file but has no `vm`, `eval`
or `new Function`, so ES modules cannot be loaded dynamically. This script
turns every module under ``web/app`` into a CommonJS-style factory inside a
single script (``tests/out/bundle.js``) together with a test runtime:

* ``/vendor/preact-htm.js`` is replaced by a shim exporting React hooks and
  ``html = htm.bind(h)``, where the htm parser is extracted verbatim from the
  vendored file and ``h`` wraps ``React.createElement`` (Preact-style props
  such as ``class``/``for``/``stroke-width`` are mapped to React names).
* Each module's top-level ``import``/``export`` statements are rewritten;
  exports become live getters, so ordinary ES semantics hold.
* ``tests/out/linemap.json`` maps bundle lines back to source files, so a
  syntax error reported by the JS engine can be located.

Usage (from run_python):  exec(open('tmp/whale/web/tests/bundle.py').read()); build(WEB_DIR, [extra test files])
"""

from __future__ import annotations

import json
import os
import re

IMPORT_NAMED = re.compile(r"^\s*import\s*\{([^}]*)\}\s*from\s*['\"]([^'\"]+)['\"];?", re.M | re.S)
IMPORT_STAR = re.compile(r"^\s*import\s+\*\s+as\s+(\w+)\s+from\s*['\"]([^'\"]+)['\"];?", re.M)
IMPORT_DEFAULT = re.compile(r"^\s*import\s+(\w+)\s+from\s*['\"]([^'\"]+)['\"];?", re.M)
IMPORT_BARE = re.compile(r"^\s*import\s*['\"]([^'\"]+)['\"];?", re.M)
EXPORT_DECL = re.compile(r"^export\s+(async\s+function\*?|function\*?|const|let|var|class)\s+(\w+)", re.M)
EXPORT_LIST = re.compile(r"^export\s*\{([^}]*)\};?", re.M)
EXPORT_DEFAULT = re.compile(r"^export\s+default\s+", re.M)


def resolve(spec: str, importer: str) -> str:
    if spec.startswith("/vendor/"):
        return "vendor"
    if spec.startswith("/app/"):
        return spec[1:]
    if spec.startswith("."):
        base = os.path.dirname(importer)
        return os.path.normpath(os.path.join(base, spec)).replace("\\", "/")
    return "node:" + spec


def transform(src: str, mod_id: str) -> tuple[str, list[str]]:
    exports: list[tuple[str, str]] = []
    deps: list[str] = []

    def named(m: re.Match) -> str:
        target = resolve(m.group(2), mod_id)
        deps.append(target)
        parts = []
        for item in m.group(1).split(","):
            item = item.strip()
            if not item:
                continue
            if " as " in item:
                a, b = [x.strip() for x in item.split(" as ")]
                parts.append(f"{a}: {b}")
            else:
                parts.append(item)
        # keep the line count identical so line numbers stay meaningful
        nl = m.group(0).count("\n")
        return f"const {{ {', '.join(parts)} }} = __req({json.dumps(target)});" + "\n" * nl

    src = IMPORT_NAMED.sub(named, src)

    def star(m: re.Match) -> str:
        target = resolve(m.group(2), mod_id)
        deps.append(target)
        return f"const {m.group(1)} = __req({json.dumps(target)});"

    src = IMPORT_STAR.sub(star, src)

    def default(m: re.Match) -> str:
        target = resolve(m.group(2), mod_id)
        deps.append(target)
        return f"const {m.group(1)} = __req({json.dumps(target)}).default;"

    src = IMPORT_DEFAULT.sub(default, src)
    src = IMPORT_BARE.sub(lambda m: f"__req({json.dumps(resolve(m.group(1), mod_id))});", src)

    def decl(m: re.Match) -> str:
        exports.append((m.group(2), m.group(2)))
        return f"{m.group(1)} {m.group(2)}"

    src = EXPORT_DECL.sub(decl, src)

    def lst(m: re.Match) -> str:
        for item in m.group(1).split(","):
            item = item.strip()
            if not item:
                continue
            if " as " in item:
                a, b = [x.strip() for x in item.split(" as ")]
                exports.append((b, a))
            else:
                exports.append((item, item))
        return ""

    src = EXPORT_LIST.sub(lst, src)
    src = EXPORT_DEFAULT.sub("__exports.default = ", src)
    if re.search(r"^\s*(import|export)\s", src, re.M):
        bad = re.search(r"^\s*(import|export)\s.*$", src, re.M)
        raise SyntaxError(f"{mod_id}: unsupported module syntax: {bad.group(0)[:80] if bad else ''}")
    getters = "".join(
        f"Object.defineProperty(__exports, {json.dumps(name)}, {{ enumerable: true, get: () => {local} }});"
        for name, local in exports)
    return getters + "\n" + src, deps


def htm_source(vendor: str) -> str:
    start = vendor.index("var ce=function")
    end = vendor.index("}.bind(a);", start)
    return vendor[start:end + 1] + ";"


VENDOR_SHIM = r"""
const React = require('react');
const MAP = { class: 'className', for: 'htmlFor', tabindex: 'tabIndex', readonly: 'readOnly', maxlength: 'maxLength',
  minlength: 'minLength', autocomplete: 'autoComplete', autofocus: 'autoFocus', spellcheck: 'spellCheck',
  enterkeyhint: 'enterKeyHint', inputmode: 'inputMode', colspan: 'colSpan', rowspan: 'rowSpan', onDblClick: 'onDoubleClick',
  'xlink:href': 'xlinkHref', crossorigin: 'crossOrigin', srcset: 'srcSet', contenteditable: 'contentEditable' };
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
"""

PRELUDE = r"""
/* generated by tests/bundle.py — do not edit */
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
"""


def build(web_dir: str, tests: list[str] | None = None, out_name: str = "bundle.js") -> dict:
    app_dir = os.path.join(web_dir, "app")
    vendor = open(os.path.join(web_dir, "vendor", "preact-htm.js"), encoding="utf-8").read()
    parts = [PRELUDE]
    linemap = []
    line = PRELUDE.count("\n")
    shim = "__modules['vendor'] = function (__exports) {\n" + VENDOR_SHIM.replace("__HTM__", htm_source(vendor)) + "\n};\n"
    parts.append(shim)
    line += shim.count("\n")
    modules = []
    for root, _, files in os.walk(app_dir):
        for f in sorted(files):
            if f.endswith(".js"):
                full = os.path.join(root, f)
                modules.append(os.path.relpath(full, web_dir).replace("\\", "/"))
    for mod_id in sorted(modules):
        src = open(os.path.join(web_dir, mod_id), encoding="utf-8").read()
        body, _ = transform(src, mod_id)
        head = f"__modules[{json.dumps(mod_id)}] = function (__exports) {{ 'use strict';\n"
        parts.append(head)
        line += head.count("\n")
        start = line + 1
        body_lines = body.count("\n") + 1
        parts.append(body + "\n};\n")
        # +1 offset: the getters line precedes the source
        linemap.append({"module": mod_id, "start": start + 1, "end": start + body_lines})
        line += body.count("\n") + 2
    for t in tests or []:
        src = open(t, encoding="utf-8").read()
        linemap.append({"module": os.path.relpath(t, web_dir), "start": line + 1, "end": line + src.count("\n") + 1})
        parts.append(src + "\n")
        line += src.count("\n") + 1
    out_dir = os.path.join(web_dir, "tests", "out")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, out_name)
    open(out, "w", encoding="utf-8").write("".join(parts))
    json.dump(linemap, open(os.path.join(out_dir, "linemap.json"), "w"), indent=1)
    return {"bundle": out, "modules": modules, "lines": line}


def locate(bundle_line: int, web_dir: str) -> str:
    linemap = json.load(open(os.path.join(web_dir, "tests", "out", "linemap.json")))
    for e in linemap:
        if e["start"] <= bundle_line <= e["end"]:
            return f"{e['module']}:{bundle_line - e['start'] + 1}"
    return f"bundle:{bundle_line}"
