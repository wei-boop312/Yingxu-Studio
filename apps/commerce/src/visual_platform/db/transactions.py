from contextlib import asynccontextmanager
from uuid import UUID


@asynccontextmanager
async def tenant_transaction(pool, trusted_user_id: UUID):
    if not isinstance(trusted_user_id, UUID):
        raise TypeError("verified UUID identity required")
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', $1, true)", str(trusted_user_id))
            yield conn
