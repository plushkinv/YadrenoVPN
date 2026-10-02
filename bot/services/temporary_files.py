"""Installation-local upload limits and daily temporary-file retention."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
import stat
import time

UPLOAD_MAX_FILES = 5
UPLOAD_MAX_BYTES = 10 * 1024 * 1024
TEMP_RETENTION_DAYS = 7
UPLOAD_RELATIVE = 'tmp/yadreno_uploads'
logger = logging.getLogger(__name__)


def attachment_sources(uploads) -> list[dict[str, str]]:
    """Describe only this message's ordered uploads, never enumerate temporary storage."""
    return [{'filename': upload.filename, 'source_path': str(upload.path.absolute()),
             'expires_at': datetime.fromtimestamp(upload.path.stat().st_mtime + TEMP_RETENTION_DAYS * 86400,
                                                  timezone.utc).isoformat()}
            for upload in uploads]


def cleanup_temporary_files(project_root: Path, *, now: float | None = None) -> int:
    """Remove expired local files without following directory links or junctions."""
    from web_tools.editor_files import checked_directory, directory_handle

    root = Path(project_root).absolute() / 'tmp'
    if not root.exists():
        return 0
    checked_directory(root)
    cutoff = (time.time() if now is None else now) - TEMP_RETENTION_DAYS * 86400
    removed = 0

    def visit(relative: str) -> None:
        nonlocal removed
        with directory_handle(root, relative) as (folder, descriptor):
            with os.scandir(descriptor if descriptor is not None else folder) as entries:
                names = [entry.name for entry in entries]
            for name in names:
                try:
                    info = (os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                            if descriptor is not None else (folder / name).lstat())
                    link = stat.S_ISLNK(info.st_mode) or bool(
                        getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0))
                    if stat.S_ISDIR(info.st_mode) and not link:
                        visit('/'.join(filter(None, (relative, name))))
                        try:
                            if descriptor is not None:
                                os.rmdir(name, dir_fd=descriptor)
                            else:
                                (folder / name).rmdir()
                        except OSError:
                            pass
                    elif info.st_mtime < cutoff and (stat.S_ISREG(info.st_mode) or link):
                        if descriptor is not None:
                            os.unlink(name, dir_fd=descriptor)
                        elif link and stat.S_ISDIR(info.st_mode):
                            (folder / name).rmdir()
                        else:
                            (folder / name).unlink()
                        removed += 1
                except (OSError, ValueError) as error:
                    logger.warning('Temporary-file cleanup skipped an entry: %s', type(error).__name__)

    visit('')
    return removed
