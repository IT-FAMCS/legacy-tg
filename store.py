"""Durable chat state; network work is staged in memory, commits are short."""
import json
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

class Store:
    def __init__(self,path):
        self.path=str(path);Path(path).parent.mkdir(parents=True,exist_ok=True)
        self.local=threading.local()
        with self.transaction() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS runtime(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS processed(id INTEGER PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS drafts(tid INTEGER PRIMARY KEY,generation TEXT NOT NULL,data TEXT NOT NULL,expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS callbacks(token TEXT PRIMARY KEY,tid INTEGER NOT NULL,generation TEXT NOT NULL,data TEXT NOT NULL,expires REAL NOT NULL,done INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS outgoing(id INTEGER PRIMARY KEY,method TEXT NOT NULL,payload TEXT NOT NULL,tid INTEGER,generation TEXT,task_ids TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,next_at REAL NOT NULL);
            ''')

    @contextmanager
    def transaction(self):
        existing=getattr(self.local,'db',None)
        if existing is not None:yield existing;return
        db=sqlite3.connect(self.path,timeout=5);db.row_factory=sqlite3.Row;self.local.db=db
        try:
            db.execute('BEGIN IMMEDIATE');yield db;db.commit()
        except BaseException:db.rollback();raise
        finally:self.local.db=None;db.close()

    @contextmanager
    def update(self):
        """Stage callback/draft/reply changes while HTTP runs; atomically commit with offset."""
        stage={'sql':[],'states':{},'done':set(),'finish':None}
        self.local.stage=stage
        try:
            yield
            with self.transaction() as db:
                for sql,args in stage['sql']:db.execute(sql,args)
                if stage['finish'] is not None:self._finish(db,stage['finish'])
        finally:self.local.stage=None

    def _read(self,sql,args=()):
        existing=getattr(self.local,'db',None)
        if existing is not None:return existing.execute(sql,args).fetchall()
        db=sqlite3.connect(self.path,timeout=5);db.row_factory=sqlite3.Row
        try:return db.execute(sql,args).fetchall()
        finally:db.close()

    def _write(self,sql,args=()):
        stage=getattr(self.local,'stage',None)
        if stage is not None:stage['sql'].append((sql,args))
        else:
            with self.transaction() as db:db.execute(sql,args)

    def processed(self,update_id):return bool(self._read('SELECT 1 FROM processed WHERE id=?',(update_id,)))

    def _finish(self,db,update_id):
        db.execute('INSERT OR IGNORE INTO processed(id) VALUES(?)',(update_id,))
        current=db.execute("SELECT value FROM runtime WHERE key='offset'").fetchone()
        offset=max(update_id+1,int(current['value']) if current else 0)
        db.execute("INSERT INTO runtime(key,value) VALUES('offset',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(str(offset),))
        db.execute('DELETE FROM processed WHERE id < ?',(offset-10000,))
        db.execute('DELETE FROM callbacks WHERE expires<?',(time.time(),))
        db.execute('DELETE FROM drafts WHERE expires<?',(time.time(),))

    def finish(self,update_id):
        stage=getattr(self.local,'stage',None)
        if stage is not None:stage['finish']=update_id
        else:
            with self.transaction() as db:self._finish(db,update_id)

    def offset(self):
        rows=self._read("SELECT value FROM runtime WHERE key='offset'")
        return int(rows[0]['value']) if rows else 0

    def state(self,tid,generation=None):
        stage=getattr(self.local,'stage',None)
        if stage is not None and tid in stage['states']:return stage['states'][tid]
        rows=self._read('SELECT * FROM drafts WHERE tid=?',(tid,))
        if not rows:return None
        row=rows[0]
        if row['expires']<=time.time() or (generation is not None and row['generation']!=generation):
            self._write('DELETE FROM drafts WHERE tid=?',(tid,));return None
        return json.loads(row['data'])

    def set_state(self,tid,generation,data):
        stage=getattr(self.local,'stage',None)
        if stage is not None:stage['states'][tid]=data
        if data is None:self._write('DELETE FROM drafts WHERE tid=?',(tid,))
        else:self._write('INSERT INTO drafts VALUES(?,?,?,?) ON CONFLICT(tid) DO UPDATE SET generation=excluded.generation,data=excluded.data,expires=excluded.expires',
                        (tid,generation,json.dumps(data,ensure_ascii=False),time.time()+1800))

    def callback(self,tid,generation,data):
        token=secrets.token_urlsafe(12)
        self._write('INSERT INTO callbacks(token,tid,generation,data,expires) VALUES(?,?,?,?,?)',
                    (token,tid,generation,json.dumps(data,ensure_ascii=False),time.time()+600))
        return 'c:'+token

    def action(self,token,tid,generation):
        if not isinstance(token,str) or not token.startswith('c:'):return None
        rows=self._read('SELECT * FROM callbacks WHERE token=? AND tid=? AND generation=? AND expires>?',
                        (token[2:],tid,generation,time.time()))
        if not rows:return None
        stage=getattr(self.local,'stage',None)
        return {'data':json.loads(rows[0]['data']),'done':bool(rows[0]['done']) or (stage is not None and token in stage['done'])}

    def done(self,token):
        stage=getattr(self.local,'stage',None)
        if stage is not None:stage['done'].add(token)
        self._write('UPDATE callbacks SET done=1 WHERE token=?',(token[2:],))

    def enqueue(self,method,payload,tid=None,generation=None,task_ids=()):
        self._write('INSERT INTO outgoing(method,payload,tid,generation,task_ids,next_at) VALUES(?,?,?,?,?,?)',
                    (method,json.dumps(payload,ensure_ascii=False),tid,generation,json.dumps(list(task_ids)),time.time()))

    def outgoing(self,due_only=False):
        rows=self._read('SELECT * FROM outgoing'+(' WHERE next_at<=?' if due_only else '')+' ORDER BY id LIMIT 100',
                        (time.time(),) if due_only else ())
        return [{**dict(row),'payload':json.loads(row['payload']),'task_ids':json.loads(row['task_ids'])} for row in rows]

    def delivered(self,row_id):self._write('DELETE FROM outgoing WHERE id=?',(row_id,))

    def retry(self,row_id,delay):
        with self.transaction() as db:
            db.execute('UPDATE outgoing SET attempts=attempts+1,next_at=? WHERE id=?',(time.time()+delay,row_id))
            db.execute('DELETE FROM outgoing WHERE attempts>=8')

    def sending_allowed(self):
        rows=self._read("SELECT value FROM runtime WHERE key='send_after'")
        return not rows or float(rows[0]['value'])<=time.time()

    def pause_sending(self,seconds):
        with self.transaction() as db:
            row=db.execute("SELECT value FROM runtime WHERE key='send_after'").fetchone()
            until=max(float(row['value']) if row else 0,time.time()+seconds)
            db.execute("INSERT INTO runtime(key,value) VALUES('send_after',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(str(until),))

    def close(self):pass
