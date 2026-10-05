"""Actionable Web operation failures; unknown internal failures belong to the platform."""
from __future__ import annotations

import logging
import re
import uuid

logger = logging.getLogger(__name__)


class WebSourceError(ValueError):
    def __init__(self, code, message, *, file=None, line=None, next_action='Correct the indicated working file and build again.'):
        super().__init__(message)
        self.code, self.file, self.line, self.next_action = code, file, line, next_action


class WebPlatformError(RuntimeError):
    pass


def failure(error, *, operation, root=None):
    """Return a bounded diagnostic, never a stack trace or a suggestion to repair core."""
    reference = uuid.uuid4().hex[:16]
    logger.error('Web operation %s failed; diagnostic=%s', operation, reference, exc_info=error)
    if isinstance(error, BlockingIOError):
        error = WebSourceError('web_busy', 'Another source or publication operation is still running.',
                               next_action='Wait for that operation to finish, then retry the same call.')
    source = isinstance(error, WebSourceError)
    message = str(error) if source else 'The Web platform could not complete this operation.'
    if root is not None:
        message = message.replace(str(root), '<installation>')
    normalized = message.replace('\\', '/')
    match = (re.search(r'custom_web/([^\n:(]+)[:(](\d+)(?:[, :](\d+))?', normalized)
             or re.search(r'(src/[^\n:(]+)[:(](\d+)(?:[, :](\d+))?', normalized))
    return {'status': 'error', 'code': error.code if source else 'web_platform_failure',
            'category': 'source' if source else 'platform', 'operation': operation,
            'error': message[-4000:], 'file': error.file if source and error.file else match[1] if match else None,
            'line': error.line if source and error.line else int(match[2]) if match else None,
            'column': int(match[3]) if match and match[3] else None,
            'next_action': error.next_action if source else
                'Tell the administrator to contact the product developer and provide diagnostic_id. '
                'Do not inspect or repair platform/compiler sources.',
            'diagnostic_id': reference, 'changed': None if operation == 'file.write' and not source else False,
            'publication_changed': False}
