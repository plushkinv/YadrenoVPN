"""One installation-owned editable frontend and verified source archives."""
from __future__ import annotations

import io
import json
import re
import zipfile
from pathlib import Path

from web_tools.editor_files import read_regular, remove_regular, scan_sources, source_name, write_regular
from web_tools.package import MAX_BYTES, MAX_FILES, digest
from web_tools.paths import atomic_write, canonical, local_path, private_directory, publication_lock

STATE = 'source-template.json'


def inventory(files):
    return {name: {'sha256': digest(data), 'size': len(data)} for name, data in sorted(files.items())}


def fingerprint(files):
    return digest(canonical(inventory(files)))


def template(root):
    """Only frontend inputs are copied; dependencies and compiler stay outside."""
    web = Path(root) / 'web'
    files = {'src/' + name: data for name, data in scan_sources(web / 'src').items()}
    for name in ('index.html', 'preview.html', 'manifest.json'):
        files[name] = read_regular(web, name)
    public = web / 'public'
    if public.exists():
        files.update({'public/' + name: data for name, data in scan_sources(public).items()})
    return files


def archive_bytes(files):
    """Deterministic contents, including an integrity inventory, without paths outside the tree."""
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted({**files, '_sources.json': canonical(inventory(files))}.items()):
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100600 << 16
            archive.writestr(info, data)
    content = output.getvalue()
    if read_archive(content) != files:
        raise ValueError('source archive verification failed')
    return content


def read_archive(content):
    if len(content) > MAX_BYTES:
        raise ValueError('source archive exceeds resource limits')
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        entries = archive.infolist()
        names = [item.filename for item in entries]
        if (len(entries) > MAX_FILES + 1 or len(names) != len(set(names))
                or sum(item.file_size for item in entries) > MAX_BYTES + 4 * 1024 * 1024):
            raise ValueError('source archive has invalid inventory or size')
        files = {source_name(item.filename): archive.read(item) for item in entries
                 if item.filename != '_sources.json'}
        if json.loads(archive.read('_sources.json')) != inventory(files):
            raise ValueError('source archive integrity check failed')
        return files


def replace_tree(folder, files):
    """Restore exact source membership, preserving only explicitly ignored caches."""
    folder = Path(folder)
    private_directory(folder)
    before = scan_sources(folder)
    for name in sorted(set(before) - set(files)):
        remove_regular(folder, name)
    for name, data in files.items():
        if before.get(name) != data:
            write_regular(folder, name, data)


def ensure(root):
    """Seed once. A missing baseline on an existing tree never permits replacement."""
    root = Path(root)
    runtime = local_path(root, 'web_runtime', directory=True)
    folder = local_path(root, 'custom_web')
    with publication_lock(runtime):
        _recover_template(runtime, folder)
        if (runtime / 'source-transition.json').exists():
            from web_tools.source_transition import materialize
            materialize(root)
        if folder.exists():
            manifest = folder / 'manifest.json'
            try:
                legacy = manifest.exists() and json.loads(read_regular(folder, 'manifest.json')).get('format_version') == 1
            except (ValueError, AttributeError):
                legacy = False  # Malformed working sources are editable, not a startup failure.
            if legacy:
                from web_tools.source_transition import materialize
                materialize(root)
            return folder
        tasks = runtime / 'editor_tasks'
        if tasks.exists() and any(re.fullmatch(r'[a-f0-9]{32}', item.name) and
                                  (item / 'editor/custom_web').is_dir() for item in tasks.iterdir()):
            from web_tools.source_transition import materialize
            folder.mkdir(mode=0o700)
            materialize(root, overlay={})
            return folder
        files = template(root)
        replace_tree(folder, files)
        atomic_write(runtime / STATE, canonical({'format_version': 1, 'template_version': fingerprint(files),
                                                 'template': inventory(files)}))
    return folder


def unchanged(root, files=None):
    root = Path(root)
    try:
        state = json.loads(read_regular(root / 'web_runtime', STATE))
    except (FileNotFoundError, ValueError):
        return False
    files = scan_sources(root / 'custom_web') if files is None else files
    return (isinstance(state, dict) and state.get('format_version') == 1 and state.get('template') == inventory(files))


def update_template(root):
    """Compare to the locally installed old baseline, independent of skipped releases."""
    root = Path(root)
    folder = ensure(root)
    runtime = root / 'web_runtime'
    with publication_lock(runtime):
        if not unchanged(root):
            return False
        before = scan_sources(folder)
        files = template(root)
        if before == files:
            return False
        # Recovery material is persisted before replacing either source or baseline.
        atomic_write(runtime / 'template-update.zip', archive_bytes(before))
        atomic_write(runtime / 'template-update.json', canonical({
            'before': json.loads(read_regular(runtime, STATE)), 'after': inventory(files)}))
        try:
            replace_tree(folder, files)
            atomic_write(runtime / STATE, canonical({'format_version': 1, 'template_version': fingerprint(files),
                                                     'template': inventory(files)}))
        except BaseException:
            _recover_template(runtime, folder)
            raise
        _recover_template(runtime, folder)
        return True


def _recover_template(runtime, folder):
    marker = runtime / 'template-update.json'
    if not marker.exists():
        return
    transaction = json.loads(read_regular(runtime, marker.name))
    state = json.loads(read_regular(runtime, STATE))
    if state.get('template') != transaction['after']:
        before = read_archive(read_regular(runtime, 'template-update.zip'))
        expected = inventory(before)
        actual = inventory(scan_sources(folder)) if folder.exists() else {}
        if any(actual.get(name) not in (expected.get(name), transaction['after'].get(name))
               for name in set(actual) | set(expected) | set(transaction['after'])):
            raise ValueError('working sources changed outside an interrupted template update')
        replace_tree(folder, before)
        atomic_write(runtime / STATE, canonical(transaction['before']))
    marker.unlink()
    (runtime / 'template-update.zip').unlink(missing_ok=True)


def backup_request(root, task_id):
    """Exactly one verified archive per locally admitted request, reused on resume."""
    if not re.fullmatch(r'[a-f0-9]{32}', task_id):
        raise ValueError('invalid source backup identity')
    root = Path(root)
    folder = ensure(root)
    backup_id = 'web-' + task_id
    path = local_path(root, 'backup/' + backup_id + '.zip')
    with publication_lock(root / 'web_runtime'):
        if path.exists():
            files = read_archive(read_regular(path.parent, path.name))
        else:
            files = scan_sources(folder)
            atomic_write(path, archive_bytes(files))
        return {'created': True, 'id': backup_id, 'path': str(path), 'source_hash': fingerprint(files)}


def restore_source(root, source):
    from web_tools.errors import WebSourceError
    root = Path(root)
    if source == 'published':
        from web_tools.publication import read_pointer
        selected = read_pointer(root / 'web_runtime')['current']
        if not isinstance(selected, str) or not re.fullmatch(r'[a-f0-9]{32}', selected):
            raise WebSourceError('web_restore_missing', 'There is no current publication to restore.',
                                 next_action='Use a request backup id shown in web.workspace.')
        path = root / 'web_runtime/publications' / selected / 'sources.zip'
        if not path.exists():
            raise WebSourceError('web_restore_missing', 'This older publication has no verifiable source snapshot.',
                                 next_action='Use a preserved request source archive; do not reconstruct sources from compiled assets.')
        return read_archive(read_regular(path.parent, path.name))
    if not isinstance(source, str) or not re.fullmatch(r'web-[a-f0-9]{32}', source):
        raise WebSourceError('web_restore_invalid', 'Unknown restoration source.',
                             next_action='Use source=published or a web-… backup id from web.workspace.')
    try:
        return read_archive(read_regular(root / 'backup', source + '.zip'))
    except FileNotFoundError:
        raise WebSourceError('web_backup_expired', 'The requested source archive is missing or expired.',
                             next_action='Use an available request archive or source=published to reset unpublished changes.') from None
