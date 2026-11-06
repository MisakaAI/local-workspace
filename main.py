#!/usr/bin/env python3
"""跨 Windows / Linux / WSL 的本地 Git 裸仓库管理面板。

依赖：pip install flask tzdata
Windows: py -3 main.py
Linux/WSL: python3 main.py
浏览器：http://127.0.0.1:43827

使用启动程序时的当前工作目录扫描仓库。
复制出的路径默认使用当前工作目录，可通过 GIT_WINDOWS_ROOT、GIT_LINUX_ROOT 覆盖。
"""

import hmac
import ntpath
import os
import posixpath
import re
import secrets
import shutil
import subprocess
from pathlib import Path

from flask import Flask, Response, abort, redirect, render_template, request, url_for
from contributions import Contributions, default_cache_dir
import repo_browser as browser

ROOT = Path.cwd()
WINDOWS_ROOT = ntpath.normpath(os.environ.get("GIT_WINDOWS_ROOT", str(ROOT)))
LINUX_ROOT = posixpath.normpath(os.environ.get("GIT_LINUX_ROOT", str(ROOT)))
IS_WINDOWS = os.name == "nt"
PLATFORM_LABEL = "Windows" if IS_WINDOWS else "Linux / WSL"
PORT = int(os.environ.get("GIT_PORT", "43827"))

NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
WINDOWS_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
CSRF_TOKEN = secrets.token_urlsafe(32)
app = Flask(__name__)
activity = Contributions(
    ROOT, os.environ.get("GIT_ACTIVITY_CACHE", default_cache_dir()),
    names=os.environ.get("GIT_AUTHOR_NAMES", "").split(","),
    emails=os.environ.get("GIT_AUTHOR_EMAILS", "").split(","),
    timezone_name=os.environ.get("GIT_ACTIVITY_TIMEZONE", "Asia/Shanghai"),
)


def is_bare_repo(path: Path) -> bool:
    """只接受目录内的真实裸仓库，不跟随符号链接。"""
    if not path.is_dir() or path.is_symlink():
        return False
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--is-bare-repository"],
            capture_output=True, encoding="utf-8", errors="replace", check=False,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and result.stdout.strip() == "true"


def clean_description(raw: str) -> str:
    """gitweb 原生 description 文件推荐单行纯文本。"""
    return " ".join(raw.split())[:300]


def clone_paths(repo_name: str) -> tuple[str, str]:
    """路径使用 ntpath / posixpath，与程序当前 OS 无关。"""
    return ntpath.join(WINDOWS_ROOT, repo_name), posixpath.join(LINUX_ROOT, repo_name)


def check_root() -> None:
    if not ROOT.is_dir():
        abort(500, f"仓库目录不存在：{ROOT}。请检查当前工作目录是否可用。")


def require_csrf() -> None:
    if not hmac.compare_digest(request.form.get("csrf", ""), CSRF_TOKEN):
        abort(403, "无效的请求令牌")


def valid_name(name: str) -> bool:
    """跨平台命名：禁止 Windows 保留设备名和大小写冲突。"""
    if not NAME_PATTERN.fullmatch(name) or name.endswith("."):
        return False
    return name.split(".", 1)[0].upper() not in WINDOWS_RESERVED_NAMES


def valid_existing_name(name: str) -> bool:
    """允许已有仓库的名称，但必须是启动目录下的单个目录名。"""
    return (name.lower().endswith(".git")
            and not any(char in name for char in ("/", "\\", "\0"))
            and not ntpath.splitdrive(name)[0])


def validate_new_name(raw: str) -> tuple[str, str, int]:
    name = raw.strip()
    if name.lower().endswith(".git"):
        name = name[:-4]
    if not valid_name(name):
        return name, "仓库名应为 1–64 个字符，以字母或数字开头，仅包含英文字母、数字、点、下划线、连字符；不能以点结尾或使用 Windows 保留名称。", 400
    # Windows 不区分大小写，Linux 也拒绝同名大小写变体。
    if any(child.name.casefold() == f"{name}.git".casefold() for child in ROOT.iterdir()):
        return name, "该仓库名称已被占用（大小写视为相同）", 409
    return name, "名称可用", 200


@app.get("/check-name")
def check_name():
    check_root()
    _, message, status = validate_new_name(request.args.get("name", ""))
    return {"available": status == 200, "message": message}


@app.get("/")
def index():
    check_root()
    repos = []
    for path in sorted(ROOT.iterdir(), key=lambda p: p.name.casefold()):
        if not path.name.lower().endswith(".git") or not is_bare_repo(path):
            continue
        desc_file = path / "description"
        try:
            desc = desc_file.read_text(encoding="utf-8", errors="replace").strip() if desc_file.is_file() else ""
        except OSError:
            desc = ""
        if desc.startswith("Unnamed repository"):
            desc = ""
        windows, linux = clone_paths(path.name)
        repos.append({"name": path.name, "description": desc, "windows": windows, "linux": linux})
    return render_template(
        "index.html", repos=repos, csrf=CSRF_TOKEN, is_windows=IS_WINDOWS,
    )


def browser_context(name: str) -> dict:
    check_root()
    if not valid_existing_name(name):
        abort(404)
    repo = ROOT / name
    if not is_bare_repo(repo):
        abort(404)
    try:
        ref_items, default = browser.refs(repo)
        ref, oid = browser.resolve_ref(repo, request.args.get("ref"), ref_items, default)
    except browser.GitBrowseError as exc:
        abort(404, str(exc))
    desc_file = repo / "description"
    try:
        description = desc_file.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        description = ""
    if description.startswith("Unnamed repository"):
        description = ""
    windows, linux = clone_paths(name)
    return {"name": name, "repo": repo, "description": description,
            "refs": ref_items, "ref": ref, "oid": oid, "csrf": CSRF_TOKEN,
            "clone_path": windows if IS_WINDOWS else linux}


@app.get("/repo/<name>")
@app.get("/repo/<name>/tree/<path:subpath>")
def repo_tree(name: str, subpath: str = ""):
    ctx = browser_context(name)
    if subpath and not browser.valid_path(subpath):
        abort(404)
    if subpath and not ctx["oid"]:
        abort(404)
    try:
        if subpath and ctx["oid"]:
            if browser.object_type(ctx["repo"], ctx["oid"], subpath) != "tree":
                abort(404)
        entries = browser.tree(ctx["repo"], ctx["oid"], subpath) if ctx["oid"] else []
        history = browser.commits(ctx["repo"], ctx["oid"], subpath, count=1) if ctx["oid"] else []
        readme = None
        if ctx["oid"]:
            entry = next((entry for entry in entries if entry["name"].casefold() == "readme.md" and entry["type"] == "blob"), None)
            if entry:
                readme, _ = browser.file_content(ctx["repo"], ctx["oid"], entry["path"])
    except browser.GitBrowseError as exc:
        abort(404, str(exc))
    return render_template("repository.html", **ctx, view="tree", path=subpath,
                           entries=entries, history=history, readme=readme)


@app.get("/repo/<name>/blob/<path:subpath>")
def repo_blob(name: str, subpath: str):
    ctx = browser_context(name)
    if not ctx["oid"] or not browser.valid_path(subpath):
        abort(404)
    try:
        if browser.object_type(ctx["repo"], ctx["oid"], subpath) != "blob":
            abort(404)
        content, size = browser.file_content(ctx["repo"], ctx["oid"], subpath)
    except browser.GitBrowseError as exc:
        abort(404, str(exc))
    return render_template("repository.html", **ctx, view="blob", path=subpath,
                           content=content, size=size)


def serve_repo_file(name: str, subpath: str, *, download: bool):
    ctx = browser_context(name)
    if not ctx["oid"] or not browser.valid_path(subpath):
        abort(404)
    try:
        if browser.object_type(ctx["repo"], ctx["oid"], subpath) != "blob":
            abort(404)
        if download:
            size = browser.blob_size(ctx["repo"], ctx["oid"], subpath)
            body = () if request.method == "HEAD" else browser.stream_blob(ctx["repo"], ctx["oid"], subpath)
        else:
            content, size = browser.file_content(ctx["repo"], ctx["oid"], subpath)
            if content is None:
                abort(404)
            body = b"" if request.method == "HEAD" else content.encode("utf-8")
    except browser.GitBrowseError as exc:
        abort(404, str(exc))
    response = Response(body, content_type="application/octet-stream" if download else "text/plain; charset=utf-8")
    response.content_length = size
    response.headers.set("Content-Disposition", "attachment" if download else "inline",
                         filename=subpath.rsplit("/", 1)[-1])
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.get("/repo/<name>/raw/<path:subpath>")
def repo_raw(name: str, subpath: str):
    return serve_repo_file(name, subpath, download=False)


@app.get("/repo/<name>/download/<path:subpath>")
def repo_download(name: str, subpath: str):
    return serve_repo_file(name, subpath, download=True)


@app.get("/repo/<name>/open/<path:subpath>")
def repo_open(name: str, subpath: str):
    """Resolve links in rendered Markdown to a file or directory at the selected ref."""
    ctx = browser_context(name)
    if not ctx["oid"] or not browser.valid_path(subpath):
        abort(404)
    try:
        kind = browser.object_type(ctx["repo"], ctx["oid"], subpath)
    except browser.GitBrowseError as exc:
        abort(404, str(exc))
    if kind == "tree":
        return redirect(url_for("repo_tree", name=name, subpath=subpath, ref=ctx["ref"]), code=302)
    if kind == "blob":
        return redirect(url_for("repo_blob", name=name, subpath=subpath, ref=ctx["ref"]), code=302)
    abort(404)


@app.get("/repo/<name>/commits")
def repo_commits(name: str):
    ctx = browser_context(name)
    subpath = request.args.get("path", "")
    if subpath and not browser.valid_path(subpath):
        abort(404)
    page_text = request.args.get("page", "1")
    if not re.fullmatch(r"[1-9][0-9]{0,4}", page_text):
        abort(400, "页码无效")
    page = int(page_text)
    try:
        records = browser.commits(ctx["repo"], ctx["oid"], subpath,
                                  skip=(page - 1) * 50, count=51) if ctx["oid"] else []
    except browser.GitBrowseError as exc:
        abort(404, str(exc))
    return render_template("repository.html", **ctx, view="commits", path=subpath,
                           history=records[:50], has_more=len(records) > 50, page=page)


@app.get("/repo/<name>/commit/<commit_oid>")
def repo_commit(name: str, commit_oid: str):
    ctx = browser_context(name)
    try:
        info = browser.commit_info(ctx["repo"], commit_oid)
        diff, diff_truncated = browser.commit_diff(ctx["repo"], commit_oid)
    except browser.GitBrowseError as exc:
        abort(404, str(exc))
    return render_template("repository.html", **ctx, view="commit", path="",
                           commit=info, diff=diff, diff_truncated=diff_truncated)


@app.get("/api/activity")
def activity_data():
    period = request.args.get("period", "recent")
    if period != "recent" and (not re.fullmatch(r"[0-9]{4}", period) or not 1 <= int(period) <= 9998):
        abort(400, "年份无效")
    response = app.json.response(activity.data(period))
    response.headers['Cache-Control'] = 'no-store'
    return response


@app.post("/api/activity/refresh")
def refresh_activity():
    require_csrf()
    activity.request_refresh(force=True)
    return {"refreshing": True}, 202


@app.post("/create")
def create():
    require_csrf()
    check_root()
    wants_json = request.accept_mimetypes.best == "application/json"

    def fail(status: int, message: str):
        if wants_json:
            return {"message": message}, status
        abort(status, message)

    name, message, status = validate_new_name(request.form.get("name", ""))
    if status != 200:
        return fail(status, message)
    target = ROOT / f"{name}.git"
    # 原子占用目录，避免检查后并发创建同名仓库时重新初始化已有仓库。
    try:
        target.mkdir()
    except FileExistsError:
        return fail(409, "该仓库名称已被占用（大小写视为相同）")
    result = subprocess.run(
        ["git", "init", "--bare", "--initial-branch=main", str(target)],
        capture_output=True, encoding="utf-8", errors="replace", check=False,
        timeout=30,
    )
    if result.returncode:
        return fail(500, f"创建失败：{result.stderr.strip()}")
    (target / "description").write_text(
        clean_description(request.form.get("description", "")) + "\n", encoding="utf-8"
    )
    if wants_json:
        return {"url": url_for("index")}, 201
    return redirect(url_for("index"), code=303)


@app.post("/describe/<name>")
def describe(name: str):
    require_csrf()
    check_root()
    if not valid_existing_name(name):
        abort(404)
    target = ROOT / name
    if not is_bare_repo(target):
        abort(404)
    description = clean_description(request.form.get("description", ""))
    (target / "description").write_text(description + "\n", encoding="utf-8")
    if request.accept_mimetypes.best == "application/json":
        return {"description": description}
    return redirect(url_for("repo_tree", name=name), code=303)


if __name__ == "__main__":
    if not shutil.which("git"):
        raise SystemExit("未找到 git 命令。Windows 请安装 Git for Windows，并确保 git.exe 在 PATH 中。")
    if not ROOT.is_dir():
        raise SystemExit(f"找不到仓库目录：{ROOT}\n请检查当前工作目录是否可用。")
    print(f"平台：{PLATFORM_LABEL}\n仓库目录：{ROOT}\n访问：http://127.0.0.1:{PORT}")
    app.run(host="127.0.0.1", port=PORT, debug=False)
