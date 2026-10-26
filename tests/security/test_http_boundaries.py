"""In-process API security regressions; fictional temporary data, no sockets."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, redirect_stdout, redirect_stderr
from http.cookies import SimpleCookie
from io import StringIO
import json
import logging
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from fastapi.testclient import TestClient
from backend.app import create_app
from backend.core import uid
from backend.credentials import hash_password, new_secret
from backend.database import connect, immediate_transaction
from backend.testing import seed_approval_start_scenario
from config.settings import Settings


class SecurityRegressions(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(prefix='benchflow-security-')
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.settings = Settings(data_root=root / 'data', local_app_data_root=root)

    def client(self, demo=False):
        s = self.settings
        if demo:
            s = Settings(data_root=s.data_root, local_app_data_root=s.local_app_data_root,
                         demo_mode=True)
        return TestClient(create_app(s), base_url=s.origin, client=('127.0.0.1', 43992))

    def checked(self, response, status):
        self.assertEqual(response.status_code, status)
        self.assertEqual(response.headers.get('cache-control'), 'no-store')
        for raw in response.headers.get_list('set-cookie'):
            parsed = SimpleCookie(raw)
            self.assertEqual(set(parsed), {'bf_session'})
            c = parsed['bf_session']
            self.assertTrue(bool(c['httponly']))
            self.assertEqual(c['samesite'].lower(), 'lax')
            self.assertEqual(c['path'], '/')
            self.assertFalse(bool(c['domain']))
            self.assertFalse(bool(c['secure']))
        return response

    def headers(self, csrf, **extra):
        return {'Origin': self.settings.origin, 'X-CSRF-Token': csrf,
                'Content-Type': 'application/json', 'X-Request-ID': uid(), **extra}

    def session(self, client):
        r = self.checked(client.get('/api/v1/session'), 200)
        return r.json()['csrf_token']

    def login(self, client, login_id, password):
        r = self.checked(client.post('/api/v1/auth/login', json={
            'login_id': login_id, 'password': password},
            headers=self.headers(self.session(client))), 200)
        self.assertTrue('bf_session=' in r.headers.get('set-cookie', ''))
        return r.json()['csrf_token']

    def test_cookie_cache_error_and_backup_matrix(self):
        seed_approval_start_scenario(self.settings)
        password = new_secret()
        with closing(connect(self.settings)) as db, immediate_transaction(db):
            db.execute('UPDATE accounts SET password_hash=? WHERE login_id=?',
                       (hash_password(password), 'fixture.manager'))
        with self.client() as c:
            r = self.checked(c.get('/api/v1/session'), 200)
            self.assertTrue('bf_session=' in r.headers.get('set-cookie', ''))
            csrf = r.json()['csrf_token']
            old = c.cookies.get('bf_session')
            self.checked(c.get('/api/v1/health', headers={'Host': 'invalid.test'}), 403)
            self.checked(c.post('/api/v1/auth/login', json={'login_id': 'fixture.manager',
                'password': password}, headers=self.headers('invalid')), 403)
            self.checked(c.post('/api/v1/auth/login', json={'login_id': 'fixture.manager',
                'password': new_secret()}, headers=self.headers(csrf)), 401)
            self.checked(c.post('/api/v1/auth/login', json={}, headers=self.headers(csrf)), 422)
            self.checked(c.get('/api/v1/me'), 401)
            csrf = self.login(c, 'fixture.manager', password)
            self.assertTrue(old != c.cookies.get('bf_session'))
            self.checked(c.get('/api/v1/me'), 200)
            bid = uid()
            self.checked(c.post('/api/v1/staff/backups', json={'command_id': bid},
                                headers=self.headers(csrf)), 200)
            path = '/api/v1/staff/backups/' + bid + '/download'
            r = self.checked(c.get(path), 200)
            self.assertEqual(r.headers.get('content-type'), 'application/octet-stream')
            self.assertTrue(r.content.startswith(b'SQLite format 3\x00'))
            r = self.checked(c.post('/api/v1/auth/logout', json={},
                                   headers=self.headers(csrf)), 204)
            self.assertTrue('Max-Age=0' in r.headers.get('set-cookie', ''))
            self.checked(c.get(path), 401)

    def test_demo_rejection_no_consumption_and_atomic_claim(self):
        with self.client(demo=True) as c:
            self.checked(c.get('/api/v1/demo/credentials'), 401)
            csrf = self.session(c)
            self.checked(c.post('/api/v1/demo/credentials', json={},
                headers=self.headers(csrf, Origin='http://invalid.test')), 403)
            self.checked(c.post('/api/v1/demo/credentials', json={},
                headers=self.headers('invalid')), 403)

            def claim(_):
                return c.post('/api/v1/demo/credentials', json={}, headers=self.headers(csrf))

            with ThreadPoolExecutor(max_workers=2) as pool:
                responses = list(pool.map(claim, range(2)))
            self.assertEqual(sorted(r.status_code for r in responses), [200, 404])
            for r in responses:
                self.checked(r, r.status_code)
                if r.status_code != 200:
                    self.assertFalse('accounts' in r.json())
            self.checked(claim(None), 404)

    def test_inert_text_projection_and_marker_not_in_audit_or_logs(self):
        s = seed_approval_start_scenario(self.settings)
        marker = 'synthetic-marker-' + uid()
        text = marker + " '); DROP TABLE jobs; -- <img src=x onerror=alert(1)>"
        output = StringIO()
        handler = logging.StreamHandler(output)
        logger = logging.getLogger()
        logger.addHandler(handler)
        self.addCleanup(logger.removeHandler, handler)
        with redirect_stdout(output), redirect_stderr(output), self.client() as c:
            csrf = self.login(c, s.technician_login_id, s.technician_password)
            detail = self.checked(c.get('/api/v1/staff/jobs/' + s.job_id), 200).json()
            data = {'task_id': None, 'kind': 'finding', 'summary': text,
                    'customer_text': text, 'customer_visible': True, 'uncertainty': None}
            payload = {'command_id': uid(), 'action': 'diagnostic.record',
                'target': {'type': 'job', 'id': s.job_id},
                'expected': {'job_rev': detail['job_rev']}, 'data': data}
            self.checked(c.post('/api/v1/commands', json=payload,
                                headers=self.headers(csrf)), 200)
            payload['command_id'] = uid()
            payload['data'] = dict(data, reason=marker)
            r = self.checked(c.post('/api/v1/commands', json=payload,
                                   headers=self.headers(csrf)), 422)
            self.assertFalse(marker in r.text)
            self.checked(c.post('/api/v1/auth/logout', json={}, headers=self.headers(csrf)), 204)
            self.login(c, s.customer_login_id, s.customer_password)
            r = self.checked(c.get('/api/v1/me/jobs/' + s.job_id), 200)
            projected = r.json()
            self.assertTrue(any(f['text'] == text for f in projected['findings']))
            self.assertTrue(not {'internal', 'timeline', 'customer', 'work'} & set(projected))
            self.assertTrue('application/json' in r.headers.get('content-type', ''))
            with closing(connect(self.settings)) as db:
                self.assertEqual(db.execute('SELECT count(*) FROM jobs').fetchone()[0], 1)
                stored = db.execute('SELECT summary,customer_text FROM diagnostic_records '
                                    'WHERE summary=?', (text,)).fetchone()
                self.assertTrue(stored is not None and tuple(stored) == (text, text))
                audit = json.dumps([tuple(row) for row in db.execute('SELECT * FROM audit_events')])
                self.assertFalse(marker in audit)
        self.assertFalse(marker in output.getvalue())


if __name__ == '__main__':
    unittest.main(verbosity=2)
