"""Sign-in: password + authenticator code, a 5-try lockout, and hashed session tokens."""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import timedelta

import pyotp
from sqlalchemy import delete, select

from coolcandle.db.session import Sessions
from coolcandle.db.tables import BrokerAccount, Session, User
from coolcandle.domain.models import Mode
from coolcandle.ports.clock import Clock
from coolcandle.services.accounts.vault import Vault
from coolcandle.services.audit import Audit
from coolcandle.services.auth.passwords import hash_password, verify_password

MAX_FAILURES = 5
LOCKOUT = timedelta(minutes=15)
SESSION_LIFE = timedelta(hours=12)


class AuthError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class CurrentUser:
    id: str
    email: str
    account_id: str
    mode: Mode
    session_id: str


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class AuthService:
    def __init__(self, db: Sessions, clock: Clock, vault: Vault, audit: Audit) -> None:
        self.db = db
        self.clock = clock
        self.vault = vault
        self.audit = audit

    async def create_admin(self, email: str, password: str) -> str:
        """Create the admin and their account. Returns the authenticator secret to enrol."""
        secret = pyotp.random_base32()
        async with self.db() as session, session.begin():
            user = User(
                email=email.lower(),
                password_hash=hash_password(password),
                totp_secret_enc=self.vault.encrypt(secret),
            )
            session.add(user)
            await session.flush()
            session.add(BrokerAccount(user_id=user.id))
        return secret

    async def login(
        self, email: str, password: str, code: str, ip: str | None, agent: str | None
    ) -> str:
        now = self.clock.now()
        refused = AuthError("BAD_LOGIN", "Wrong email, password or code")
        async with self.db() as session, session.begin():
            user = (
                await session.execute(select(User).where(User.email == email.lower()))
            ).scalar_one_or_none()
            if user is None or not user.is_active:
                verify_password(hash_password("x"), password)  # same work for unknown users
                raise refused
            if user.locked_until is not None and user.locked_until > now:
                raise AuthError("LOCKED_OUT", "Too many tries. Try again in 15 minutes")

            totp = pyotp.TOTP(self.vault.decrypt(user.totp_secret_enc))
            good = verify_password(user.password_hash, password) and totp.verify(
                code, for_time=now, valid_window=1
            )
            if good:
                user.failed_logins = 0
                user.locked_until = None
                token = secrets.token_urlsafe(32)
                # Every login starts in paper mode.
                session.add(
                    Session(
                        user_id=user.id,
                        token_hash=_hash_token(token),
                        expires_at=now + SESSION_LIFE,
                        mode=Mode.PAPER,
                        ip=ip,
                        user_agent=(agent or "")[:255],
                    )
                )
                user_id = user.id
            else:
                user.failed_logins += 1
                if user.failed_logins >= MAX_FAILURES:
                    user.locked_until = now + LOCKOUT
                    user.failed_logins = 0
                user_id = None
        if user_id is None:
            await self.audit.write("login_failed", now, data={"email": email}, ip=ip)
            raise refused
        await self.audit.write("login", now, user_id=user_id, ip=ip)
        return token

    async def current(self, token: str) -> CurrentUser | None:
        now = self.clock.now()
        async with self.db() as session:
            row = (
                await session.execute(
                    select(Session, User, BrokerAccount)
                    .join(User, User.id == Session.user_id)
                    .join(BrokerAccount, BrokerAccount.user_id == User.id)
                    .where(Session.token_hash == _hash_token(token))
                )
            ).first()
        if row is None:
            return None
        stored, user, account = row
        if stored.expires_at <= now or not user.is_active:
            return None
        return CurrentUser(
            id=user.id,
            email=user.email,
            account_id=account.id,
            mode=Mode(stored.mode),
            session_id=stored.id,
        )

    async def set_mode(self, session_id: str, mode: Mode) -> None:
        async with self.db() as session, session.begin():
            row = await session.get(Session, session_id)
            if row is not None:
                row.mode = mode

    async def logout(self, token: str) -> None:
        async with self.db() as session, session.begin():
            await session.execute(delete(Session).where(Session.token_hash == _hash_token(token)))

    async def logout_everywhere(self, user_id: str) -> None:
        async with self.db() as session, session.begin():
            await session.execute(delete(Session).where(Session.user_id == user_id))

    async def sessions(self, user_id: str) -> list[Session]:
        async with self.db() as session:
            rows = await session.execute(
                select(Session)
                .where(Session.user_id == user_id, Session.expires_at > self.clock.now())
                .order_by(Session.created_at.desc())
            )
        return list(rows.scalars())
