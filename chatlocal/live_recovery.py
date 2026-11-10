"""Persisted, account-bound recovery metadata; never reset unrelated cursors."""
import copy
import time


class IdentityConflict(ValueError):
    def __init__(self, conversation_id):
        super().__init__('native_identity_conflict')
        self.conversation_id = conversation_id


def diagnosis(db, table, prior):
    row = db.execute(f'SELECT server_id,create_time,local_type FROM "{table}" WHERE local_id=?',
                     (prior['last'],)).fetchone()
    maximum = db.execute(f'SELECT coalesce(max(local_id),0) FROM "{table}"').fetchone()[0]
    if maximum < prior['last']:
        return 'sequence_rewind'
    if row is None:
        return 'boundary_deleted'
    return 'boundary_identity_changed'


def replay_issue(previous, reason, now=None):
    now = time.time() if now is None else now
    attempts = previous.get('attempts', 0) + 1
    return dict(reason=reason, status='replaying' if attempts <= 3 else 'blocked',
                attempts=attempts, first_seen=previous.get('first_seen', now), updated_at=now,
                next_retry_at=now if attempts <= 3 else None,
                action='overlap_replay' if attempts <= 3 else 'inspect_source_and_replay')


def quarantine(checkpoint, conversation_id, now=None):
    import hashlib
    result = copy.deepcopy(checkpoint)
    suffix = '/Msg_' + hashlib.md5(conversation_id.encode()).hexdigest()
    for key in result.get('cursors', {}):
        if key.endswith(suffix):
            result.setdefault('recovery', {})[key] = dict(reason='native_identity_conflict', status='blocked',
                attempts=1, updated_at=now or time.time(), next_retry_at=None, action='inspect_identity_conflict')
    return result


def public(checkpoint):
    import hashlib
    issues = [dict(v,issue_id=hashlib.sha256(k.encode()).hexdigest()[:16]) for k,v in checkpoint.get('recovery',{}).items()]
    active = [i for i in issues if i.get('status') != 'recovered']
    return dict(affected_conversations=len(active), error_types=sorted({i.get('reason','unknown') for i in active}),
                issues=active[:50],issues_truncated=len(active)>50,
                pending_ack_messages=sum(len(c.get('pending_ids',[])) for c in checkpoint.get('cursors',{}).values() if isinstance(c,dict)),
                recovery_states={s:sum(i.get('status') == s for i in active) for s in ('replaying','blocked')},
                next_retry_at=min((i['next_retry_at'] for i in active if i.get('next_retry_at')), default=None))
