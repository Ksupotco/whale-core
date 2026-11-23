// @ts-check
/**
 * Markdown-lite → virtual DOM. Never produces HTML strings, so untrusted text
 * cannot inject markup. Supports: paragraphs, line breaks, headings, fenced
 * code (tolerates an unclosed fence while streaming), bullet/numbered lists,
 * blockquotes, horizontal rules, inline `code`, **bold**, *italic*, ~~strike~~,
 * [links](https://…) and bare URLs (http/https/mailto only).
 */
import { h } from '/vendor/preact-htm.js';

const SAFE_URL = /^(https?:\/\/|mailto:)/i;

/** @param {string} url */
export function safeUrl(url) {
  const u = String(url || '').trim();
  return SAFE_URL.test(u) ? u : '';
}

const INLINE_SRC = /(`+)([\s\S]*?[^`])\1(?!`)|\*\*([^*\n]+)\*\*|__([^_\n]+)__|~~([^~\n]+)~~|\*([^*\s][^*\n]*?)\*|\[([^\]\n]+)\]\(([^)\s]+)\)|((?:https?:\/\/|mailto:)[^\s<>()（）]+[^\s<>()（）.,;:!?，。；：！？'"」』])/g;

/** @param {string} text @param {string} keyBase @returns {any[]} */
export function inline(text, keyBase = 'i') {
  /** @type {any[]} */
  const out = [];
  let last = 0;
  let k = 0;
  // a fresh regex per call: nested calls (bold inside links…) must not share lastIndex
  const re = new RegExp(INLINE_SRC.source, 'g');
  let m;
  while ((m = re.exec(text))) {
    if (m.index > last) pushText(out, text.slice(last, m.index));
    const key = `${keyBase}-${k++}`;
    if (m[2] != null) out.push(h('code', { key, class: 'md-code' }, m[2]));
    else if (m[3] != null || m[4] != null) out.push(h('strong', { key }, inline(m[3] ?? m[4], key)));
    else if (m[5] != null) out.push(h('del', { key }, inline(m[5], key)));
    else if (m[6] != null) out.push(h('em', { key }, inline(m[6], key)));
    else if (m[7] != null) {
      const href = safeUrl(m[8]);
      out.push(href ? h('a', { key, href, target: '_blank', rel: 'noopener noreferrer' }, inline(m[7], key)) : h('span', { key }, m[7]));
    } else if (m[9] != null) {
      const href = safeUrl(m[9]);
      out.push(href ? h('a', { key, href, target: '_blank', rel: 'noopener noreferrer' }, m[9]) : m[9]);
    }
    last = re.lastIndex;
  }
  if (last < text.length) pushText(out, text.slice(last));
  return out;
}

/** @param {any[]} out @param {string} s */
function pushText(out, s) {
  const lines = s.split('\n');
  lines.forEach((line, i) => {
    if (i) out.push(h('br', { key: `br${out.length}` }));
    if (line) out.push(line);
  });
}

/**
 * @typedef {{type: 'p'|'h'|'code'|'ul'|'ol'|'quote'|'hr', text?: string, level?: number, lang?: string, items?: string[], open?: boolean, start?: number}} Block
 * @param {string} src @returns {Block[]}
 */
export function parseBlocks(src) {
  const lines = String(src ?? '').replace(/\r\n?/g, '\n').split('\n');
  /** @type {Block[]} */
  const blocks = [];
  let i = 0;
  /** @type {string[]} */
  let para = [];
  const flush = () => { if (para.length) { blocks.push({ type: 'p', text: para.join('\n') }); para = []; } };
  while (i < lines.length) {
    const line = lines[i];
    const fence = line.match(/^\s*(```|~~~)\s*([\w+#.-]*)\s*$/);
    if (fence) {
      flush();
      const body = [];
      i++;
      let closed = false;
      while (i < lines.length) {
        if (lines[i].trim().startsWith(fence[1])) { closed = true; i++; break; }
        body.push(lines[i]); i++;
      }
      blocks.push({ type: 'code', lang: fence[2] || '', text: body.join('\n'), open: !closed });
      continue;
    }
    const head = line.match(/^(#{1,4})\s+(.*)$/);
    if (head) { flush(); blocks.push({ type: 'h', level: head[1].length, text: head[2] }); i++; continue; }
    if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) { flush(); blocks.push({ type: 'hr' }); i++; continue; }
    if (/^\s*>\s?/.test(line)) {
      flush();
      const q = [];
      while (i < lines.length && /^\s*>\s?/.test(lines[i])) { q.push(lines[i].replace(/^\s*>\s?/, '')); i++; }
      blocks.push({ type: 'quote', text: q.join('\n') });
      continue;
    }
    const ul = /^\s*[-*+•]\s+/;
    const ol = /^\s*(\d+)[.)、]\s+/;
    if (ul.test(line) || ol.test(line)) {
      flush();
      const ordered = !ul.test(line);
      const start = ordered ? Number((line.match(ol) || [])[1] || 1) : 1;
      const items = [];
      while (i < lines.length && (ordered ? ol : ul).test(lines[i])) {
        let item = lines[i].replace(ordered ? ol : ul, '');
        i++;
        while (i < lines.length && /^\s{2,}\S/.test(lines[i]) && !ul.test(lines[i]) && !ol.test(lines[i])) { item += '\n' + lines[i].trim(); i++; }
        items.push(item);
      }
      blocks.push({ type: ordered ? 'ol' : 'ul', items, start });
      continue;
    }
    if (!line.trim()) { flush(); i++; continue; }
    para.push(line);
    i++;
  }
  flush();
  return blocks;
}

/** Render markdown-lite text to vnodes. @param {string} src */
export function renderMarkdown(src) {
  return parseBlocks(src).map((b, n) => {
    const key = `b${n}`;
    switch (b.type) {
      case 'h': return h(`h${Math.min(6, (b.level || 1) + 2)}`, { key, class: 'md-h' }, inline(b.text || '', key));
      case 'code': return h('div', { key, class: 'md-pre' + (b.open ? ' is-open' : '') },
        b.lang ? h('span', { class: 'md-lang' }, b.lang) : null,
        h('pre', null, h('code', null, b.text)));
      case 'ul': return h('ul', { key, class: 'md-list' }, (b.items || []).map((it, j) => h('li', { key: j }, inline(it, `${key}-${j}`))));
      case 'ol': return h('ol', { key, class: 'md-list', start: b.start }, (b.items || []).map((it, j) => h('li', { key: j }, inline(it, `${key}-${j}`))));
      case 'quote': return h('blockquote', { key, class: 'md-quote' }, inline(b.text || '', key));
      case 'hr': return h('hr', { key, class: 'md-hr' });
      default: return h('p', { key }, inline(b.text || '', key));
    }
  });
}
