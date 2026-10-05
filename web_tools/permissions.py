"""Existing POSIX ownership/mode requirements and read-only release diagnostics."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path


class PathPermissionsError(ValueError):
    """Keep concrete metadata separate from the administrator-facing renderer."""

    def __init__(self, issues, message='UI paths have invalid ownership or permissions'):
        self.issues = list(issues)
        super().__init__(message + ': ' + json.dumps(self.issues, ensure_ascii=True))


def permission_issue(path, info=None, *, private=False):
    if os.name == 'nt':
        return None
    info = Path(path).stat() if info is None else info
    expected = os.getuid()
    forbidden = info.st_mode & (0o077 if private else 0o022)
    if info.st_uid == expected and not forbidden:
        return None
    import pwd

    def user(uid):
        try:
            return pwd.getpwuid(uid).pw_name
        except KeyError:
            return None

    return {'path': str(Path(path).absolute()), 'owner_uid': info.st_uid,
            'owner_name': user(info.st_uid), 'expected_uid': expected,
            'expected_name': user(expected), 'mode': format(info.st_mode & 0o7777, '04o'),
            'owner_mismatch': info.st_uid != expected, 'forbidden_mode': format(forbidden, '03o'),
            'private': private}


def require_permissions(path, info=None, *, private=False, message=None):
    issue = permission_issue(path, info, private=private)
    if issue is not None:
        raise PathPermissionsError([issue], message or 'UI path has invalid ownership or permissions')


def check_release_paths(root):
    """Inspect only paths consumed by the current release branch, without seeding UI."""
    if os.name == 'nt':
        return
    from web_tools.paths import CUSTOM_SOURCE_IGNORED, local_path
    from web_tools.editor_files import read_regular, scan_sources
    from web_tools.source_tree import unchanged
    from web_tools.publication import read_pointer

    root = Path(root)
    runtime = local_path(root, 'web_runtime')
    custom = local_path(root, 'custom_web')
    issues = {}

    def inspect(path, *, private=False):
        # Reuse the link boundary before stat; missing paths will be created later.
        path = local_path(path.parent, path.name, hidden=True)
        if path.exists():
            issue = permission_issue(path, private=private)
            if issue is not None:
                issues[issue['path']] = issue

    def tree(folder):
        inspect(folder)
        if not folder.is_dir():
            return
        for directory, children, files in os.walk(folder, followlinks=False):
            children[:] = sorted(name for name in children if name not in CUSTOM_SOURCE_IGNORED)
            for name in [*children, *sorted(files)]:
                if name not in CUSTOM_SOURCE_IGNORED:
                    inspect(Path(directory) / name)

    tree(custom)
    custom_invalid = bool(issues)
    legacy = False
    if custom.exists() and not custom_invalid:
        try:
            legacy = json.loads(read_regular(custom, 'manifest.json')).get('format_version') == 1
        except (FileNotFoundError, ValueError, AttributeError):
            pass  # An unknown source baseline is preserved by the existing contract.
    transition = (runtime / 'source-transition.json').exists()
    legacy_tasks = []
    tasks = runtime / 'editor_tasks'
    if (not custom.exists() or legacy or transition) and tasks.is_dir():
        for task in sorted(tasks.iterdir()):
            if re.fullmatch(r'[a-f0-9]{32}', task.name) and (task / 'editor/custom_web').is_dir():
                legacy_tasks.append(task)
                tree(task / 'editor/custom_web')
                tree(task / 'editor/baseline')

    needs_template = not custom.exists() or legacy or transition
    install_base = not custom.exists() and not legacy_tasks and not transition
    if custom.exists() and not custom_invalid and not legacy and not transition:
        install_base = unchanged(root, scan_sources(custom))
    # Startup seeds the first publication even when working custom edits survive.
    # Without a live UI, this update still consumes the base signing inputs.
    if not read_pointer(runtime)['current']:
        install_base = True
    if needs_template or install_base:
        web = local_path(root, 'web')
        inspect(web)
        tree(web / 'src')
        tree(web / 'public')
        for name in ('index.html', 'preview.html', 'manifest.json'):
            inspect(web / name)
    if install_base:
        inspect(root)
        inspect(runtime)
        inspect(runtime / 'secrets/ed25519.key', private=True)
    # Recovery reads these through the same regular-file boundary before choosing a branch.
    for marker, names in (
        ('template-update.json', ('source-template.json', 'template-update.zip')),
        ('source-transition.json', ('source-transition-after.zip',)),
    ):
        if (runtime / marker).exists():
            inspect(runtime)
            for name in (marker, *names):
                inspect(runtime / name)
    if issues:
        raise PathPermissionsError([issues[name] for name in sorted(issues)])
