/* Local, inert rich-text rendering. Conversation records remain plain Markdown. */
(function () {
  "use strict";
  const t = (...args) => window.DrugStudioUI.t(...args);
  const escape = value => String(value ?? "").replace(/[&<>"']/g, ch => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[ch]));
  const md = window.markdownit({ html: false, breaks: true, linkify: true, typographer: false });
  const escaped = (source, at) => {
    let count = 0;
    while (at > 0 && source[--at] === '\\') count++;
    return count % 2 === 1;
  };
  function closing(source, marker, from) {
    let at = from;
    while ((at = source.indexOf(marker, at)) !== -1) {
      if (!escaped(source, at)) return at;
      at += marker.length;
    }
    return -1;
  }
  function formula(source, display) {
    if (source.length > 16000) return `<code>${escape(source)}</code>`;
    try {
      return window.katex.renderToString(source, {
        displayMode: display, throwOnError: false, strict: 'ignore', trust: false,
        maxExpand: 1000, maxSize: 12, output: 'htmlAndMathml'
      });
    } catch (_) { return `<code class="math-fallback">${escape(source)}</code>`; }
  }
  md.inline.ruler.before('escape', 'math', (state, silent) => {
    const start = state.pos, source = state.src;
    const marker = ['$$', '\\(', '\\[', '$'].find(value => source.startsWith(value, start));
    if (!marker) return false;
    const endMarker = marker === '\\(' ? '\\)' : marker === '\\[' ? '\\]' : marker;
    const from = start + marker.length, end = closing(source, endMarker, from);
    if (end < 0 || end === from) return false;
    // Avoid treating prices such as "$5 and $10" as a formula.
    if (marker === '$' && (/\s/.test(source[from]) || /\s/.test(source[end - 1]) || /\d/.test(source[end + 1] || '') || source.slice(from, end).includes('\n'))) return false;
    if (!silent) {
      const token = state.push('math_inline', 'math', 0);
      token.content = source.slice(from, end);
      token.meta = { display: marker === '$$' || marker === '\\[' };
    }
    state.pos = end + endMarker.length;
    return true;
  });
  md.block.ruler.before('fence', 'math_block', (state, start, end, silent) => {
    const first = state.src.slice(state.bMarks[start] + state.tShift[start], state.eMarks[start]);
    const marker = first.startsWith('$$') ? '$$' : first.startsWith('\\[') ? '\\[' : null;
    if (!marker) return false;
    const endMarker = marker === '$$' ? '$$' : '\\]';
    let text = first.slice(marker.length), line = start, at = closing(text, endMarker, 0);
    while (at < 0 && ++line < end) {
      if (state.sCount[line] < state.blkIndent) return false;
      text += '\n' + state.src.slice(state.bMarks[line] + state.tShift[line], state.eMarks[line]);
      at = closing(text, endMarker, 0);
    }
    if (at < 0 || text.slice(at + endMarker.length).trim()) return false;
    if (silent) return true;
    const token = state.push('math_block', 'div', 0);
    token.block = true; token.content = text.slice(0, at).trim(); token.map = [start, line + 1];
    state.line = line + 1;
    return true;
  }, { alt: ['paragraph', 'reference', 'blockquote', 'list'] });
  md.renderer.rules.math_inline = (tokens, index) => formula(tokens[index].content, tokens[index].meta.display);
  md.renderer.rules.math_block = (tokens, index) => `<div class="chat-math" tabindex="0">${formula(tokens[index].content, true)}</div>\n`;
  md.renderer.rules.fence = (tokens, index) => {
    const token = tokens[index], language = token.info.trim().split(/\s+/)[0].toLowerCase();
    let code = escape(token.content);
    if (language && window.hljs.getLanguage(language) && token.content.length < 100000) {
      try { code = window.hljs.highlight(token.content, { language, ignoreIllegals: true }).value; } catch (_) { /* Plain code remains readable. */ }
    }
    return `<div class="chat-code"><div class="chat-code-toolbar"><span>${escape(language || t('代码'))}</span><button type="button" data-copy-code>${escape(t('复制代码'))}</button></div><pre tabindex="0"><code class="hljs">${code}</code></pre></div>\n`;
  };
  md.renderer.rules.code_block = (tokens, index) => md.renderer.rules.fence(tokens, index);
  md.renderer.rules.table_open = () => `<div class="chat-table-scroll" tabindex="0" role="region" aria-label="${escape(t('表格'))}"><table>`;
  md.renderer.rules.table_close = () => '</table></div>\n';
  const originalLink = md.renderer.rules.link_open || ((tokens, index, options, env, renderer) => renderer.renderToken(tokens, index, options));
  md.renderer.rules.link_open = (tokens, index, options, env, renderer) => {
    tokens[index].attrSet('target', '_blank');
    tokens[index].attrSet('rel', 'noopener noreferrer');
    return originalLink(tokens, index, options, env, renderer);
  };
  const originalImage = md.renderer.rules.image;
  md.renderer.rules.image = (tokens, index, options, env, renderer) => {
    tokens[index].attrSet('loading', 'lazy');
    tokens[index].attrSet('referrerpolicy', 'no-referrer');
    return originalImage(tokens, index, options, env, renderer);
  };
  function render(source) {
    try {
      return window.DOMPurify.sanitize(md.render(String(source ?? '')), {
        USE_PROFILES: { html: true, svg: true, mathMl: true },
        ADD_ATTR: ['target'], FORBID_TAGS: ['style', 'form', 'input', 'textarea', 'iframe']
      });
    } catch (_) { return `<div class="chat-plain">${escape(source)}</div>`; }
  }
  async function copy(text) {
    if (navigator.clipboard?.writeText) {
      try { await navigator.clipboard.writeText(text); return; } catch (_) { /* HTTP and denied clipboard permissions use a local fallback. */ }
    }
    const previous = document.activeElement, area = document.createElement('textarea');
    area.value = text; area.style.cssText = 'position:fixed;left:-9999px;top:0';
    document.body.append(area); area.select();
    const copied = document.execCommand('copy'); area.remove(); previous?.focus({ preventScroll: true });
    if (!copied) throw new Error(t('复制失败，请手动选择文本'));
  }
  window.DrugStudioChat = { render, copy };
})();
