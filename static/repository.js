function rewriteMarkdownHref(href, markdownDir, repoBase, ref) {
  if (!href || href.startsWith('#') || href.startsWith('?') || href.startsWith('/') || href.startsWith('//')) return href;
  if (/^(javascript|data):/i.test(href)) return null;
  if (/^[a-z][a-z\d+.-]*:/i.test(href)) return href;

  const match = /^([^?#]*)(\?[^#]*)?(#.*)?$/.exec(href);
  if (!match || !match[1]) return href;
  const parts = markdownDir ? markdownDir.split('/') : [];
  for (const encoded of match[1].split('/')) {
    let part;
    try { part = decodeURIComponent(encoded); } catch (_) { return null; }
    if (!part || part === '.') continue;
    if (part === '..') {
      if (!parts.length) return null;
      parts.pop();
    } else {
      if (part.includes('/') || part.includes('\\') || part.includes('\0')) return null;
      parts.push(part);
    }
  }
  const target = repoBase.replace(/\/$/, '') + (parts.length ? '/open/' + parts.map(encodeURIComponent).join('/') : '');
  const params = new URLSearchParams(match[2] || '');
  if (ref) params.set('ref', ref);
  const query = params.toString();
  return target + (query ? '?' + query : '') + (match[3] || '');
}

function headingSlug(text) {
  return text.toLowerCase().replace(/[^\p{L}\p{N}\p{M}\s_-]/gu, '').trim().replace(/\s+/g, '-');
}

if (typeof module !== 'undefined') module.exports = { rewriteMarkdownHref, headingSlug };

if (typeof document !== 'undefined') document.addEventListener('DOMContentLoaded', () => {
  const configElement = document.getElementById('repository-config');
  if (configElement && window.Vue && window.ElementPlus) {
    const config = JSON.parse(configElement.textContent);
    Vue.createApp({
      data() { return { selectedRef: config.ref, refs: config.refs }; },
      methods: {
        changeRef(value) {
          const url = new URL(config.treeUrl, location.href);
          url.searchParams.set('ref', value);
          location.assign(url.href);
        },
      },
    }).use(ElementPlus).mount('#repository-ref-picker');

    Vue.createApp({
      methods: {
        async copyCloneAddress() {
          const address = config.clonePath;
          try {
            if (!navigator.clipboard?.writeText) throw new Error('Clipboard API unavailable');
            await navigator.clipboard.writeText(address);
            ElementPlus.ElMessage.success('已复制 Clone 地址');
          } catch (error) {
            const field = document.createElement('textarea');
            field.value = address;
            field.style.position = 'fixed';
            field.style.opacity = '0';
            document.body.appendChild(field);
            field.focus();
            field.select();
            try {
              if (!document.execCommand('copy')) throw new Error('Copy failed');
              ElementPlus.ElMessage.success('已复制 Clone 地址');
            } catch (fallbackError) {
              window.prompt('浏览器未允许自动复制，请手动复制 Clone 地址：', address);
              ElementPlus.ElMessage.warning('自动复制失败，请手动复制地址');
            } finally {
              field.remove();
            }
          }
        },
      },
    }).use(ElementPlus).mount('#repository-clone');

    Vue.createApp({
      data() {
        return { description: config.description, draft: '', visible: false, saving: false };
      },
      methods: {
        openEditor() {
          this.draft = this.description;
          this.visible = true;
        },
        async saveDescription() {
          if (this.saving) return;
          this.saving = true;
          try {
            const response = await fetch(config.describeUrl, {
              method: 'POST',
              headers: { Accept: 'application/json' },
              body: new URLSearchParams({ csrf: config.csrf, description: this.draft }),
            });
            if (!response.ok) throw new Error('Save failed');
            const result = await response.json();
            this.description = result.description;
            this.visible = false;
            ElementPlus.ElMessage.success('仓库说明已更新');
          } catch (error) {
            ElementPlus.ElMessage.error('保存失败，请重试');
          } finally {
            this.saving = false;
          }
        },
      },
    }).use(ElementPlus).mount('#repository-description-editor');

    if (document.getElementById('markdown-view-picker')) {
      Vue.createApp({
        data() { return { markdownView: 'preview' }; },
        methods: {
          changeMarkdownView(value) {
            document.getElementById('readme-content').hidden = value === 'source';
            document.getElementById('markdown-source').hidden = value !== 'source';
          },
        },
      }).use(ElementPlus).mount('#markdown-view-picker');
    }
  }

  const markdownData = document.getElementById('readme-data');
  const markdownTarget = document.getElementById('readme-content');
  if (markdownData && markdownTarget) {
    const source = JSON.parse(markdownData.textContent);
    if (window.markdownit && window.DOMPurify) {
      const markdown = window.markdownit({ html: false, linkify: true, breaks: false });
      markdown.core.ruler.push('heading_ids', state => {
        const seen = new Map();
        state.tokens.forEach((token, index) => {
          if (token.type !== 'heading_open') return;
          const inline = state.tokens[index + 1];
          const title = (inline.children || []).filter(child => ['text', 'code_inline'].includes(child.type))
            .map(child => child.content).join('');
          const slug = headingSlug(title);
          if (!slug) return;
          const count = seen.get(slug) || 0;
          seen.set(slug, count + 1);
          token.attrSet('id', count ? slug + '-' + count : slug);
        });
      });
      const html = markdown.render(source);
      markdownTarget.innerHTML = window.DOMPurify.sanitize(html);
      markdownTarget.querySelectorAll('a[href]').forEach(anchor => {
        const rewritten = rewriteMarkdownHref(anchor.getAttribute('href'),
          markdownTarget.dataset.markdownDir, markdownTarget.dataset.repoBase, markdownTarget.dataset.ref);
        if (rewritten === null) anchor.removeAttribute('href');
        else anchor.setAttribute('href', rewritten);
      });
    } else {
      const pre = document.createElement('pre');
      pre.textContent = source;
      markdownTarget.appendChild(pre);
    }
  }
  document.querySelectorAll('.source-code code').forEach(block => {
    if (window.hljs && block.textContent.length < 100000) window.hljs.highlightElement(block);
  });
});
