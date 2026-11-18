import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError

_hasher = PasswordHasher()


def hash_password(password: str):
    if not isinstance(password, str) or not 12 <= len(password) <= 1024:
        raise ValueError("password length must be 12..1024")
    return _hasher.hash(password)


def verify_password(encoded: str, candidate: str):
    if not isinstance(candidate, str) or len(candidate) > 1024:
        return False
    try:
        return _hasher.verify(encoded, candidate)
    except VerificationError:
        return False


def issue_access(
    user_id: UUID, private_key: str, *, issuer: str, audience: str, family_id: UUID | None = None
):
    now = datetime.now(timezone.utc)
    return jwt.encode(
        dict(
            sub=str(user_id),
            iss=issuer,
            aud=audience,
            iat=now,
            exp=now + timedelta(minutes=15),
            jti=str(uuid4()),
            sid=str(family_id or uuid4()),
        ),
        private_key,
        algorithm="RS256",
    )


def decode_access(token: str, public_key: str, *, issuer: str, audience: str):
    claims = jwt.decode(
        token,
        public_key,
        algorithms=["RS256"],
        issuer=issuer,
        audience=audience,
        options={"require": ["sub", "iss", "aud", "iat", "exp", "jti", "sid"]},
    )
    UUID(claims["sub"])
    UUID(claims["jti"])
    UUID(claims["sid"])
    return claims


def new_refresh():
    token = secrets.token_urlsafe(48)
    return token, hashlib.sha256(token.encode()).hexdigest()
