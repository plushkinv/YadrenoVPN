"""Read-only installed discovery; never import configuration or user files."""
import importlib.util
from pathlib import Path
import shlex
import sys

from . import VERSION


def invocation():
    """Copyable shell commands bound to this installation, without probing it."""
    root = Path(__file__).resolve().parents[1]
    command = 'cd -- ' + shlex.quote(str(root)) + ' && ' + shlex.join(
        [sys.executable, '-B', '-m', 'extension_tools.check'])
    return {'command': command,
            'example_command': command + ' --extension custom_extensions/demo.py --tests tests/.tmp/check_demo.py',
            'help_command': command + ' --help'}


def capabilities():
    reason = None
    try:
        if sys.platform != 'linux':
            raise RuntimeError('Extension checking requires a root-managed Linux systemd installation.')
        from web_tools.compiler_rootfs import prerequisites
        prerequisites()
        missing = [name for name in ('aiohttp', 'pydantic', 'aiogram', 'argon2',
                                    'phonenumbers', 'cryptography', 'jwt', 'PIL', 'qrcode')
                   if importlib.util.find_spec(name) is None]
        if missing:
            reason = 'Missing installed Python dependencies: ' + ', '.join(missing)
    except Exception as error:
        reason = str(error).replace('UI compilation', 'Extension checking')[:500]
    return {'available': reason is None, 'contract_version': 1, 'version': VERSION,
            'reason': reason, **invocation(),
            'shell_timeout_seconds': 240,
            'help': '--help', 'documentation': {'file': 'agent_extension_platform.md',
                                               'id': 'extension_check_api'}}


def inspection():
    """Keep discovery compact; detailed diagnostics remain available in the CLI."""
    details = capabilities()
    return {key: details[key] for key in (
        'available', 'contract_version', 'version', 'command',
        'shell_timeout_seconds', 'documentation')}
