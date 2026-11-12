"""Durable state for the single-process beta server; no model runtime required."""
import json
import sqlite3
import time
from pathlib import Path


class JobStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, data TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS requests (owner TEXT, key TEXT, digest TEXT, response TEXT, '
                       'PRIMARY KEY(owner, key))')

    def connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def save(self, job_id, job, request=None):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO jobs VALUES (?, ?)', (job_id, json.dumps(job)))
            if request:
                owner, key, digest = request
                db.execute('INSERT INTO requests VALUES (?, ?, ?, ?)',
                           (owner, key, digest, json.dumps({'id': job_id})))

    def response(self, owner, key):
        with self.connect() as db:
            row = db.execute('SELECT digest, response FROM requests WHERE owner=? AND key=?',
                             (owner, key)).fetchone()
        return (row[0], json.loads(row[1])) if row else None

    def load(self):
        with self.connect() as db:
            jobs = {key: json.loads(data) for key, data in db.execute('SELECT id, data FROM jobs')}
        for key, job in jobs.items():
            if job.get('status') in ('queued', 'writing', 'rendering'):
                job.update(status='error', detail='The server restarted. Your saved draft is safe; retry the last step.',
                           updated_at=time.time())
                self.save(key, job)
        return jobs
