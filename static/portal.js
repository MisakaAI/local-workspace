(() => {
  const config = JSON.parse(document.getElementById('portal-config').textContent);
  const repositories = Array.from(document.querySelectorAll('.repo'), card =>
    `${card.dataset.name} ${card.dataset.description}`.toLocaleLowerCase());
  Vue.createApp({
    data() {
      return {
        search: '',
        createVisible: false,
        name: '', description: '', nameFeedback: '', nameState: '',
        nameCheckVersion: 0, creating: false,
      };
    },
    computed: {
      visibleCount() { return repositories.filter((_, index) => this.matchesRepo(index)).length; },
    },
    methods: {
      matchesRepo(index) {
        return repositories[index].includes(this.search.trim().toLocaleLowerCase());
      },
      clearNameFeedback() {
        ++this.nameCheckVersion;
        this.nameFeedback = '';
        this.nameState = '';
      },
      openCreate() {
        if (!this.creating) {
          this.name = '';
          this.description = '';
          this.clearNameFeedback();
        }
        this.createVisible = true;
      },
      async checkName() {
        const version = ++this.nameCheckVersion;
        const name = this.name;
        this.nameFeedback = '正在检查名称…';
        this.nameState = '';
        try {
          const response = await fetch(config.checkNameUrl + '?' + new URLSearchParams({ name }), { cache: 'no-store' });
          if (!response.ok) throw new Error('Name check failed');
          const result = await response.json();
          if (version !== this.nameCheckVersion || this.name !== name) return false;
          this.nameFeedback = (result.available ? '✓ ' : '') + result.message;
          this.nameState = result.available ? 'success' : 'error';
          return result.available;
        } catch (error) {
          if (version === this.nameCheckVersion) {
            this.nameFeedback = '名称检查失败，请重试';
            this.nameState = 'error';
          }
          return false;
        }
      },
      async createRepo() {
        if (this.creating) return;
        this.creating = true;
        try {
          if (!await this.checkName()) return;
          const response = await fetch(config.createUrl, {
            method: 'POST',
            body: new URLSearchParams({ csrf: config.csrf, name: this.name, description: this.description }),
            headers: { Accept: 'application/json' },
          });
          const result = await response.json();
          if (!response.ok) {
            this.nameFeedback = result.message;
            this.nameState = 'error';
            return;
          }
          window.location.assign(result.url);
        } catch (error) {
          this.nameFeedback = '创建请求失败，请刷新页面确认仓库是否已创建后重试';
          this.nameState = 'error';
        } finally {
          this.creating = false;
        }
      },
      async copyCommand(event) {
        // 双引号保留 PowerShell、CMD、Bash 中包含空格的本地路径。
        const text = 'git clone "' + event.currentTarget.closest('.repo').dataset.path + '"';
        try {
          if (!navigator.clipboard?.writeText) throw new Error('Clipboard API unavailable');
          await navigator.clipboard.writeText(text);
          ElementPlus.ElMessage.success('已复制 git clone 命令');
        } catch (error) {
          const field = document.createElement('textarea');
          field.value = text;
          field.style.position = 'fixed';
          field.style.opacity = '0';
          document.body.appendChild(field);
          field.focus();
          field.select();
          try {
            if (!document.execCommand('copy')) throw new Error('Copy failed');
            ElementPlus.ElMessage.success('已复制 git clone 命令');
          } catch (fallbackError) {
            window.prompt('浏览器未允许自动复制，请手动复制命令：', text);
            ElementPlus.ElMessage.warning('自动复制失败，请手动复制命令');
          } finally {
            field.remove();
          }
        }
      },
    },
  }).use(ElementPlus).mount('#portal');
})();
