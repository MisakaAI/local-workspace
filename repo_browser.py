"""Read-only Git object access for the repository browser."""

from __future__ import annotations

import re
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from pathlib import Path


MAX_FILE_BYTES = 1024 * 1024
MAX_DIFF_BYTES = 2 * 1024 * 1024


class GitBrowseError(Exception):
    pass


def git(repo: Path, *args: str, limit: int = 4 * 1024 * 1024) -> bytes:
    """Keep Git output bounded, including for unexpectedly large objects."""
    command = ["git", "--git-dir=" + str(repo), "--no-pager", *args]
    with tempfile.TemporaryFile() as error_file:
        try:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=error_file)
        except OSError as exc:
            raise GitBrowseError("无法运行 Git") from exc
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(process.stdout.read, limit + 1)
                try:
                    output = future.result(timeout=20)
                    if len(output) > limit:
                        raise GitBrowseError("内容过大，无法在浏览器中展示")
                    process.wait(timeout=20)
                except (FutureTimeout, subprocess.TimeoutExpired):
                    raise GitBrowseError("Git 读取超时") from None
                finally:
                    # Unblock the reader before the executor waits for it to exit.
                    if process.poll() is None:
                        process.kill()
                    process.wait()
        finally:
            process.stdout.close()
        if process.returncode:
            error_file.seek(0)
            raise GitBrowseError(error_file.read(2048).decode("utf-8", "replace").strip() or "Git 读取失败")
        return output


def refs(repo: Path) -> tuple[list[dict], str | None]:
    output = git(repo, "for-each-ref", "--format=%(refname)", "refs/heads", "refs/tags")
    names = output.decode("utf-8", "replace").splitlines()
    items = [{"value": name, "label": name.split("/", 2)[2],
              "kind": "分支" if name.startswith("refs/heads/") else "Tag"}
             for name in names]
    try:
        head = git(repo, "symbolic-ref", "--quiet", "HEAD").decode().strip()
    except GitBrowseError:
        head = None
    default = head if head in names else (items[0]["value"] if items else None)
    return items, default


def resolve_ref(repo: Path, selected: str | None, items: list[dict], default: str | None) -> tuple[str | None, str | None]:
    ref = selected if selected is not None else default
    if ref is None:
        return None, None
    if ref not in {item["value"] for item in items}:
        raise GitBrowseError("分支或 Tag 不存在")
    oid = git(repo, "rev-parse", "--verify", ref + "^{commit}").decode().strip()
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", oid):
        raise GitBrowseError("无效的提交")
    return ref, oid


def valid_path(path: str) -> bool:
    return bool(path) and "\0" not in path and all(part not in ("", ".", "..") for part in path.split("/"))


def object_type(repo: Path, oid: str, path: str) -> str:
    if not valid_path(path):
        raise GitBrowseError("无效的文件路径")
    return git(repo, "cat-file", "-t", oid + ":" + path).decode().strip()


def tree(repo: Path, oid: str, path: str = "") -> list[dict]:
    spec = oid + (":" + path if path else "")
    result = git(repo, "ls-tree", "-z", "-l", spec)
    entries = []
    for raw in result.split(b"\0"):
        if not raw:
            continue
        meta, name = raw.split(b"\t", 1)
        mode, kind, object_id, size = meta.decode("ascii").split(" ", 3)
        size = size.strip()
        filename = name.decode("utf-8", "replace")
        entries.append({"name": filename, "path": path + "/" + filename if path else filename,
                        "type": kind, "mode": mode, "size": None if size == "-" else int(size),
                        "oid": object_id})
    return sorted(entries, key=lambda item: (item["type"] != "tree", item["name"].casefold()))


def file_content(repo: Path, oid: str, path: str) -> tuple[str | None, int]:
    spec = oid + ":" + path
    size = int(git(repo, "cat-file", "-s", spec).decode())
    if size > MAX_FILE_BYTES:
        return None, size
    raw = git(repo, "cat-file", "blob", spec, limit=MAX_FILE_BYTES)
    if b"\0" in raw:
        return None, size
    try:
        return raw.decode("utf-8"), size
    except UnicodeDecodeError:
        return None, size


def blob_size(repo: Path, oid: str, path: str) -> int:
    return int(git(repo, "cat-file", "-s", oid + ":" + path).decode())


def stream_blob(repo: Path, oid: str, path: str):
    """Stream the complete blob without the preview size limit."""
    spec = oid + ":" + path
    try:
        process = subprocess.Popen(
            ["git", "--git-dir=" + str(repo), "--no-pager", "cat-file", "blob", spec],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        raise GitBrowseError("无法运行 Git") from exc

    def chunks():
        try:
            while chunk := process.stdout.read(64 * 1024):
                yield chunk
        finally:
            process.stdout.close()
            if process.poll() is None:
                process.kill()
            process.wait()

    return chunks()


def commits(repo: Path, oid: str, path: str = "", *, skip: int = 0, count: int = 50) -> list[dict]:
    args = ["log", "-z", "-n", str(count), "--skip=" + str(skip),
            "--format=%H%x00%an%x00%aI%x00%s", oid]
    if path:
        args.extend(["--", path])
    raw = git(repo, *args).decode("utf-8", "replace")
    fields = raw.split("\0")
    return [{"oid": fields[i], "author": fields[i + 1], "date": fields[i + 2],
             "subject": fields[i + 3]} for i in range(0, len(fields) - 4, 4)]


def commit_info(repo: Path, oid: str) -> dict:
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", oid):
        raise GitBrowseError("无效的提交编号")
    raw = git(repo, "show", "-s", "--format=%H%x00%an%x00%ae%x00%aI%x00%B", oid).decode("utf-8", "replace")
    fields = raw.split("\0", 4)
    if len(fields) != 5:
        raise GitBrowseError("无法读取提交")
    return dict(zip(("oid", "author", "email", "date", "message"), fields))


def commit_diff(repo: Path, oid: str) -> tuple[list[dict], bool]:
    try:
        raw = git(repo, "show", "--format=", "--no-ext-diff", "--no-textconv", "--no-color", "--find-renames",
                  "--first-parent", "--patch", oid, limit=MAX_DIFF_BYTES)
        truncated = False
    except GitBrowseError as exc:
        if "内容过大" not in str(exc):
            raise
        return [], True
    lines = []
    for line in raw.decode("utf-8", "replace").splitlines():
        kind = "context"
        if line.startswith("diff --git "):
            kind = "header"
        elif line.startswith("@@"):
            kind = "hunk"
        elif line.startswith("+") and not line.startswith("+++"):
            kind = "add"
        elif line.startswith("-") and not line.startswith("---"):
            kind = "delete"
        elif line.startswith(("index ", "new file mode", "deleted file mode", "rename ", "similarity ", "Binary files")):
            kind = "meta"
        lines.append({"text": line, "kind": kind})
    return lines, truncated
