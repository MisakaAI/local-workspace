# LOCAL WORKSPACE / 本地仓库管理

本地仓库管理是一个用于管理本地 Git 裸仓库的轻量 Web 页面。  
它可以列出和创建仓库、编辑仓库说明、复制 Clone 命令，并浏览文件、分支、标签和提交历史。

> [!CAUTION]
> **本项目仅供本地使用。禁止将服务监听地址改为 `0.0.0.0`，也不要将端口暴露到公网。**
> **页面具有创建仓库和修改仓库说明的功能，公网访问会带来安全风险。**

## 快速开始

需要 Python 3.9+ 和 Git。先用以下任一方法获取项目代码。

**方法一：克隆到新目录**

```sh
git clone https://github.com/MisakaAI/local-workspace.git local-workspace
cd local-workspace
```

**方法二：在已有文件的目录中初始化并关联远程**

在该目录中执行（以下以远端分支 `main` 为例；  
如果远端默认分支是 `master`，将命令中的 `main` 都改为 `master`）：

```sh
git init
git remote add origin https://github.com/MisakaAI/local-workspace.git
git fetch origin
git checkout -b main origin/main
```

这会在当前目录创建 `.git`，获取远端内容并检出到当前目录。如果已有同名文件，检出可能因冲突而失败；请先确认或备份这些文件。

获取项目代码后，在项目目录中安装依赖：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

在**存放裸仓库的目录**中启动程序；程序会扫描启动时的当前目录：

```sh
cd /path/to/repositories
/path/to/local-workspace/.venv/bin/python /path/to/local-workspace/main.py
```

然后打开 <http://127.0.0.1:43827>。  
Windows 可用 `py -3 -m venv .venv` 创建虚拟环境，并使用 `.venv\Scripts\python.exe` 和对应的 Windows 路径执行后续命令。

页面会列出当前目录下以 `.git` 结尾的裸仓库。  
可以在页面中创建仓库，也可以手动执行 `git init --bare project.git`。  
已有仓库可通过页面复制的命令克隆。

## 常用配置

通过环境变量配置，修改后重新启动程序：

| 变量 | 用途 |
| --- | --- |
| `GIT_PORT` | 页面端口，默认 `43827` |
| `GIT_WINDOWS_ROOT`、`GIT_LINUX_ROOT` | 复制 Clone 命令时使用的仓库路径；默认采用启动目录 |
| `GIT_AUTHOR_EMAILS` | 按作者邮箱统计个人提交活动，多个邮箱用逗号分隔；留空时显示所有仓库的提交活动 |
| `GIT_AUTHOR_NAMES` | 可选的作者姓名筛选，多个姓名用逗号分隔 |
| `GIT_ACTIVITY_TIMEZONE` | 活动统计时区，默认 `Asia/Shanghai` |

程序默认只监听本机地址。  
页面组件已包含在 `static/js` 中，浏览器无需访问 CDN。
