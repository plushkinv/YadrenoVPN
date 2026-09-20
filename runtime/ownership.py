"""OS-held runtime lock; stale files do not become permanent owners."""
from __future__ import annotations

import os
from pathlib import Path


class RuntimeOwner:
    def __init__(self, path: Path):
        self.path = path
        self._file = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open('a+b')
        try:
            if os.name == 'posix':
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            elif os.name == 'nt':
                import msvcrt

                handle.seek(0, 2)
                if handle.tell() == 0:
                    handle.write(b'\0')
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                raise RuntimeError('Runtime locking is unsupported on this platform')
        except BaseException:
            handle.close()
            raise RuntimeError('Another runtime owns this installation') from None
        self._file = handle

    def release(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None
