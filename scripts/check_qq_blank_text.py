"""Regression for whitespace-only native QQ elements; no client or image IO."""
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'scripts'),str(ROOT/'tools/qq-reader')]
from chatlog_keeper import qq_db as q
from reader_media import QQMedia
from reader_patches import patch_qq_blank_text


def varint(n):
    result=bytearray()
    while n>127:
        result.append((n&127)|128);n>>=7
    result.append(n)
    return bytes(result)


def field(number,data):
    return varint(number*8+2)+varint(len(data))+data


def main():
    installed=Path(q.__file__).read_bytes()
    replacement='''    # Native text elements can contain only whitespace (for example, an
    # @mention envelope). Preserve the row without promoting metadata to text.
    return " ".join(out) if out else "[空白文本]" if any(parts) else ""
'''.encode('utf-8')
    assert installed.count(replacement)==1
    original=installed.replace(replacement,b'    return " ".join(out)\n')
    assert patch_qq_blank_text(original)==installed
    try:patch_qq_blank_text(installed)
    except ValueError:pass
    else:raise AssertionError('Unexpected decoder accepted')
    parser=QQMedia.__new__(QQMedia);parser.q=q
    for text in (' ','\t','\n',' \t\n'):
        blob=field(40800,field(45101,text.encode()))
        assert q._extract_msg_text(blob)=='[空白文本]'
        content,media,reply=parser.parse(blob,17)
        assert (content,media,reply)==('[空白文本]',[],None)
        row=(10,1780000000,'fixture',100,blob,'200',1,7,'group','u_fixture')
        rec=q._qq_message_page_record(row,account_id='fixture',conversation_id='200',
            buddy_map={},group_map={},group_name_map={},recover_unreadable_rows=True)
        assert rec['content']=='[空白文本]' and rec['sender_qq']==100 and rec['msg_id']==10
        assert rec['source_offset'].endswith(':7') and 'decode_status' not in rec
        bad=q._qq_message_page_record((*row[:3],None,*row[4:]),account_id='fixture',
            conversation_id='200',buddy_map={},group_map={},group_name_map={},recover_unreadable_rows=True)
        assert bad['decode_error_code']=='qq_sender_identity_unavailable'
    ordinary=field(40800,field(45101,b'actual text'))
    assert q._extract_msg_text(ordinary)=='actual text'
    assert parser.parse(ordinary,17)==('actual text',[],None)
    metadata=field(40800,field(45406,b'\xff'*16))
    assert q._extract_msg_text(metadata)==''
    assert parser.parse(metadata,17)==('',[],None)
    empty=field(40800,field(45101,b''))
    assert q._extract_msg_text(empty)=='' and parser.parse(empty,17)==('',[],None)
    print('PASS: blank native QQ text preserved, metadata excluded, sender validation retained; no client/image IO')


if __name__=='__main__':main()
