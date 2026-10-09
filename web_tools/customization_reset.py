"""Restore the shipped UI through the existing source/publication transaction."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import json
from pathlib import Path

from web_tools.compatibility import current_capabilities, verification_versions
from web_tools.distribution import install_base
from web_tools.editor_files import read_regular, scan_sources
from web_tools.package import verify_package
from web_tools.paths import atomic_write, canonical, local_path, publication_lock
from web_tools.publication import _activate_verified, read_pointer
from web_tools.source_tree import STATE, archive_bytes, fingerprint, inventory, read_archive, replace_tree, template


def has_web(root):
    """Do not provision Web just because a Telegram-only reset was requested."""
    return (local_path(root, 'custom_web').exists()
            or local_path(root, 'web_runtime/active.json').exists())


def preview_reset(root):
    if not has_web(root):
        return ['Web не установлен; файловый сброс пропущен']
    return ['Опубликованный дизайн и неопубликованные правки → базовая поставка установленной версии',
            'Исходники и их эталон будут восстановлены вместе с публикацией']


@contextmanager
def prepare_reset(root):
    """Keep editor writes excluded from preparation through backup and activation."""
    root = Path(root)
    if not has_web(root):
        yield None
        return
    from web_tools.editor_publication import recover
    from web_tools.source_tree import _recover_template
    recover(root)
    runtime = local_path(root, 'web_runtime', directory=True)
    with publication_lock(runtime / 'source-lock'):
        with publication_lock(runtime):
            _recover_template(runtime, local_path(root, 'custom_web'))
            if (runtime / 'source-transition.json').exists():
                from web_tools.source_transition import materialize
                materialize(root)
            before = read_pointer(runtime)
        candidate = install_base(root, runtime)
        yield PreparedReset(root, before, candidate)


@dataclass
class PreparedReset:
    root: Path
    before: dict
    candidate: dict

    def apply(self):
        """The pointer commits sources and baseline together; never compile custom code."""
        runtime = self.root / 'web_runtime'
        folder = local_path(self.root, 'custom_web')
        stage = local_path(runtime, 'staging/' + self.candidate['build_id'])
        content = read_regular(stage, 'package.zip')
        trust = json.loads(read_regular(runtime, 'identity.json'))
        signed, assets = verify_package(content, trust, **verification_versions(current_capabilities()))
        sources = read_archive(read_regular(stage, 'sources.zip'))
        if sources != template(self.root):
            raise ValueError('base UI sources changed after reset preparation')
        with publication_lock(runtime):
            if read_pointer(runtime) != self.before:
                raise ValueError('UI publication changed after reset preparation')
            before = scan_sources(folder) if folder.exists() else {}
            baseline = local_path(runtime, STATE)
            transaction = {
                'operation': 'custom_reset', 'before': self.before,
                'after': {'current': signed['manifest']['build_id'], 'previous': None},
                'baseline_present': baseline.exists(), 'sources_present': folder.exists(),
                'template_after': {'format_version': 1, 'template_version': fingerprint(sources),
                                   'template': inventory(sources)},
            }
            atomic_write(runtime / 'template-update.zip', archive_bytes(before))
            if baseline.exists():
                atomic_write(runtime / 'template-update-baseline.bin', read_regular(runtime, STATE))
            atomic_write(runtime / 'template-update.json', canonical(transaction))
            try:
                replace_tree(folder, sources)
                atomic_write(baseline, canonical(transaction['template_after']))
                _activate_verified(runtime, content, trust, signed, assets)
            except Exception:
                # A post-commit housekeeping failure does not undo a verified UI.
                if not recover_reset(runtime, folder, transaction):
                    raise
            else:
                recover_reset(runtime, folder, transaction)
        return ['Базовая Web-публикация активирована: ' + signed['manifest']['build_id'],
                'Неопубликованные правки сброшены; исходники и эталон восстановлены']


def recover_reset(runtime, folder, transaction):
    """Called under the publication lock by normal source-tree startup recovery."""
    from web_tools.release import publication
    pointer = read_pointer(runtime)
    if pointer not in (transaction['before'], transaction['after']):
        raise ValueError('another UI publication followed the interrupted customization reset')
    before = read_archive(read_regular(runtime, 'template-update.zip'))
    actual = inventory(scan_sources(folder)) if folder.exists() else {}
    old, new = inventory(before), transaction['template_after']['template']
    if any(actual.get(name) not in (old.get(name), new.get(name)) for name in set(actual) | set(old) | set(new)):
        raise ValueError('working sources changed outside the interrupted customization reset')
    committed = pointer == transaction['after']
    if committed:
        publication(runtime, pointer['current'], current_capabilities())
        if actual != new:
            raise ValueError('committed reset sources differ from the base publication')
        atomic_write(runtime / STATE, canonical(transaction['template_after']))
    else:
        replace_tree(folder, before)
        if not transaction['sources_present']:
            # Only empty directories created by this interrupted replacement can go.
            for child in sorted(folder.rglob('*'), key=lambda path: len(path.parts), reverse=True):
                if child.is_dir():
                    child.rmdir()
            folder.rmdir()
        if transaction['baseline_present']:
            atomic_write(runtime / STATE, read_regular(runtime, 'template-update-baseline.bin'))
        else:
            (runtime / STATE).unlink(missing_ok=True)
    (runtime / 'template-update.json').unlink()
    (runtime / 'template-update.zip').unlink(missing_ok=True)
    (runtime / 'template-update-baseline.bin').unlink(missing_ok=True)
    return committed
