"""Image identity lifecycle/backfill tests; all data and files are synthetic."""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from PIL import Image
from chatlocal.store import Store
from chatlocal.image_identity import backfill_batch,candidates
from chatlocal.data_delete import preview,purge
from chatlocal.media_refresh import refresh
from chatlocal.mcp_chat_local_media import history_image,read_path
import threading

SUBPROCESS_ENV={k:os.environ[k] for k in ('SYSTEMROOT','WINDIR') if k in os.environ}


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='image-index-')
        self.root=Path(self.tmp.name);(self.root/'data').mkdir();(self.root/'imports').mkdir()
        self.patches=[patch('chatlocal.media.ROOT',self.root),patch('chatlocal.media.DATA',self.root/'data'),
                      patch('chatlocal.media.local_path',side_effect=lambda p:(self.root/p).resolve())]
        for p in self.patches:p.start()
        self.store=Store(self.root/'data/chats.sqlite3');self.n=0
        out=io.BytesIO();Image.new('RGB',(8,8),'red').save(out,'PNG');self.raw=out.getvalue()
        self.md5=hashlib.md5(self.raw).hexdigest()
        self.image=self.root/'imports/source.png';self.image.write_bytes(self.raw)

    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()

    def row(self,mid='1',cid='111:group:222',platform='qq',status='available'):
        return dict(source_id=mid,platform=platform,conversation_id=cid,conversation=cid,sender_id='333',sender='fixture',
            timestamp='2026-10-01 12:00',content='fixture',media=[dict(kind='image',status=status,
                local_path=str(self.image),metadata=dict(md5=self.md5,thumbnail=False))])

    def load(self,rows):
        self.n+=1;path=self.root/f'import{self.n}.json';path.write_text(json.dumps(rows),'utf-8')
        return self.store.import_file(path)

    def identities(self):
        with self.store.connect() as db:return [dict(r) for r in db.execute('SELECT * FROM message_image_ids ORDER BY message_id,slot')]

    def test_import_update_scope_and_delete(self):
        self.load([self.row(),self.row('2','111:group:223'),self.row('3','999:group:222'),self.row('4',platform='wechat')])
        self.assertEqual(len(self.identities()),3)
        with self.store.connect() as db:
            self.assertEqual(len(candidates(db,'111:group:222',self.md5)),1)
            db.execute("UPDATE messages SET media_json='[]' WHERE id=1")
        self.assertEqual(len(self.identities()),2)
        # Re-import enriches the same source identity, not a new message.
        self.load([self.row()]);self.assertEqual(len(self.identities()),3)
        with self.store.connect() as db:db.execute("UPDATE messages SET conversation_id='111:direct:333' WHERE id=1")
        with self.store.connect() as db:
            self.assertFalse(candidates(db,'111:group:222',self.md5))
            self.assertEqual(len(candidates(db,'111:direct:333',self.md5)),1)
            db.execute("UPDATE messages SET platform='wechat' WHERE id=1")
        self.assertEqual(len(self.identities()),2)
        with self.store.connect() as db:db.execute("UPDATE messages SET platform='qq' WHERE id=1")
        self.assertEqual(len(self.identities()),3)
        scope=dict(platform='qq',conversations=['111:group:223'],start='2026-10-01',end='2026-10-01')
        purge(self.store,scope,preview(self.store,scope)['fingerprint'])
        self.assertEqual(len(self.identities()),2)
        with self.store.connect() as db:
            db.execute('DELETE FROM provenance WHERE message_id=1')
            db.execute('DELETE FROM messages WHERE id=1')
        self.assertEqual(len(self.identities()),1)
        with self.store.connect() as db:self.assertFalse(db.execute('PRAGMA foreign_key_check').fetchall())

    def test_media_refresh_registers_recovered_image(self):
        row=self.row(status='unavailable');row['media'][0].pop('local_path')
        self.load([row]);self.assertFalse(self.identities())
        def runner(request,output):
            payload=json.loads(request.read_text('utf-8'));recovered=self.row()
            output.write_text(json.dumps(dict(messages=[recovered],stats=dict(recovered=1,unavailable=0,
                skipped_sticker_messages=0,metadata_missing_messages=0))),'utf-8')
        result=refresh(self.store,dict(platform='qq',conversations=['111:group:222'],start='2026-10-01',end='2026-10-01',load_stickers=True),runner=runner)
        self.assertEqual(result['recovered'],1);self.assertEqual(len(self.identities()),1)

    def test_indexed_original_thumbnail_and_file_validation(self):
        self.load([self.row()])
        out=io.BytesIO();Image.new('RGB',(4,4),'blue').save(out,'PNG');thumb=out.getvalue()
        small=self.root/'imports/thumb.png';small.write_bytes(thumb)
        row=self.row('2');row['media'][0].update(local_path=str(small),metadata=dict(md5=self.md5,thumbnail=True))
        self.load([row]);ref=dict(file=self.md5,timestamp=1)
        def read(prefer=False):
            source={};data=history_image(self.store,'111:group:222',ref,threading.Event(),source,prefer_original=prefer)
            return data,source
        self.assertEqual(read()[0],self.raw);self.assertEqual(read()[1]['variant'],'ori')
        with self.store.connect() as db:
            items=[json.loads(r[0])[0] for r in db.execute('SELECT media_json FROM messages ORDER BY id')]
        original=self.root/items[0]['local_path'];cached_thumb=self.root/items[1]['local_path']
        original.unlink()
        self.assertEqual(read()[0],thumb);self.assertEqual(read()[1]['variant'],'thumb')
        self.assertIsNone(read(True)[0])
        cached_thumb.write_bytes(b'corrupt');self.assertIsNone(read()[0])
        original.write_bytes(self.raw);self.assertEqual(read()[0],self.raw)
        self.assertEqual(len(self.identities()),2)

    def test_invalid_index_candidates_fall_back_to_nearby_history(self):
        self.load([self.row(),self.row('2')])
        with self.store.connect() as db:
            record=db.execute('SELECT timestamp,media_json FROM messages WHERE id=1').fetchone()
            original=json.loads(record['media_json'])[0]
            legacy=dict(original,metadata={})
            db.execute('UPDATE messages SET media_json=? WHERE id=1',(json.dumps([legacy]),))
        stamp=record['timestamp'];ref=dict(file=self.md5,timestamp=stamp)
        invalid=self.root/'data/media/00/invalid.png';invalid.parent.mkdir(exist_ok=True)
        for state in ('missing','corrupt','thumbnail'):
            with self.subTest(state=state):
                if state=='corrupt':invalid.write_bytes(b'corrupt')
                indexed=dict(original,local_path=invalid.relative_to(self.root).as_posix())
                if state=='thumbnail':indexed=dict(original,metadata=dict(md5=self.md5,thumbnail=True))
                with self.store.connect() as db:
                    db.execute('UPDATE messages SET media_json=?,timestamp=? WHERE id=2',
                               (json.dumps([indexed]),stamp-60000))
                source={}
                self.assertEqual(history_image(self.store,'111:group:222',ref,threading.Event(),source,
                                               prefer_original=True),self.raw)
                self.assertEqual(source['variant'],'ori')
                self.assertIsNone(history_image(self.store,'111:group:222',ref,threading.Event(),{},
                                                plan=SimpleNamespace(start=stamp+1,end=None)))
                self.assertIsNone(history_image(self.store,'999:group:222',ref,threading.Event(),{}))

    def test_index_and_nearby_history_share_file_read_limit(self):
        self.load([self.row(),self.row('2')])
        with self.store.connect() as db:
            record=db.execute('SELECT timestamp,media_json FROM messages WHERE id=1').fetchone()
            original=json.loads(record['media_json'])[0]
            db.execute('UPDATE messages SET media_json=? WHERE id=1',
                       (json.dumps([dict(original,metadata={})]),))
            invalid=[]
            for i in range(8):
                path=self.root/f'data/media/00/corrupt{i}.png';path.parent.mkdir(exist_ok=True)
                path.write_bytes(b'corrupt')
                invalid.append(dict(original,local_path=path.relative_to(self.root).as_posix()))
            db.execute('UPDATE messages SET media_json=?,timestamp=? WHERE id=2',
                       (json.dumps(invalid),record['timestamp']-60000))
        with patch('chatlocal.mcp_chat_local_media.read_path',wraps=read_path) as reader:
            self.assertIsNone(history_image(self.store,'111:group:222',
                dict(file=self.md5,timestamp=record['timestamp']),threading.Event(),{}))
            self.assertEqual(reader.call_count,8)

    def test_explicit_batches_repeat_without_startup_scan(self):
        self.load([self.row(str(i)) for i in range(1,6)])
        with self.store.connect() as db:db.execute('DELETE FROM message_image_ids')
        # Reopening creates no history rows. Only the explicit command backfills.
        self.store=Store(self.store.path);self.assertFalse(self.identities())
        prefix=[sys.executable,'-X','utf8',str(ROOT/'scripts/index_history_images.py'),
            '--database',str(self.store.path),'--batch-size','2']
        run=lambda args:json.loads(subprocess.check_output(prefix+args,cwd=ROOT,env=dict(os.environ,**SUBPROCESS_ENV),text=True,encoding='utf-8'))
        first=run([]);self.assertEqual(first['processed'],2);self.assertTrue(first['has_more'])
        second=run(['--after-id',str(first['after_id']),'--through-id',str(first['through_id']),'--max-batches','5'])
        self.assertFalse(second['has_more']);before=self.identities();self.assertEqual(len(before),5)
        run(['--max-batches','5']);self.assertEqual(self.identities(),before)
        with self.store.connect() as db:db.execute('DELETE FROM message_image_ids')
        self.load([self.row()])
        self.assertEqual(len(self.identities()),1,'unchanged re-import must index its explicit batch')
        # The backfill cannot resurrect a deleted row or stale media entry.
        with self.store.connect() as db:
            db.execute('DELETE FROM provenance WHERE message_id=2')
            db.execute('DELETE FROM messages WHERE id=2')
            db.execute("UPDATE messages SET media_json='[]' WHERE id=3")
        backfill_batch(self.store,0,5,100)
        self.assertEqual({r['message_id'] for r in self.identities()},{1,4,5})


if __name__=='__main__':unittest.main(verbosity=2)
