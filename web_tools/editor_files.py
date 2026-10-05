"""Bounded regular-file access for a trusted, private editor task directory.

These guards are not process isolation. Callers must keep task roots inaccessible
to untrusted local processes; build workers need their own OS isolation.
"""
from __future__ import annotations

import contextlib
import os
import stat
import uuid
from pathlib import Path

from web_tools.package import MAX_BYTES, MAX_FILES
from web_tools.paths import CUSTOM_SOURCE_IGNORED, atomic_write, local_path, relative_name
from web_tools.errors import WebSourceError
from web_tools.permissions import require_permissions

TEXT_EXTENSIONS = frozenset({'.ts', '.tsx', '.js', '.jsx', '.mjs', '.css', '.json', '.html', '.md', '.txt'})
ASSET_EXTENSIONS = frozenset({'.svg', '.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp', '.ico', '.woff2'})


def source_name(name):
    try:
        name = relative_name(name, hidden=True)
    except ValueError as error:
        raise WebSourceError('web_source_path_invalid', str(error), file=name,
                             next_action='Use an exact path relative to custom_web, with forward slashes and no parent traversal.') from error
    parts = name.split('/')
    if (any(part in CUSTOM_SOURCE_IGNORED or part.lower() == '.env'
            or part.lower().startswith('.env.') for part in parts)
            or Path(name).suffix not in TEXT_EXTENSIONS | ASSET_EXTENSIONS):
        raise WebSourceError('web_source_file_unsupported', 'Unsupported working-source file.', file=name,
                             next_action='Keep secrets, databases and archives outside custom_web. '
                             'Use a supported source or asset extension: ' + ', '.join(sorted(TEXT_EXTENSIONS | ASSET_EXTENSIONS)) + '.')
    return name


def checked_info(info, *, directory=False, private=False, path=None):
    if (stat.S_ISLNK(info.st_mode)
            or getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0)
            or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
            or not directory and info.st_nlink != 1):
        raise ValueError('editor paths require regular files and directories without links')
    require_permissions(path or '<editor>', info, private=private,
                        message='editor files require private administrator ownership')
    return info


def checked_directory(path, *, private=False):
    """Check every ancestor for links, and ownership on the selected root."""
    path = Path(path).absolute()
    for parent in (*reversed(path.parents), path):
        info = parent.lstat()
        if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
                or getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0)):
            raise ValueError('editor directory must not contain links')
    checked_info(path.lstat(), directory=True, private=private, path=path)
    return path.resolve(strict=True)


@contextlib.contextmanager
def directory_handle(root, relative='', *, create=False):
    """Pin each POSIX directory while traversing; Windows retains path guards."""
    root = checked_directory(root)
    parts = relative_name(relative, hidden=True).split('/') if relative else []
    if os.name == 'nt':
        current = root
        for part in parts:
            current = local_path(current, part, hidden=True)
            if create:
                current.mkdir(mode=0o700, exist_ok=True)
            checked_directory(current)
        yield current, None
        return
    descriptor = os.open(root.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in root.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        checked_info(os.fstat(descriptor), directory=True, path=root)
        current = root
        for part in parts:
            if create:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
            checked_info(os.fstat(descriptor), directory=True, path=current / part)
            current = current / part
        yield current, descriptor
    finally:
        os.close(descriptor)


def read_regular(root, name, *, maximum=MAX_BYTES):
    name = relative_name(name, hidden=True)
    parent, _, leaf = name.rpartition('/')
    with directory_handle(root, parent) as (folder, descriptor):
        before = checked_info(os.stat(leaf, dir_fd=descriptor, follow_symlinks=False)
                              if descriptor is not None else (folder / leaf).lstat(), path=folder / leaf)
        if before.st_size > maximum:
            raise ValueError('editor file exceeds resource limits')
        flags = (os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
                 | getattr(os, 'O_BINARY', 0))
        file_descriptor = os.open(leaf, flags, dir_fd=descriptor) if descriptor is not None else os.open(folder / leaf, flags)
        with os.fdopen(file_descriptor, 'rb') as stream:
            opened = checked_info(os.fstat(stream.fileno()), path=folder / leaf)
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                raise ValueError('editor file changed while opening')
            content = stream.read(maximum + 1)
            after = checked_info(os.fstat(stream.fileno()), path=folder / leaf)
            if (len(content) > maximum or after.st_size != len(content)
                    or (opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns)
                    != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
                raise ValueError('editor file changed while reading or exceeds limits')
            return content


def scan_sources(root):
    """Bound traversal and bytes before exposing or copying custom sources."""
    checked_directory(root)
    pending = ['']
    files = {}
    size = directories = 0
    while pending:
        relative = pending.pop()
        with directory_handle(root, relative) as (folder, descriptor):
            with os.scandir(descriptor if descriptor is not None else folder) as entries:
                for entry in entries:
                    if entry.name in CUSTOM_SOURCE_IGNORED:
                        continue
                    name = '/'.join(filter(None, (relative, entry.name)))
                    info = entry.stat(follow_symlinks=False)
                    if stat.S_ISDIR(info.st_mode):
                        checked_info(info, directory=True, path=folder / entry.name)
                        directories += 1
                        if directories > MAX_FILES:
                            raise ValueError('editor tree exceeds resource limits')
                        pending.append(name)
                    else:
                        checked_info(info, path=folder / entry.name)
                        source_name(name)
                        if len(files) >= MAX_FILES:
                            raise ValueError('editor tree exceeds resource limits')
                        content = read_regular(root, name, maximum=MAX_BYTES - size)
                        size += len(content)
                        files[name] = content
    return files


def write_regular(root, name, content):
    """Atomically replace one regular file under a pinned destination parent."""
    name = relative_name(name, hidden=True)
    parent, _, leaf = name.rpartition('/')
    with directory_handle(root, parent, create=True) as (folder, descriptor):
        try:
            checked_info(os.stat(leaf, dir_fd=descriptor, follow_symlinks=False)
                         if descriptor is not None else (folder / leaf).lstat(), path=folder / leaf)
        except FileNotFoundError:
            pass
        if descriptor is None:
            atomic_write(folder / leaf, content)
            return
        temporary = '.editor-' + uuid.uuid4().hex
        file_descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                  0o600, dir_fd=descriptor)
        try:
            with os.fdopen(file_descriptor, 'wb') as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, leaf, src_dir_fd=descriptor, dst_dir_fd=descriptor)
            os.fsync(descriptor)
        finally:
            try:
                os.unlink(temporary, dir_fd=descriptor)
            except FileNotFoundError:
                pass


def remove_regular(root, name):
    name = relative_name(name, hidden=True)
    parent, _, leaf = name.rpartition('/')
    with directory_handle(root, parent) as (folder, descriptor):
        checked_info(os.stat(leaf, dir_fd=descriptor, follow_symlinks=False)
                     if descriptor is not None else (folder / leaf).lstat(), path=folder / leaf)
        if descriptor is None:
            (folder / leaf).unlink()
        else:
            os.unlink(leaf, dir_fd=descriptor)
            os.fsync(descriptor)
