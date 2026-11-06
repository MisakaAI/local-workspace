(() => {
  const media = window.matchMedia('(prefers-color-scheme: dark)');
  let preference = 'auto';
  try { preference = localStorage.getItem('local-workspace-theme') || 'auto'; } catch (_) {}
  if (!['auto', 'dark', 'light'].includes(preference)) preference = 'auto';

  function applyTheme() {
    const theme = preference === 'auto' ? (media.matches ? 'dark' : 'light') : preference;
    document.documentElement.dataset.theme = theme;
    const button = document.getElementById('theme-toggle');
    if (button) {
      const label = theme === 'light' ? '切换到深色主题' : '切换到浅色主题';
      button.setAttribute('aria-label', label);
      button.title = label;
    }
  }

  media.addEventListener('change', applyTheme);
  applyTheme();
  document.addEventListener('DOMContentLoaded', () => {
    const button = document.getElementById('theme-toggle');
    applyTheme();
    button.addEventListener('click', () => {
      preference = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
      try { localStorage.setItem('local-workspace-theme', preference); } catch (_) {}
      applyTheme();
    });
  });
})();
