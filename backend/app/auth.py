"""Credential access for a shared commute workspace."""
import hashlib
import os
import secrets
import time
from sqlalchemy import delete, select
from .core import Session
from .models import WorkspaceSession

COOKIE = 'commute_session'
MAX_AGE = 8 * 60 * 60

def configured():
    return bool(os.getenv('APP_USERNAME') and len(os.getenv('APP_PASSWORD', '')) >= 12)

def valid_credentials(username, password):
    # Hash both values before comparison so length does not affect comparison timing.
    digest = lambda value: hashlib.sha256(value.encode()).digest()
    return (secrets.compare_digest(digest(username), digest(os.getenv('APP_USERNAME', '')))
            & secrets.compare_digest(digest(password), digest(os.getenv('APP_PASSWORD', ''))))

def session_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()

def authenticated(token):
    if not token or not configured():
        return False
    with Session() as db:
        session = db.scalar(select(WorkspaceSession).where(WorkspaceSession.token_hash == session_hash(token)))
        return bool(session and session.expires_at > time.time())

def create_session():
    token = secrets.token_urlsafe(32)
    with Session() as db:
        db.execute(delete(WorkspaceSession).where(WorkspaceSession.expires_at <= time.time()))
        db.add(WorkspaceSession(token_hash=session_hash(token), expires_at=int(time.time()) + MAX_AGE))
        db.commit()
    return token

def revoke_session(token):
    with Session() as db:
        db.execute(delete(WorkspaceSession).where(WorkspaceSession.token_hash == session_hash(token or '')))
        db.commit()
