from __future__ import annotations

from uuid import UUID

import asyncpg

from wallet_transfer.repositories.errors import IdempotencyKeyConflictError
from wallet_transfer.repositories.idempotency_repository import IdempotencyRecord


class AsyncpgIdempotencyRepository:
    def __init__(self, connection: asyncpg.pool.PoolConnectionProxy) -> None:
        self._connection = connection

    async def insert(
        self, *, idempotency_key: str, request_fingerprint: str, transfer_id: UUID
    ) -> None:
        try:
            await self._connection.execute(
                """
                INSERT INTO idempotency_records (idempotency_key, request_fingerprint, transfer_id)
                VALUES ($1, $2, $3)
                """,
                idempotency_key,
                request_fingerprint,
                transfer_id,
            )
        except asyncpg.exceptions.UniqueViolationError as error:
            raise IdempotencyKeyConflictError(idempotency_key) from error

    async def get_by_key(self, idempotency_key: str) -> IdempotencyRecord | None:
        row = await self._connection.fetchrow(
            """
            SELECT idempotency_key, request_fingerprint, transfer_id, created_at
            FROM idempotency_records WHERE idempotency_key = $1
            """,
            idempotency_key,
        )
        if row is None:
            return None
        return IdempotencyRecord(
            idempotency_key=row["idempotency_key"],
            request_fingerprint=row["request_fingerprint"],
            transfer_id=row["transfer_id"],
            created_at=row["created_at"],
        )
