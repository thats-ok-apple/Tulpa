"""Linux adapters for pinned readers; all candidates retain page-1 HMAC checks.

The upstream WeChat SQL decoder is reused; only discovery and key acquisition
are replaced. Database snapshots, WAL validation and import IDs stay unchanged.
"""
import json
import os
from pathlib import Path
import re
import sys
import time

from .config import ROOT, DATA
from .native_sources import configured_root, supplied_keys
from .snowluma_secure import atomic_json, private_directory


def _upstream():
    path=str(ROOT/'tools/qq-reader')
    if path not in sys.path:sys.path.insert(0,path)
    os.environ['CHATLOG_KEEPER_DATA_DIR']=str(DATA/'qq-reader')


def install_qq(namespace):
    original=namespace['find_qq_data_root']
    def root():
        configured=configured_root('qq')
        if configured:return configured
        legacy=DATA/'qq-source-root.json'
        if legacy.is_file():
            saved=json.loads(legacy.read_text())
            path=Path(saved.get('path',''))
            if saved.get('format')!='tulpa-qq-source-v1' or not path.is_absolute() or not path.is_dir():
                raise ValueError('保存的 QQ 来源目录不可用；未自动换号。')
            return path
        return original()
    namespace['find_qq_data_root']=root
    reader=namespace['QQDBReader'];initialize=reader.initialize
    def init(self):
        keys=supplied_keys('qq')
        account=self._configured_account_id
        if keys and account:
            root=self._configured_data_root or namespace['find_qq_data_root']()
            path=namespace['find_qq_account_databases'](root).get(account) if root else None
            value=keys.get(account,keys.get('passphrase',keys.get('key')))
            if isinstance(value,dict):value=value.get('passphrase',value.get('key'))
            key=namespace['_parse_cached_key_text'](value) if isinstance(value,str) else None
            raw=namespace['_read_qq_verification_bytes'](path) if path and key else None
            if raw and namespace['_verify_key_qq'](key,raw):namespace['save_cached_key_for_account'](key,account)
            elif value:raise ValueError('所选 QQ 密钥未通过当前账号数据库验证。')
        return initialize(self)
    reader.initialize=init


def install_wechat(namespace):
    _upstream()
    from chatlog_keeper import wechat_db as wx
    from chatlog_keeper.core._linux import process_pids
    cls=namespace['WeChatDB']
    namespace['auto_detect_db_dir']=lambda:str(root) if (root:=configured_root('wechat') or wx.find_weixin_data_root()) else None
    cls._find_weixin_pids=lambda self:process_pids(('wechat','weixin'))
    cls._try_other_accounts=lambda self:False
    cls._auto_diagnose_key_failure=lambda self,rel:None
    def load(self,master_key=None):
        self.master_key=None;self.cfg_dword=None;self._keys={}
        supplied=supplied_keys('wechat')
        if isinstance(supplied.get(self.account),dict):supplied=supplied[self.account]
        candidates=[]
        for text in (master_key,supplied.get('master_key'),supplied.get('key')):
            if isinstance(text,str):
                try:candidates.append(bytes.fromhex(text))
                except ValueError:raise ValueError('微信密钥格式无效。') from None
        cached=wx.load_cached_wechat_key_for_account(self.account)
        if cached:candidates.append(cached)
        for path in (self._stable_key_file(),self.keys_file):
            if path and Path(path).is_file():
                for rel,key in self._load_key_cache(path).items():self._keys.setdefault(rel,key)
        # Scan only within the normal OS permission boundary, with one total
        # budget for all databases. Active startup capture is a separate action.
        pids=self._find_weixin_pids();deadline=time.monotonic()+20
        for rel,path,_ in self._db_files:
            with open(path,'rb') as stream:page=stream.read(4096)
            keys=list(candidates)
            if rel in self._keys:keys.insert(0,self._keys[rel])
            value=supplied.get(rel.replace(os.sep,'/'))
            if isinstance(value,dict):value=value.get('enc_key',value.get('key'))
            if isinstance(value,str):
                try:keys.insert(0,bytes.fromhex(value))
                except ValueError:raise ValueError('微信密钥格式无效。') from None
            selected=None
            for key in keys:
                if len(key)==48 and namespace['_verify_enc_key'](key,page):selected=key;break
                if len(key)==32 and (effective:=wx._effective_page_key(key,page)):
                    selected=effective;break
            if not selected:
                for pid in pids:
                    remaining=deadline-time.monotonic()
                    if remaining<=0:break
                    key=wx.extract_key_from_weixin(pid,Path(path),timeout_s=min(5,remaining))
                    if key and (effective:=wx._effective_page_key(key,page)):
                        selected=effective;candidates.append(key);break
            self._keys.pop(rel,None)
            if selected:self._keys[rel]=selected
        if self._keys:
            private_directory(Path(self.keys_file).parent)
            atomic_json(Path(self.keys_file),{k:v.hex() for k,v in self._keys.items()})
            if stable:=self._stable_key_file():
                private_directory(Path(stable).parent)
                atomic_json(Path(stable),{k:v.hex() for k,v in self._keys.items()})
    cls._load_or_extract_keys=load


def install_media(namespace):
    _upstream()
    cls=namespace['MediaDownloader']
    def scan(self,monitor=False,monitor_timeout=120):
        if not self._probe_ct():return None
        supplied=supplied_keys('wechat')
        if isinstance(supplied.get(self.db.account),dict):supplied=supplied[self.db.account]
        provided=supplied.get('image_key')
        if isinstance(provided,str) and len(provided)==16 and self._validate_key(provided):return provided
        from chatlog_keeper.core._linux_process_memory import iter_process_memory_chunks
        from chatlog_keeper.core._windows_process_memory import ProcessMemoryAccessDenied
        from .platform_process import linux_processes
        deadline=time.monotonic()+min(monitor_timeout,20)
        # Renderer subprocesses can hold the image key; executable names are
        # matched, while unrelated applications' memory is never read.
        pids=[p['pid'] for p in linux_processes() if Path(p['executable']).name.lower() in ('wechat','weixin','wechatappex')]
        seen=set()
        for pid in pids:
            if time.monotonic()>=deadline:break
            try:
                for chunk in iter_process_memory_chunks(pid,timeout_s=max(.1,deadline-time.monotonic())):
                    candidates=[m.group().decode('ascii') for m in re.finditer(rb'(?<![a-zA-Z0-9])[a-zA-Z0-9]{16,32}(?![a-zA-Z0-9])',chunk)]
                    candidates += [m.group().decode('utf-16le') for m in re.finditer(rb'(?:[a-zA-Z0-9]\x00){16,32}',chunk)]
                    for candidate in candidates:
                        key=candidate[:16]
                        if key not in seen and self._validate_key(key):return key
                        seen.add(key)
                    if time.monotonic()>=deadline:break
            except (OSError,ProcessMemoryAccessDenied):continue
        return None
    cls._scan_aes_key=scan
    original=cls._persist_key
    def persist(self,key):
        original(self,key)
        path=Path(self._key_store())
        if path.exists():path.chmod(0o600)
    cls._persist_key=persist
