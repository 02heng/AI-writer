/**
 * 极简 Markdown 渲染（仅本地书库阅读）：标题、粗体、斜体、行内代码、列表、段落、围栏代码块。
 * 先转义再替换，避免 XSS。
 */
(function (global) {
  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function inlineFormat(line) {
    let t = escapeHtml(line);
    t = t.replace(/`([^`]+)`/g, '<code>$1</code>');
    t = t.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    t = t.replace(/\*([^*]+)\*/g, '<em>$1</em>');
    return t;
  }

  function renderMarkdownLite(raw) {
    const lines = String(raw || '').split(/\r?\n/);
    const blocks = [];
    let i = 0;
    let inCode = false;
    const codeBuf = [];
    const paraBuf = [];

    function flushPara() {
      if (!paraBuf.length) return;
      const text = paraBuf.join('\n').trimEnd();
      if (text) blocks.push({ type: 'p', html: inlineFormat(text).replace(/\n/g, '<br/>') });
      paraBuf.length = 0;
    }

    while (i < lines.length) {
      const line = lines[i];

      if (line.trim().startsWith('```')) {
        flushPara();
        if (!inCode) {
          inCode = true;
          codeBuf.length = 0;
        } else {
          blocks.push({ type: 'pre', text: codeBuf.join('\n') });
          codeBuf.length = 0;
          inCode = false;
        }
        i += 1;
        continue;
      }

      if (inCode) {
        codeBuf.push(line);
        i += 1;
        continue;
      }

      const h3 = line.match(/^###\s+(.+)$/);
      const h2 = line.match(/^##\s+(.+)$/);
      const h1 = line.match(/^#\s+(.+)$/);
      if (h3 || h2 || h1) {
        flushPara();
        const level = h1 ? 1 : h2 ? 2 : 3;
        const content = (h1 || h2 || h3)[1];
        blocks.push({ type: 'h', level, html: inlineFormat(content) });
        i += 1;
        continue;
      }

      if (/^\s*[-*]\s+/.test(line)) {
        flushPara();
        const items = [];
        while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
          items.push(inlineFormat(lines[i].replace(/^\s*[-*]\s+/, '')));
          i += 1;
        }
        blocks.push({ type: 'ul', items });
        continue;
      }

      if (line.trim() === '') {
        flushPara();
        i += 1;
        continue;
      }

      paraBuf.push(line);
      i += 1;
    }

    flushPara();
    if (inCode && codeBuf.length) {
      blocks.push({ type: 'pre', text: codeBuf.join('\n') });
    }

    const container = document.createElement('div');
    container.className = 'md-root';
    for (const b of blocks) {
      if (b.type === 'h') {
        const el = document.createElement(`h${b.level + 1}`);
        el.className = 'md-h';
        el.innerHTML = b.html;
        container.appendChild(el);
      } else if (b.type === 'p') {
        const el = document.createElement('p');
        el.className = 'md-p';
        el.innerHTML = b.html;
        container.appendChild(el);
      } else if (b.type === 'pre') {
        const el = document.createElement('pre');
        el.className = 'md-pre';
        const code = document.createElement('code');
        code.textContent = b.text;
        el.appendChild(code);
        container.appendChild(el);
      } else if (b.type === 'ul') {
        const ul = document.createElement('ul');
        ul.className = 'md-ul';
        for (const it of b.items) {
          const li = document.createElement('li');
          li.innerHTML = it;
          ul.appendChild(li);
        }
        container.appendChild(ul);
      }
    }
    return container;
  }

  global.renderMarkdownLite = renderMarkdownLite;
})(typeof window !== 'undefined' ? window : globalThis);
