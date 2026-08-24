from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from uuid6 import uuid7

from wallet_transfer.domain.ledger_entry import LedgerEntry, LedgerEntryType
from wallet_transfer.domain.transfer import Transfer
from wallet_transfer.repositories.errors import (
    IdempotencyKeyConflictError,
    RetryableRepositoryError,
)
from wallet_transfer.repositories.unit_of_work import UnitOfWorkFactory
from wallet_transfer.services.errors import (
    IdempotencyKeyReusedError,
    RetryExhaustedError,
    WalletNotFoundError,
)
from wallet_transfer.services.fingerprint import compute_request_fingerprint


@dataclass(frozen=True, slots=True)
class CreateTransferRequest:
    idempotency_key: str
    from_wallet_id: UUID
    to_wallet_id: UUID
    amount: int


class TransferService:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        id_generator: Callable[[], UUID] = uuid7,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        max_attempts: int = 3,
        base_backoff_seconds: float = 0.05,
    ) -> None:
        self._uow_factory = uow_factory
        self._id_generator = id_generator
        self._clock = clock
        self._sleep = sleep
        self.max_attempts = max_attempts
        self._base_backoff_seconds = base_backoff_seconds

    async def create_transfer(self, request: CreateTransferRequest) -> Transfer:
        fingerprint = compute_request_fingerprint(
            from_wallet_id=request.from_wallet_id,
            to_wallet_id=request.to_wallet_id,
            amount=request.amount,
        )

        last_error: RetryableRepositoryError | None = None
        for attempt in range(1, self.max_attempts + 1):
            if attempt > 1:
                await self._sleep_backoff(attempt)

            # Domain-level construction validates self-transfer / non-positive amount
            # (InvalidTransferError) before any transaction opens (ADR-0002's validation
            # boundary) — nothing persisted for these, and no retry applies.
            pending_transfer = Transfer.create(
                id=self._id_generator(),
                from_wallet_id=request.from_wallet_id,
                to_wallet_id=request.to_wallet_id,
                amount=request.amount,
                created_at=self._clock(),
            )

            try:
                return await self._attempt(request, fingerprint, pending_transfer)
            except RetryableRepositoryError as error:
                last_error = error
                continue

        assert last_error is not None
        raise RetryExhaustedError(self.max_attempts) from last_error

    async def _attempt(
        self,
        request: CreateTransferRequest,
        fingerprint: str,
        pending_transfer: Transfer,
    ) -> Transfer:
        async with self._uow_factory() as uow:
            try:
                await uow.idempotency.insert(
                    idempotency_key=request.idempotency_key,
                    request_fingerprint=fingerprint,
                    transfer_id=pending_transfer.id,
                )
            except IdempotencyKeyConflictError:
                existing = await uow.idempotency.get_by_key(request.idempotency_key)
                assert existing is not None
                if existing.request_fingerprint != fingerprint:
                    raise IdempotencyKeyReusedError(request.idempotency_key) from None
                cached = await uow.transfers.get_by_id(existing.transfer_id)
                assert cached is not None
                return cached

            await uow.transfers.insert(pending_transfer)

            wallets = await uow.wallets.get_two_for_update(
                request.from_wallet_id, request.to_wallet_id
            )
            missing = [
                wallet_id
                for wallet_id in (request.from_wallet_id, request.to_wallet_id)
                if wallet_id not in wallets
            ]
            if missing:
                raise WalletNotFoundError(missing)

            from_wallet = wallets[request.from_wallet_id]
            to_wallet = wallets[request.to_wallet_id]

            if from_wallet.balance < request.amount:
                failed = pending_transfer.mark_failed("INSUFFICIENT_FUNDS", at=self._clock())
                await uow.transfers.update(failed)
                return failed

            await uow.wallets.update_balance(from_wallet.id, from_wallet.balance - request.amount)
            await uow.wallets.update_balance(to_wallet.id, to_wallet.balance + request.amount)

            processed = pending_transfer.mark_processed(at=self._clock())
            await uow.transfers.update(processed)

            now = self._clock()
            await uow.ledger.insert_entries(
                [
                    LedgerEntry(
                        id=self._id_generator(),
                        transfer_id=processed.id,
                        wallet_id=from_wallet.id,
                        type=LedgerEntryType.DEBIT,
                        amount=request.amount,
                        created_at=now,
                    ),
                    LedgerEntry(
                        id=self._id_generator(),
                        transfer_id=processed.id,
                        wallet_id=to_wallet.id,
                        type=LedgerEntryType.CREDIT,
                        amount=request.amount,
                        created_at=now,
                    ),
                ]
            )

            return processed

    async def _sleep_backoff(self, attempt: int) -> None:
        max_delay = self._base_backoff_seconds * (2 ** (attempt - 2))
        await self._sleep(random.uniform(0, max_delay))
