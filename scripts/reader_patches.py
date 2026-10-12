"""Small reproducible adaptations to the pinned, read-only upstream modules."""

def patch_qq_proxy(data):
    before=b'    return [sys.executable, "-I", "-u", str(helper), *arguments]'
    after=b'''    # Desktop has a separately pinned stdlib-only Python/SQLite process.
    # Do not change the SQLite DLL or pending byte of the application itself.
    dedicated = Path(sys.executable).parent / "qq-sqlite" / "python.exe"
    executable = str(dedicated) if dedicated.is_file() else sys.executable
    return [executable, "-I", "-u", str(helper), *arguments]'''
    if data.count(before)!=1:raise ValueError('QQ helper command changed; review desktop runtime adaptation')
    data=data.replace(before,after)
    before=b'            raise QQShiftedSQLiteError("isolated QQ SQLite helper refused the database")'
    after=b'''            reason = response.get("error", "unknown")
            # Only a small protocol error code, never a path/database value.
            if not isinstance(reason, str) or not reason.replace("_", "").isalnum() or len(reason)>80:
                reason = "unknown"
            raise QQShiftedSQLiteError("isolated QQ SQLite helper refused the database (" + reason + ")")'''
    if data.count(before)!=1:raise ValueError('QQ helper refusal changed; review diagnostic adaptation')
    return patch_qq_query_diagnostic(data.replace(before,after))


def patch_qq_query_diagnostic(data):
    before=b'                raise sqlite3.DatabaseError("isolated QQ SQLite query failed")'
    after=b'''                reason = response.get("error", "unknown")
                if not isinstance(reason, str) or not reason.replace("_", "").isalnum() or len(reason)>80:
                    reason = "unknown"
                raise sqlite3.DatabaseError("isolated QQ SQLite query failed (" + reason + ")")'''
    if data.count(before)!=1:raise ValueError('QQ query error changed; review sanitized diagnostic')
    return data.replace(before,after)


def patch_wechat_db(data):
    before=b'''            if len(keys) >= len(self._db_files):
                break
        return keys

    def _collect_key_candidates'''
    after=b'''            if len(keys) >= len(self._db_files):
                break
        if len(keys) < len(self._db_files):
            from chatlocal.wechat_keys import recover_legacy_keys
            keys.update(recover_legacy_keys(self, keys))
        return keys

    def _collect_key_candidates'''
    # The downloaded upstream bytes have LF line endings.
    if data.count(before)!=1:raise ValueError('WeChat extraction changed; review legacy-key fallback')
    data=data.replace(before,after)
    before=b'''_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.OpenProcess.restype = wintypes.HANDLE
_k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_k32.VirtualQueryEx.argtypes = [
    wintypes.HANDLE, ctypes.c_void_p, ctypes.POINTER(_MBI), ctypes.c_size_t,
]
_k32.VirtualQueryEx.restype = ctypes.c_size_t
_k32.ReadProcessMemory.argtypes = [
    wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
]'''
    if data.count(before)!=1:raise ValueError('WeChat Windows initialization changed; review Mac isolation')
    after=b'if sys.platform == "win32":\n'+b'\n'.join(b'    '+line for line in before.splitlines())+b'\nelse:\n    _k32 = None'
    data=data.replace(before,after)
    notice=b'# Modified by Tulpa contributors: bounded Windows recovery and Mac cache-only reads; see scripts/reader_patches.py.\n'
    adapter=b'''\n\n# Keep parser/crypto portable; Mac process-key recovery is an explicit separate action.
if sys.platform == "darwin":
    from pathlib import Path as _TulpaPath
    _tulpa_root = str(_TulpaPath(__file__).resolve().parents[3])
    if _tulpa_root not in sys.path:
        sys.path.insert(0, _tulpa_root)
    from scripts.wechat_macos import install as _tulpa_install_macos
    _tulpa_install_macos(globals())
'''
    return notice+data+adapter


def patch_wechat_media(data):
    before=b'''        probe = self._probe_ct()
        if not probe:
            return None
        pids = self.db._find_weixin_pids()'''
    after=b'''        # Mac media consumes only an authenticated account cache. Process
        # recovery is a separate explicit operation, never a media fallback.
        if sys.platform == "darwin":
            return None
        probe = self._probe_ct()
        if not probe:
            return None
        pids = self.db._find_weixin_pids()'''
    if data.count(before)!=1:raise ValueError('WeChat media scanning changed; review Mac isolation')
    if data.count(b'import sys\n')!=0:raise ValueError('WeChat media imports changed; review Mac isolation')
    return b'# Modified by Tulpa contributors: Mac media never falls back to Windows process scanning.\n'+data.replace(b'import struct\n',b'import struct\nimport sys\n',1).replace(before,after)


def patch_qq_helper(data):
    # One pure window function used by our historical export; do not permit
    # WITH/RECURSIVE, arbitrary functions/tables, writes or extension loading.
    before=b'_ALLOWED_SQL_FUNCTIONS = frozenset({"count", "max", "nullif", "trim"})'
    after=b'_ALLOWED_SQL_FUNCTIONS = frozenset({"count", "max", "nullif", "trim", "row_number"})'
    if data.count(before)!=1:raise ValueError('QQ function allowlist changed; review history-window adaptation')
    return patch_qq_directory_tables(data.replace(before,after))


def patch_qq_directory_tables(data):
    # Fixed, known schema generations only; no arbitrary table/SQL access.
    before=b'        "profile_info_v6",'
    after=b'        "profile_info",\n'+b''.join(f'        "profile_info_v{i}",\n'.encode() for i in range(1,6))+before
    if data.count(before)!=1:raise ValueError('QQ directory allowlist changed')
    if b'        "profile_info_v5",' in data:return data
    return data.replace(before,after)
