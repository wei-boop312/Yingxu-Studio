import asyncio
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import Field

from visual_platform.core.identity import (
    decode_access,
    hash_password,
    issue_access,
    new_refresh,
    verify_password,
)
from visual_platform.domain.schemas import StrictModel

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
# Equal-cost password verification for unknown users; initialized once per process.
_dummy = hash_password("not a real password 987654321")


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def origin_check(request):
    if request.headers.get("origin") != request.app.state.settings.origin:
        raise HTTPException(403, "origin_rejected")


def csrf_check(request):
    origin_check(request)
    cookie = request.cookies.get("visual_csrf", "")
    header = request.headers.get("x-csrf-token", "")
    if not cookie or len(cookie) > 200 or not hmac.compare_digest(cookie, header):
        raise HTTPException(403, "csrf_rejected")
    return digest(cookie)


def cookies(response, settings, access, refresh, csrf):
    common = dict(secure=settings.cookie_secure, samesite="strict", path="/")
    response.set_cookie("visual_access", access, httponly=True, max_age=900, **common)
    response.set_cookie("visual_refresh", refresh, httponly=True, max_age=604800, **common)
    response.set_cookie("visual_csrf", csrf, httponly=False, max_age=604800, **common)


def access_token(settings, user, family):
    return issue_access(
        user,
        settings.private_key,
        issuer=settings.issuer,
        audience=settings.audience,
        family_id=family,
    )


async def current_user(request: Request):
    settings = request.app.state.settings
    token = request.cookies.get("visual_access", "")
    if len(token) > 8192:
        raise HTTPException(401, "authentication_required")
    try:
        claims = decode_access(
            token, settings.public_key, issuer=settings.issuer, audience=settings.audience
        )
        uid = UUID(claims["sub"])
        sid = UUID(claims["sid"])
    except (jwt.PyJWTError, ValueError, TypeError):
        raise HTTPException(401, "authentication_required")
    async with request.app.state.auth_pool.acquire() as c:
        user = await c.fetchrow(
            """SELECT u.id,u.email_normalized,u.display_name FROM app.users u
          JOIN auth.session_families f ON f.user_id=u.id
          WHERE u.id=$1 AND f.id=$2 AND u.status='active' AND f.revoked_at IS NULL AND f.expires_at>now()""",
            uid,
            sid,
        )
    if not user:
        raise HTTPException(401, "authentication_required")
    request.state.family_id = sid
    return dict(user)


class Login(StrictModel):
    email: str = Field(min_length=3, max_length=320, strict=True)
    password: str = Field(min_length=1, max_length=1024, strict=True)


async def rate_limit(pool, buckets):
    async with pool.acquire() as c:
        async with c.transaction():
            for bucket, limit in sorted(buckets):
                count = await c.fetchval(
                    """INSERT INTO auth.login_limits(bucket,attempts) VALUES($1,1)
                ON CONFLICT(bucket) DO UPDATE SET
                attempts=CASE WHEN auth.login_limits.window_start<now()-interval '15 minutes' THEN 1 ELSE auth.login_limits.attempts+1 END,
                window_start=CASE WHEN auth.login_limits.window_start<now()-interval '15 minutes' THEN now() ELSE auth.login_limits.window_start END
                RETURNING attempts""",
                    bucket,
                )
                # Commit counters before reporting rejection.
                if count > limit:
                    return False
    return True


@router.post("/login")
async def login(body: Login, request: Request, response: Response):
    origin_check(request)
    email = body.email.strip().casefold()
    ip = request.client.host if request.client else "unknown"
    if not await rate_limit(
        request.app.state.auth_pool, [("email:" + digest(email), 10), ("ip:" + digest(ip), 100)]
    ):
        raise HTTPException(429, "login_rate_limited")
    async with request.app.state.auth_pool.acquire() as c:
        row = await c.fetchrow(
            """SELECT u.id,u.email_normalized,u.display_name,u.status,c.password_hash
             FROM app.users u JOIN auth.credentials c ON c.user_id=u.id WHERE u.email_normalized=$1""",
            email,
        )
    good = await asyncio.to_thread(
        verify_password, row["password_hash"] if row else _dummy, body.password
    )
    if not row or not good or row["status"] != "active":
        raise HTTPException(401, "invalid_credentials")
    family = uuid4()
    session = uuid4()
    refresh, refresh_hash = new_refresh()
    csrf = secrets.token_urlsafe(32)
    expiry = datetime.now(timezone.utc) + timedelta(days=7)
    async with request.app.state.auth_pool.acquire() as c:
        async with c.transaction():
            active = await c.fetchval("SELECT status FROM app.users WHERE id=$1", row["id"])
            if active != "active":
                raise HTTPException(401, "invalid_credentials")
            await c.execute(
                "INSERT INTO auth.session_families(id,user_id,csrf_digest,expires_at) VALUES($1,$2,$3,$4)",
                family,
                row["id"],
                digest(csrf),
                expiry,
            )
            await c.execute(
                "INSERT INTO auth.refresh_sessions(id,user_id,family_id,token_digest,expires_at) VALUES($1,$2,$3,$4,$5)",
                session,
                row["id"],
                family,
                refresh_hash,
                expiry,
            )
    cookies(
        response,
        request.app.state.settings,
        access_token(request.app.state.settings, row["id"], family),
        refresh,
        csrf,
    )
    return {
        "id": str(row["id"]),
        "email": row["email_normalized"],
        "display_name": row["display_name"],
    }


@router.post("/refresh")
async def refresh(request: Request, response: Response):
    csrf_hash = csrf_check(request)
    token = request.cookies.get("visual_refresh", "")
    if not token or len(token) > 200:
        raise HTTPException(401, "authentication_required")
    replacement, replacement_hash = new_refresh()
    new_id = uuid4()
    valid = False
    async with request.app.state.auth_pool.acquire() as c:
        async with c.transaction():
            old = await c.fetchrow(
                "SELECT id,family_id FROM auth.refresh_sessions WHERE token_digest=$1",
                digest(token),
            )
            if old:
                family = await c.fetchrow(
                    "SELECT * FROM auth.session_families WHERE id=$1 FOR UPDATE", old["family_id"]
                )
                session = await c.fetchrow(
                    "SELECT * FROM auth.refresh_sessions WHERE id=$1 FOR UPDATE", old["id"]
                )
                if family and hmac.compare_digest(family["csrf_digest"], csrf_hash):
                    now = datetime.now(timezone.utc)
                    active = await c.fetchval(
                        "SELECT status FROM app.users WHERE id=$1", session["user_id"]
                    )
                    if session["revoked_at"] or session["replaced_by"]:
                        await c.execute(
                            "UPDATE auth.session_families SET revoked_at=COALESCE(revoked_at,now()) WHERE id=$1",
                            family["id"],
                        )
                    elif (
                        not family["revoked_at"]
                        and family["expires_at"] > now
                        and session["expires_at"] > now
                        and active == "active"
                    ):
                        await c.execute(
                            "INSERT INTO auth.refresh_sessions(id,user_id,family_id,token_digest,expires_at) VALUES($1,$2,$3,$4,$5)",
                            new_id,
                            session["user_id"],
                            family["id"],
                            replacement_hash,
                            family["expires_at"],
                        )
                        await c.execute(
                            "UPDATE auth.refresh_sessions SET revoked_at=now(),replaced_by=$1 WHERE id=$2",
                            new_id,
                            session["id"],
                        )
                        valid = True
                        uid = session["user_id"]
                        sid = family["id"]
    # Never raise inside the revocation transaction: replay revocation must commit.
    if not valid:
        raise HTTPException(401, "authentication_required")
    cookies(
        response,
        request.app.state.settings,
        access_token(request.app.state.settings, uid, sid),
        replacement,
        request.cookies["visual_csrf"],
    )
    return {"refreshed": True}


@router.post("/logout", status_code=204)
async def logout(request: Request, response: Response):
    csrf_hash = csrf_check(request)
    token = request.cookies.get("visual_refresh", "")
    if token and len(token) <= 200:
        async with request.app.state.auth_pool.acquire() as c:
            await c.execute(
                """UPDATE auth.session_families f SET revoked_at=COALESCE(f.revoked_at,now())
             FROM auth.refresh_sessions s WHERE s.family_id=f.id AND s.token_digest=$1 AND f.csrf_digest=$2""",
                digest(token),
                csrf_hash,
            )
    for name in ("visual_access", "visual_refresh", "visual_csrf"):
        response.delete_cookie(
            name, path="/", secure=request.app.state.settings.cookie_secure, samesite="strict"
        )


@router.get("/me")
async def me(user=Depends(current_user)):
    return {
        "id": str(user["id"]),
        "email": user["email_normalized"],
        "display_name": user["display_name"],
    }
