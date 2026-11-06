import os
import subprocess
import tempfile
import threading
import unittest
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import quote

import main as portal
import repo_browser


class BrowserTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.bare = self.root / 'project.git'
        self.work = self.root / 'work'
        self.git('init', '--bare', '--initial-branch=main', str(self.bare))
        self.git('init', '--initial-branch=main', str(self.work))
        (self.work / 'src').mkdir()
        (self.work / 'src' / 'hello.py').write_text('print("first")\n', encoding='utf-8')
        (self.work / 'README.md').write_text('# Project\n\n[Source](src/hello.py) [Folder](src/)\n\n<script>alert(1)</script>\n', encoding='utf-8')
        (self.work / 'src' / 'README.md').write_text('[Parent](../README.md) [Hello](hello.py)\n', encoding='utf-8')
        self.commit('Initial commit')
        self.git('tag', 'v1', cwd=self.work)
        (self.work / 'src' / 'hello.py').write_text('print("second")\n', encoding='utf-8')
        self.commit('Update source')
        self.git('branch', 'feature', cwd=self.work)
        self.git('push', str(self.bare), 'main', 'feature', 'v1', cwd=self.work)
        self.client = portal.app.test_client()
        self.root_patch = patch.object(portal, 'ROOT', self.root)
        self.root_patch.start()

    def tearDown(self):
        self.root_patch.stop()
        self.tmp.cleanup()

    def git(self, *args, cwd=None):
        env = {**os.environ, 'GIT_AUTHOR_NAME': 'Tester', 'GIT_AUTHOR_EMAIL': 'test@example.com',
               'GIT_COMMITTER_NAME': 'Tester', 'GIT_COMMITTER_EMAIL': 'test@example.com'}
        return subprocess.run(['git', *args], cwd=cwd, env=env, capture_output=True, check=True).stdout.decode().strip()

    def commit(self, message):
        self.git('add', '.', cwd=self.work)
        self.git('commit', '-m', message, cwd=self.work)

    def test_cards_tree_blob_history_diff_and_refs(self):
        index = self.client.get('/')
        self.assertIn(b'/repo/project.git', index.data)
        root = self.client.get('/repo/project.git')
        self.assertEqual(root.status_code, 200)
        self.assertIn(b'/repo/project.git/tree/src', root.data)
        self.assertIn(b'Project', root.data)
        self.assertIn(b'data-markdown-dir=""', root.data)
        self.assertNotIn(b'<script>alert(1)</script>', root.data)
        self.assertIn(b'refs/heads/main', root.data)
        self.assertIn(b'refs/tags/v1', root.data)
        self.assertEqual(self.client.get('/repo/project.git/tree/src').status_code, 200)
        self.assertIn(b'data-markdown-dir="src"', self.client.get('/repo/project.git/tree/src').data)
        blob = self.client.get('/repo/project.git/blob/src/hello.py')
        self.assertIn(b'print(&#34;second&#34;)', blob.data)
        history = self.client.get('/repo/project.git/commits')
        self.assertIn(b'Update source', history.data)
        commit_oid = self.git('rev-parse', 'HEAD', cwd=self.work)
        diff = self.client.get('/repo/project.git/commit/' + commit_oid)
        self.assertEqual(diff.status_code, 200)
        self.assertIn(b'+print(&#34;second&#34;)', diff.data)
        tag = self.client.get('/repo/project.git/blob/src/hello.py?ref=refs/tags/v1')
        self.assertIn(b'first', tag.data)
        self.assertNotIn(b'second', tag.data)
        file_link = self.client.get('/repo/project.git/open/src/hello.py?ref=refs/tags/v1')
        self.assertEqual(file_link.status_code, 302)
        self.assertIn('/blob/src/hello.py?ref=refs/tags/v1', file_link.location)
        self.assertIn(b'first', self.client.get(file_link.location).data)
        folder_link = self.client.get('/repo/project.git/open/src?ref=refs/heads/main')
        self.assertEqual(folder_link.status_code, 302)
        self.assertIn('/tree/src?ref=refs/heads/main', folder_link.location)

    def test_invalid_paths_and_refs(self):
        self.assertEqual(self.client.get('/repo/project.git?ref=refs/heads/missing').status_code, 404)
        self.assertEqual(self.client.get('/repo/project.git/tree/src/hello.py').status_code, 404)
        self.assertEqual(self.client.get('/repo/project.git/blob/src').status_code, 404)
        self.assertEqual(self.client.get('/repo/project.git/blob/src/../hello.py').status_code, 404)
        self.assertEqual(self.client.get('/repo/project.git/open/missing.py').status_code, 404)
        self.assertEqual(self.client.get('/repo/../project.git').status_code, 404)
        self.assertEqual(self.client.get('/repo/project.git/commits?page=0').status_code, 400)

    def test_existing_names_can_be_browsed_and_described(self):
        for name in ('中文.git', 'my project.git', 'a' * 65 + '.git'):
            with self.subTest(name=name):
                target = self.root / name
                self.bare.rename(target)
                try:
                    self.assertIn(name, self.client.get('/').get_data(as_text=True))
                    self.assertEqual(self.client.get('/repo/' + quote(name)).status_code, 200)
                    response = self.client.post('/describe/' + quote(name), data={
                        'csrf': portal.CSRF_TOKEN, 'description': 'Updated',
                    })
                    self.assertEqual(response.status_code, 303)
                    self.assertEqual((target / 'description').read_text().strip(), 'Updated')
                    self.assertFalse(portal.valid_name(name[:-4]))
                finally:
                    target.rename(self.bare)

    def test_existing_name_rejects_paths(self):
        for name in ('../outside.git', '..\\outside.git', 'C:outside.git', '/outside.git', 'bad\0.git'):
            with self.subTest(name=name):
                self.assertFalse(portal.valid_existing_name(name))

    def test_archive_hides_from_default_list_and_can_be_restored(self):
        marker = self.bare / portal.ARCHIVE_MARKER
        self.assertIn('project.git', self.client.get('/').get_data(as_text=True))
        self.assertNotIn('project.git', self.client.get('/?archived=1').get_data(as_text=True))
        self.assertEqual(self.client.post('/archive/project.git', data={'archived': '1'}).status_code, 403)
        self.assertFalse(marker.exists())
        self.assertEqual(self.client.post('/archive/project.git', data={
            'csrf': portal.CSRF_TOKEN, 'archived': 'invalid',
        }).status_code, 400)

        current_page = '/repo/project.git/commits?ref=refs/heads/main'
        archived = self.client.post('/archive/project.git', data={
            'csrf': portal.CSRF_TOKEN, 'archived': '1', 'next': current_page,
        })
        self.assertEqual(archived.status_code, 303)
        self.assertEqual(archived.location, current_page)
        self.assertTrue(marker.exists())
        self.assertNotIn('project.git', self.client.get('/').get_data(as_text=True))
        self.assertIn('project.git', self.client.get('/?archived=1').get_data(as_text=True))
        detail = self.client.get('/repo/project.git')
        self.assertEqual(detail.status_code, 200)
        self.assertIn('取消存档', detail.get_data(as_text=True))
        self.assertIn('此仓库已存档，现为只读状态。', detail.get_data(as_text=True))
        self.assertIn('name="next" value="/repo/project.git', detail.get_data(as_text=True))

        restored = self.client.post('/archive/project.git', data={
            'csrf': portal.CSRF_TOKEN, 'archived': '0', 'next': current_page,
        })
        self.assertEqual(restored.status_code, 303)
        self.assertEqual(restored.location, current_page)
        self.assertFalse(marker.exists())
        self.assertIn('project.git', self.client.get('/').get_data(as_text=True))
        self.assertNotIn('project.git', self.client.get('/?archived=1').get_data(as_text=True))
        self.assertNotIn('repository-archive-alert', self.client.get('/repo/project.git').get_data(as_text=True))

    def test_raw_only_for_previewable_files_and_download_all_files(self):
        large = b'\x00\xff' + b'x' * (1024 * 1024)
        (self.work / 'src' / 'large.bin').write_bytes(large)
        (self.work / 'src' / 'small.bin').write_bytes(b'\x00\xff')
        (self.work / 'src' / 'large.txt').write_bytes(b'x' * (1024 * 1024 + 1))
        self.commit('Add binary file')
        self.git('push', str(self.bare), 'main', cwd=self.work)

        for name, expected in [('large.bin', large), ('small.bin', b'\x00\xff'),
                               ('large.txt', b'x' * (1024 * 1024 + 1))]:
            preview = self.client.get('/repo/project.git/blob/src/' + name)
            self.assertIn('无法预览'.encode(), preview.data)
            self.assertNotIn(('/repo/project.git/raw/src/' + name).encode(), preview.data)
            self.assertIn(('/repo/project.git/download/src/' + name).encode(), preview.data)
            self.assertEqual(self.client.get('/repo/project.git/raw/src/' + name).status_code, 404)
            response = self.client.get('/repo/project.git/download/src/' + name)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data, expected)
            self.assertEqual(response.content_length, len(expected))
            self.assertIn('attachment', response.headers['Content-Disposition'])
            self.assertIn(name, response.headers['Content-Disposition'])
            self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')

        self.assertIn(b'/repo/project.git/raw/src/hello.py',
                      self.client.get('/repo/project.git/blob/src/hello.py').data)
        old_raw = self.client.get('/repo/project.git/raw/src/hello.py?ref=refs/tags/v1')
        self.assertEqual(old_raw.data, b'print("first")\n')
        self.assertTrue(old_raw.content_type.startswith('text/plain'))
        self.assertIn('inline', old_raw.headers['Content-Disposition'])
        head = self.client.head('/repo/project.git/download/src/large.bin')
        self.assertEqual(head.content_length, len(large))
        self.assertEqual(head.data, b'')
        self.assertEqual(self.client.get('/repo/project.git/raw/src').status_code, 404)
        self.assertEqual(self.client.get('/repo/project.git/download/missing.bin').status_code, 404)
        self.assertEqual(self.client.get('/repo/project.git/raw/src/large.bin?ref=refs/tags/v1').status_code, 404)

    def test_commit_history_pagination(self):
        for index in range(49):
            (self.work / 'counter.txt').write_text(str(index), encoding='utf-8')
            self.commit('History ' + str(index))
        self.git('push', str(self.bare), 'main', cwd=self.work)
        first = self.client.get('/repo/project.git/commits')
        self.assertEqual(first.status_code, 200)
        self.assertIn(b'page=2', first.data)
        self.assertNotIn(b'Initial commit', first.data)
        second = self.client.get('/repo/project.git/commits?page=2')
        self.assertEqual(second.status_code, 200)
        self.assertIn(b'Initial commit', second.data)
        self.assertIn(b'page=1', second.data)

    def test_empty_repository(self):
        self.git('init', '--bare', '--initial-branch=main', str(self.root / 'empty.git'))
        page = self.client.get('/repo/empty.git')
        self.assertEqual(page.status_code, 200)
        self.assertIn('此仓库还没有提交'.encode(), page.data)
        self.assertEqual(self.client.get('/repo/empty.git/tree/missing').status_code, 404)


class GitTimeoutTests(unittest.TestCase):
    def test_timeout_kills_process_before_waiting_for_reader(self):
        released = threading.Event()
        fallback_fired = threading.Event()
        process = Mock()
        process.poll.return_value = None
        process.kill.side_effect = released.set
        process.stdout.read.side_effect = lambda size: (released.wait(), b'')[1]
        original_result = Future.result

        def short_result(future, timeout=None):
            return original_result(future, timeout=0.02)

        def fallback():
            fallback_fired.set()
            released.set()

        # Bound a regression so a broken implementation fails instead of hanging.
        timer = threading.Timer(1, fallback)
        timer.start()
        try:
            with patch.object(repo_browser.subprocess, 'Popen', return_value=process), \
                    patch.object(Future, 'result', short_result):
                with self.assertRaisesRegex(repo_browser.GitBrowseError, 'Git 读取超时'):
                    repo_browser.git(Path('/unused'), 'log')
            self.assertFalse(fallback_fired.is_set(), 'Reader was not released by process.kill()')
            process.kill.assert_called_once()
            process.wait.assert_called_once()
            process.stdout.close.assert_called_once()
        finally:
            timer.cancel()
            released.set()
            timer.join()


if __name__ == '__main__':
    unittest.main()
