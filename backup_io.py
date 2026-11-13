"""Shared backup I/O; scenario-specific ownership and validation stay with callers.

Keep this module stdlib-only: maintenance runners carry a private copy so they
can continue after replacing the application's checkout with an older version.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
from pathlib import Path
import sqlite3
import stat
import zipfile


def copy_stream(source, target=None):
    """Read the complete stream, optionally copying it, and return its SHA-256."""
    digest = hashlib.sha256()
    for chunk in iter(lambda: source.read(1024 * 1024), b''):
        digest.update(chunk)
        if target is not None:
            target.write(chunk)
    return digest.hexdigest()


def file_digest(path):
    with path.open('rb') as source:
        return copy_stream(source)


def write_zip_file(archive, path, name, *, metadata=True):
    """Copy a regular file with its ZIP metadata and hash the bytes actually read."""
    path = Path(path)
    info = zipfile.ZipInfo.from_file(path, name) if metadata else zipfile.ZipInfo(name)
    info.compress_type = archive.compression
    # Manifest archives stream unknown-sized entries; ordinary ZIPs retain the
    # original file-size-based ZIP64 selection used by ZipFile.write().
    with path.open('rb') as source, archive.open(info, 'w', force_zip64=not metadata) as target:
        return copy_stream(source, target)


def supported_zip_entry(info, *, directories=False):
    """Reject encryption and special files; callers enforce their path contract."""
    kind = stat.S_IFMT(info.external_attr >> 16)
    return (not info.flag_bits & 1
            and kind in ({0, stat.S_IFREG, stat.S_IFDIR} if directories else {0, stat.S_IFREG})
            and (directories or not info.is_dir())
            and (kind != stat.S_IFDIR or info.is_dir()))


def verified_content(content, digest):
    if hashlib.sha256(content).hexdigest() != digest:
        raise ValueError('incomplete or corrupted UI backup')
    return content


def read_verified_zip(archive, inventory, *, manifest_name):
    """Read every inventoried byte, including material a restore will not apply."""
    entries = archive.infolist()
    names = [info.filename for info in entries]
    if (len(names) != len(set(names)) or set(names) != set(inventory) | {manifest_name}
            or any(not supported_zip_entry(info) for info in entries)):
        raise ValueError('incomplete or corrupted UI backup')
    return {name: verified_content(archive.read(name), digest) for name, digest in inventory.items()}


@contextmanager
def sqlite_snapshot(source, destination, *, timeout=5.0):
    """Use SQLite online backup; the caller owns the source and checks the copy."""
    target = sqlite3.connect(destination, timeout=timeout)
    try:
        source.backup(target)
        yield target
    finally:
        target.close()
