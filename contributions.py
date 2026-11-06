"""Read-only bare repository activity, with an SSD-side cache and one background worker."""
import hashlib
import json
import os
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


def git(repo, *args, stdout=subprocess.PIPE):
    return subprocess.run(
        ['git', '--git-dir=' + str(repo), *args], stdout=stdout,
        stderr=subprocess.PIPE, timeout=300, check=True,
    )


def null_fields(stream):
    pending = b''
    while chunk := stream.read(65536):
        parts = (pending + chunk).split(b'\0')
        pending = parts.pop()
        yield from parts
    if pending:
        raise ValueError('Incomplete git log record')


def count_history(repo, head, zone, names, emails, scratch):
    """Walk the exact captured HEAD; never prune by committer dates."""
    days = {}
    seen = set()
    with tempfile.TemporaryFile(dir=scratch) as output:
        git(repo, 'log', '--no-show-signature', '-z', '--format=%H%x00%aI%x00%an%x00%ae', head, '--', stdout=output)
        output.seek(0)
        fields = iter(null_fields(output))
        for commit in fields:
            stamp, name, email = (next(fields).decode('utf-8', 'replace') for _ in range(3))
            if commit in seen:
                continue
            seen.add(commit)
            if (names or emails) and name.casefold() not in names and email.casefold() not in emails:
                continue
            day = datetime.fromisoformat(stamp).astimezone(zone).date().isoformat()
            days[day] = days.get(day, 0) + 1
    return days


def calendar_data(repos, period, today):
    if period == 'recent':
        start, end = today - timedelta(days=364), today
    else:
        year = int(period)
        start, end = date(year, 1, 1), date(year, 12, 31)
    days = []
    streak = longest = total = active = 0
    current = start
    while current <= end:
        key = current.isoformat()
        breakdown = {name: repo['days'][key] for name, repo in repos.items() if repo.get('days', {}).get(key)}
        count = sum(breakdown.values())
        streak = streak + 1 if count else 0
        longest = max(longest, streak)
        total += count
        active += bool(count)
        days.append({'date': key, 'count': count, 'repos': breakdown})
        current += timedelta(days=1)
    return {'start': start.isoformat(), 'end': end.isoformat(), 'days': days,
            'total': total, 'active_days': active, 'longest_streak': longest}


class Contributions:
    def __init__(self, root, cache_dir, names=(), emails=(), timezone_name='Asia/Shanghai', interval=60):
        self.root = Path(root).resolve()
        self.cache_dir = Path(cache_dir).expanduser().resolve()
        # A bad override must not put cache files on the repository disk tree.
        if self.cache_dir == self.root or self.root in self.cache_dir.parents:
            raise ValueError('GIT_ACTIVITY_CACHE must be outside GIT_ROOT (use an SSD directory)')
        self.names = frozenset(n.strip().casefold() for n in names if n.strip())
        self.emails = frozenset(e.strip().casefold() for e in emails if e.strip())
        self.zone = ZoneInfo(timezone_name)
        self.timezone_name = timezone_name
        config = [2, str(self.root), sorted(self.names), sorted(self.emails), timezone_name]
        key = hashlib.sha256(json.dumps(config).encode()).hexdigest()
        self.cache_file = self.cache_dir / (key + '.json')
        self.interval = interval
        self.lock = threading.Lock()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='git-activity')
        self.future = None
        self.last_check = float('-inf')
        self.snapshot = {'repos': {}, 'updated_at': None, 'errors': []}
        try:
            data = json.loads(self.cache_file.read_text(encoding='utf-8'))
            if not isinstance(data, dict) or not isinstance(data['repos'], dict):
                raise ValueError('Invalid cache')
            for repo in data['repos'].values():
                if not isinstance(repo['days'], dict) or not all(
                    isinstance(k, str) and isinstance(v, int) and v >= 0 for k, v in repo['days'].items()
                ):
                    raise ValueError('Invalid daily counts')
                for day in repo['days']:
                    if date.fromisoformat(day).isoformat() != day:
                        raise ValueError('Invalid cached date')
            self.snapshot = data
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            pass

    def request_refresh(self, force=False):
        with self.lock:
            running = self.future is not None and not self.future.done()
            if not running and (force or time.monotonic() - self.last_check >= self.interval):
                self.last_check = time.monotonic()
                self.future = self.executor.submit(self._refresh)
            return self.future is not None and not self.future.done()

    def _refresh(self):
        with self.lock:
            previous = self.snapshot['repos']
        repos, errors = {}, []
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            paths = sorted(self.root.iterdir(), key=lambda p: p.name.casefold())
            for path in paths:
                if path.is_symlink() or not path.is_dir():
                    continue
                if not path.name.lower().endswith('.git') and not ((path / 'HEAD').is_file() and (path / 'objects').is_dir()):
                    continue
                try:
                    if git(path, 'rev-parse', '--is-bare-repository').stdout.strip() != b'true':
                        continue
                    # Only symbolic HEAD pointing to a local branch defines a default branch.
                    ref = git(path, 'symbolic-ref', '-q', 'HEAD').stdout.decode().strip()
                    if not ref.startswith('refs/heads/'):
                        raise ValueError('HEAD 未指向默认分支')
                    resolved = subprocess.run(
                        ['git', '--git-dir=' + str(path), 'rev-parse', '--verify', 'HEAD^{commit}'],
                        capture_output=True, timeout=15,
                    )
                    if resolved.returncode:
                        # An unborn branch is an ordinary empty repository; a missing
                        # default branch in a populated repository is reported explicitly.
                        refs = git(path, 'for-each-ref', '--count=1', '--format=%(refname)').stdout.strip()
                        if refs:
                            raise ValueError('默认分支不存在或无法读取')
                        repos[path.name] = {'head': None, 'ref': ref, 'days': {}}
                        continue
                    head = resolved.stdout.decode().strip()
                    old = previous.get(path.name, {})
                    if old.get('head') == head and old.get('ref') == ref:
                        repos[path.name] = old
                    else:
                        repos[path.name] = {'head': head, 'ref': ref, 'days': count_history(
                            path, head, self.zone, self.names, self.emails, self.cache_dir)}
                except (OSError, subprocess.SubprocessError, ValueError, StopIteration) as exc:
                    errors.append(f'{path.name}: {exc}')
                    # Retain known data when a disk or repository fails temporarily.
                    if path.name in previous:
                        repos[path.name] = previous[path.name]
            snapshot = {'repos': repos, 'errors': errors,
                        'updated_at': datetime.now(timezone.utc).isoformat()}
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.cache_dir, delete=False) as output:
                temp = Path(output.name)
                json.dump(snapshot, output, ensure_ascii=False)
            try:
                os.replace(temp, self.cache_file)
            finally:
                temp.unlink(missing_ok=True)
            with self.lock:
                self.snapshot = snapshot
        except Exception as exc:
            with self.lock:
                self.snapshot = {**self.snapshot, 'errors': [f'统计刷新失败: {exc}']}

    def data(self, period='recent'):
        refreshing = self.request_refresh()
        with self.lock:
            snapshot = self.snapshot
        today = datetime.now(self.zone).date()
        result = calendar_data(snapshot['repos'], period, today)
        years = {today.year, max(1, today.year - 4)}
        for repo in snapshot['repos'].values():
            years.update(int(day[:4]) for day in repo['days'])
        result.update(refreshing=refreshing, updated_at=snapshot.get('updated_at'),
                      errors=snapshot.get('errors', []), years=list(range(max(years), min(years) - 1, -1)),
                      timezone=self.timezone_name,
                      label='个人贡献' if self.names or self.emails else '全仓库提交活动（未配置作者身份）')
        return result


def default_cache_dir():
    if os.name == 'nt':
        return Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData' / 'Local')) / 'local-workspace' / 'activity'
    return Path(os.environ.get('XDG_CACHE_HOME', Path.home() / '.cache')) / 'local-workspace' / 'activity'
