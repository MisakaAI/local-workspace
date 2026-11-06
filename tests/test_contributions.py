import os
import subprocess
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import contributions as c


class CalendarTests(unittest.TestCase):
    def test_leap_year_zero_days_and_streak_across_months(self):
        repos = {'a': {'days': {'2024-02-28': 1, '2024-02-29': 2, '2024-03-01': 1}},
                 'b': {'days': {'2024-02-29': 3}}}
        data = c.calendar_data(repos, '2024', date(2026, 1, 1))
        self.assertEqual(len(data['days']), 366)
        self.assertEqual((data['total'], data['active_days'], data['longest_streak']), (7, 3, 3))
        self.assertEqual(data['days'][59]['repos'], {'a': 2, 'b': 3})
        self.assertEqual(data['days'][0]['count'], 0)

    def test_recent_is_365_days_and_crosses_year(self):
        repos = {'a': {'days': {'2024-12-31': 1, '2025-01-01': 1}}}
        result = c.calendar_data(repos, 'recent', date(2025, 1, 2))
        self.assertEqual(len(result['days']), 365)
        self.assertEqual(result['longest_streak'], 2)
        self.assertEqual(result['end'], '2025-01-02')
        self.assertEqual(len(c.calendar_data({}, '2023', date.today())['days']), 365)


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.root = self.base / 'repos'
        self.root.mkdir()
        self.cache = self.base / 'ssd-cache'
        self.cache.mkdir()
        self.repo = self.root / 'project.git'
        subprocess.run(['git', 'init', '--bare', '--initial-branch=main', str(self.repo)], check=True, capture_output=True)
        self.services = []

    def tearDown(self):
        for service in self.services:
            service.executor.shutdown(wait=True)
        self.tmp.cleanup()

    def command(self, *args, input=None, env=None):
        return subprocess.run(['git', '--git-dir=' + str(self.repo), *args], input=input,
                              env=env, capture_output=True, check=True).stdout.decode().strip()

    def commit(self, email='one@example.com', name='Alice', stamp='2024-02-29T23:30:00-02:00', parents=(), branch='main'):
        tree = self.command('mktree', input=b'')
        env = {**os.environ, 'GIT_AUTHOR_NAME': name, 'GIT_AUTHOR_EMAIL': email,
               'GIT_COMMITTER_NAME': 'Committer', 'GIT_COMMITTER_EMAIL': 'committer@example.com',
               'GIT_AUTHOR_DATE': stamp, 'GIT_COMMITTER_DATE': '2026-01-01T00:00:00Z'}
        args = ['commit-tree', tree, '-m', name + stamp]
        for parent in parents:
            args += ['-p', parent]
        commit = self.command(*args, env=env)
        self.command('update-ref', 'refs/heads/' + branch, commit)
        return commit

    def service(self, **kwargs):
        service = c.Contributions(self.root, self.cache, **kwargs)
        self.services.append(service)
        return service

    def refresh(self, service):
        service.request_refresh(force=True)
        service.future.result(timeout=10)
        return service.snapshot

    def test_authors_timezone_and_default_branch_only(self):
        first = self.commit()
        second = self.commit(email='two@example.com', stamp='2024-03-01T02:00:00Z', parents=[first])
        self.commit(email='other@example.com', name='Other', stamp='2024-03-01T03:00:00Z', parents=[second])
        self.commit(stamp='2024-03-02T00:00:00Z', branch='feature')
        counts = c.count_history(self.repo, self.command('rev-parse', 'main'), ZoneInfo('Asia/Shanghai'),
                                 frozenset(), frozenset(['one@example.com', 'two@example.com']), self.cache)
        self.assertEqual(counts, {'2024-03-01': 2})
        named = c.count_history(self.repo, self.command('rev-parse', 'main'), ZoneInfo('UTC'),
                                frozenset(['other']), frozenset(), self.cache)
        self.assertEqual(named, {'2024-03-01': 1})

    def test_cache_head_change_restart_and_filter_isolation(self):
        first = self.commit(stamp='2024-01-01T00:00:00Z')
        service = self.service(emails=['ONE@example.com'])
        original = c.count_history
        with patch.object(c, 'count_history', wraps=original) as read:
            self.refresh(service)
            self.refresh(service)
            self.assertEqual(read.call_count, 1)
            self.commit(stamp='2024-01-02T00:00:00Z', parents=[first])
            self.refresh(service)
            self.assertEqual(read.call_count, 2)
            restored = self.service(emails=['one@example.com'])
            self.refresh(restored)
            self.assertEqual(read.call_count, 2)
            self.assertEqual(sum(restored.snapshot['repos']['project.git']['days'].values()), 2)
            filtered = self.service(emails=['nobody@example.com'])
            self.refresh(filtered)
            self.assertEqual(read.call_count, 3)
            self.assertEqual(filtered.snapshot['repos']['project.git']['days'], {})
            # Switching years uses daily summaries and performs no log traversal.
            service.data('2024')
            service.data('2025')
            self.assertEqual(read.call_count, 3)

    def test_same_hash_in_two_repositories_counts_twice(self):
        self.commit(stamp='2024-01-01T00:00:00Z')
        subprocess.run(['git', 'clone', '--bare', str(self.repo), str(self.root / 'copy-without-suffix')], capture_output=True, check=True)
        snapshot = self.refresh(self.service())
        result = c.calendar_data(snapshot['repos'], '2024', date.today())
        self.assertEqual(result['total'], 2)

    def test_timezone_moves_author_date_across_leap_day(self):
        head = self.commit(stamp='2024-02-29T23:30:00Z')
        for zone, expected in [('UTC', '2024-02-29'), ('Asia/Shanghai', '2024-03-01')]:
            self.assertEqual(c.count_history(self.repo, head, ZoneInfo(zone), (), (), self.cache), {expected: 1})

    def test_flask_routes_and_existing_management(self):
        import main as portal
        service = self.service()
        client = portal.app.test_client()
        with patch.object(portal, 'ROOT', self.root), patch.object(portal, 'activity', service):
            page = client.get('/')
            self.assertEqual(page.status_code, 200)
            self.assertIn(b'activity-grid', page.data)
            self.assertIn(b'copy-command', page.data)
            self.assertIsNone(service.future)  # HTML rendering never scans history.
            with client.get('/static/contributions.js') as asset:
                self.assertEqual(asset.status_code, 200)
            self.assertEqual(client.get('/api/activity?period=invalid').status_code, 400)
            self.assertEqual(client.post('/api/activity/refresh').status_code, 403)
            result = client.get('/api/activity?period=2024')
            self.assertEqual(result.status_code, 200)
            self.assertEqual(len(result.json['days']), 366)
            service.future.result(timeout=10)
            response = client.post('/api/activity/refresh', data={'csrf': portal.CSRF_TOKEN})
            self.assertEqual(response.status_code, 202)
            service.future.result(timeout=10)
            response = client.post('/create', data={'csrf': portal.CSRF_TOKEN, 'name': 'NewRepo'}, headers={'Accept': 'application/json'})
            self.assertEqual(response.status_code, 201)
            response = client.post('/describe/NewRepo.git', data={'csrf': portal.CSRF_TOKEN, 'description': 'New description'})
            self.assertEqual(response.status_code, 303)
            self.assertEqual((self.root / 'NewRepo.git' / 'description').read_text().strip(), 'New description')

    def test_merge_reachable_commit_counts_once_and_force_push_replaces(self):
        first = self.commit(stamp='2024-01-01T00:00:00Z')
        second = self.commit(stamp='2024-01-02T00:00:00Z', parents=[first])
        side = self.commit(stamp='2024-01-03T00:00:00Z', parents=[first], branch='side')
        self.commit(stamp='2024-01-04T00:00:00Z', parents=[second, side])
        service = self.service()
        self.assertEqual(sum(self.refresh(service)['repos']['project.git']['days'].values()), 4)
        self.command('update-ref', 'refs/heads/main', first)
        self.assertEqual(sum(self.refresh(service)['repos']['project.git']['days'].values()), 1)

    def test_empty_missing_default_and_broken_repository_are_isolated(self):
        service = self.service()
        empty = self.refresh(service)
        self.assertEqual(empty['repos']['project.git']['days'], {})
        self.assertEqual(empty['errors'], [])
        self.commit(branch='other')
        (self.root / 'broken.git').mkdir()
        result = self.refresh(service)
        self.assertTrue(any('project.git' in error for error in result['errors']))
        self.assertTrue(any('broken.git' in error for error in result['errors']))
        # A detached HEAD does not implicitly include a different branch.
        head = self.command('rev-parse', 'other')
        (self.repo / 'HEAD').write_text(head + '\n')
        self.assertTrue(self.refresh(service)['errors'])

    def test_cache_corruption_and_ssd_guard(self):
        service = self.service()
        service.cache_file.write_text('{bad json')
        restored = self.service()
        self.assertEqual(restored.snapshot['repos'], {})
        with self.assertRaises(ValueError):
            c.Contributions(self.root, self.root / 'cache')

    def test_background_request_returns_while_worker_runs(self):
        import threading
        service = self.service()
        entered, release = threading.Event(), threading.Event()
        def slow_refresh():
            entered.set()
            release.wait(5)
        with patch.object(service, '_refresh', side_effect=slow_refresh) as refresh:
            self.assertTrue(service.request_refresh(force=True))
            self.assertTrue(entered.wait(2))
            try:
                self.assertTrue(service.data()['refreshing'])
                service.request_refresh(force=True)
                self.assertEqual(refresh.call_count, 1)
                self.assertIn('全仓库', service.data()['label'])
            finally:
                release.set()
            service.future.result(timeout=5)


if __name__ == '__main__':
    unittest.main()
