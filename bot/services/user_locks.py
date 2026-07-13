"""
Locks to synchronize transactions with user balances.

Used to prevent race conditions during balance operations.
"""
import asyncio
from collections import defaultdict


user_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
