"""Bounded Argon2id work outside the runtime event loop."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from weakref import finalize

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError

from core.results import CoreError

HASHER = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1,
                        hash_len=32, salt_len=16, type=Type.ID)
_POOL_ATTRIBUTE = '_yadreno_password_pool'


class _HashPool:
    def __init__(self):
        self.slots = asyncio.Semaphore(2)
        self.pending = 0
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='account-password')
        self.closed = False

    async def run(self, function, *args):
        if self.closed or self.pending >= 8:
            raise CoreError('authentication_busy', retryable=True)
        self.pending += 1
        try:
            await self.slots.acquire()
        except BaseException:
            self.pending -= 1
            raise
        if self.closed:
            self.slots.release()
            self.pending -= 1
            raise CoreError('authentication_busy', retryable=True)
        task = asyncio.get_running_loop().run_in_executor(self.executor, function, *args)

        def done(completed):
            self.slots.release()
            self.pending -= 1
            if not completed.cancelled():
                completed.exception()

        task.add_done_callback(done)
        # Disconnecting a browser must not free a slot while its thread hashes.
        return await asyncio.shield(task)


async def _run(function, *args):
    loop = asyncio.get_running_loop()
    pool = getattr(loop, _POOL_ATTRIBUTE, None)
    if pool is None:
        pool = _HashPool()
        setattr(loop, _POOL_ATTRIBUTE, pool)
        finalize(loop, pool.executor.shutdown, wait=False)
    return await pool.run(function, *args)


async def close_password_workers() -> None:
    """Finish already running hashes after HTTP ingress has stopped."""
    loop = asyncio.get_running_loop()
    pool = getattr(loop, _POOL_ATTRIBUTE, None)
    if pool is None:
        return
    delattr(loop, _POOL_ATTRIBUTE)
    pool.closed = True
    while pool.pending:
        await asyncio.sleep(.01)
    pool.executor.shutdown(wait=True, cancel_futures=True)


def validate_password(password: str) -> None:
    if not isinstance(password, str) or not 8 <= len(password) <= 128:
        raise CoreError('password_invalid', details={'min_length': 8, 'max_length': 128})
    try:
        password.encode('utf-8')
    except UnicodeError:
        raise CoreError('password_invalid') from None


async def hash_password(password: str) -> str:
    validate_password(password)
    return await _run(HASHER.hash, password)


async def verify_password(encoded: str | None, password: str) -> bool:
    if not isinstance(password, str) or len(password) > 128:
        return False
    try:
        password.encode('utf-8')
    except UnicodeError:
        return False

    def verify():
        if encoded is None:
            HASHER.hash(password)
            return False
        try:
            return HASHER.verify(encoded, password)
        except (VerificationError, InvalidHashError):
            return False

    return await _run(verify)
