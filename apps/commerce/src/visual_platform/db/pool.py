import asyncpg


async def open_pool(dsn: str, role: str):
    pool = await asyncpg.create_pool(
        dsn,
        min_size=1,
        max_size=10,
        command_timeout=10,
        server_settings={
            "application_name": "visual-platform",
            "statement_timeout": "10000",
            "idle_in_transaction_session_timeout": "15000",
        },
    )
    try:
        async with pool.acquire() as c:
            row = await c.fetchrow(
                "SELECT rolsuper,rolbypassrls,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname=current_user"
            )
            if any(row):
                raise RuntimeError("runtime role must be unprivileged")
            if not await c.fetchval("SELECT pg_has_role(current_user,$1,'MEMBER')", role):
                raise RuntimeError("runtime role group missing")
            if await c.fetchval(
                "SELECT EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname IN ('app','auth','ops') AND c.relowner=(SELECT oid FROM pg_roles WHERE rolname=current_user))"
            ):
                raise RuntimeError("runtime role cannot own application objects")
        return pool
    except BaseException:
        await pool.close()
        raise
