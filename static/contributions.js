(() => {
  const panel = document.getElementById('activity');
  const controls = Vue.reactive({ period: 'recent', years: [], refreshing: false });
  let grid, detail, status, tooltip;
  let timer, version = 0, selectedDate;
  function hideTooltip() {
    tooltip.hidden = true;
  }
  function showTooltip(tile, day) {
    tooltip.textContent = `${day.date} · ${day.count} 次提交`;
    tooltip.hidden = false;
    const rect = tile.getBoundingClientRect();
    const width = tooltip.offsetWidth;
    const height = tooltip.offsetHeight;
    const center = rect.left + rect.width / 2;
    const left = Math.max(8, Math.min(center - width / 2, window.innerWidth - width - 8));
    const below = rect.top < height + 12;
    tooltip.dataset.placement = below ? 'bottom' : 'top';
    tooltip.style.left = `${left}px`;
    tooltip.style.top = `${below ? rect.bottom + 8 : rect.top - height - 8}px`;
    tooltip.style.setProperty('--arrow-left', `${center - left}px`);
  }
  function showDay(day) {
    selectedDate = day.date;
    for (const tile of grid.querySelectorAll('button')) tile.setAttribute('aria-pressed', String(tile.dataset.date === selectedDate));
    detail.replaceChildren();
    const heading = document.createElement('strong');
    heading.textContent = `${day.date} · ${day.count} 次提交`;
    detail.append(heading);
    const list = document.createElement('ul');
    for (const [name, count] of Object.entries(day.repos).sort((a, b) => b[1] - a[1])) {
      const item = document.createElement('li');
      item.textContent = `${name}：${count} 次`;
      list.append(item);
    }
    detail.append(list);
    if (!day.count) detail.append(document.createTextNode(' 当天没有提交。'));
  }
  function render(data) {
    document.getElementById('activity-range').textContent = `${data.start} — ${data.end}`;
    document.getElementById('activity-total').textContent = `${data.total} 次`;
    document.getElementById('activity-active-days').textContent = `${data.active_days} 天`;
    document.getElementById('activity-longest-streak').textContent = `${data.longest_streak} 天`;
    controls.years = data.years;
    status.textContent = `${data.timezone} · ${data.refreshing ? '后台更新中…' : (data.updated_at ? '更新于 ' + new Date(data.updated_at).toLocaleString() : '尚无缓存')}`;
    document.getElementById('activity-errors').textContent = data.errors.length ? '部分统计未更新，可能显示上次缓存：\n' + data.errors.join('\n') : '';
    controls.refreshing = data.refreshing;
    hideTooltip();
    grid.replaceChildren();
    grid.style.gridTemplateColumns = '28px';
    ['一', '二', '三', '四', '五', '六', '日'].forEach((name, i) => {
      const label = document.createElement('span');
      label.className = 'weekday'; label.style.gridRow = i + 2; label.textContent = name;
      grid.append(label);
    });
    const first = new Date(data.start + 'T00:00:00Z');
    const offset = (first.getUTCDay() + 6) % 7;
    data.days.forEach((day, index) => {
      const date = new Date(day.date + 'T00:00:00Z');
      const column = Math.floor((index + offset) / 7) + 2;
      if (index === 0 || date.getUTCDate() === 1) {
        const label = document.createElement('span');
        label.className = 'month'; label.textContent = (date.getUTCMonth() + 1) + '月';
        label.style.gridColumn = column; label.style.gridRow = 1; grid.append(label);
      }
      const tile = document.createElement('button');
      tile.type = 'button'; tile.dataset.date = day.date;
      tile.dataset.level = day.count === 0 ? 0 : Math.min(4, Math.ceil(day.count / 3));
      tile.style.gridColumn = column; tile.style.gridRow = (index + offset) % 7 + 2;
      tile.setAttribute('aria-label', `${day.date} · ${day.count} 次提交`);
      tile.setAttribute('aria-pressed', String(day.date === selectedDate));
      tile.addEventListener('pointerenter', () => showTooltip(tile, day));
      tile.addEventListener('pointerleave', hideTooltip);
      tile.addEventListener('focus', () => showTooltip(tile, day));
      tile.addEventListener('blur', hideTooltip);
      tile.addEventListener('click', () => showDay(day)); grid.append(tile);
    });
    const selected = data.days.find(day => day.date === selectedDate);
    if (selected) showDay(selected);
    else detail.textContent = '点击日期查看各仓库提交分布。';
  }
  async function load() {
    clearTimeout(timer);
    const current = ++version;
    try {
      const response = await fetch(panel.dataset.api + '?' + new URLSearchParams({period: controls.period}), {cache: 'no-store'});
      if (!response.ok) throw new Error('统计请求失败');
      const data = await response.json();
      if (current !== version) return;
      render(data);
      timer = setTimeout(load, data.refreshing ? 1500 : 60000);
    } catch (error) {
      if (current !== version) return;
      status.textContent = '加载失败，请重试刷新缓存。';
      controls.refreshing = false;
      timer = setTimeout(load, 10000);
    }
  }
  async function refreshCache() {
    controls.refreshing = true;
    try {
      const response = await fetch(panel.dataset.refresh, {method: 'POST', body: new URLSearchParams({csrf: panel.dataset.csrf})});
      if (!response.ok) throw new Error('刷新请求失败');
      await load();
    } catch (error) { status.textContent = '刷新失败，请重试。'; controls.refreshing = false; }
  }
  Vue.createApp({
    setup() { return { controls, load, refreshCache, String }; },
    mounted() {
      grid = document.getElementById('activity-grid');
      detail = document.getElementById('activity-detail');
      status = document.getElementById('activity-status');
      tooltip = document.getElementById('activity-tooltip');
      panel.querySelector('.activity-scroll').addEventListener('scroll', hideTooltip);
      window.addEventListener('scroll', hideTooltip, { passive: true });
      window.addEventListener('resize', hideTooltip);
      load();
    },
  }).use(ElementPlus).mount(panel);
})();
