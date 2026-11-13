"""Mandatory pre-input isolation check inside the restricted Python process."""
import json
import os
from pathlib import Path
import re


def verify():
    expected = json.loads(Path('/isolation.json').read_text())
    status = Path('/proc/self/status').read_text()
    valid = (os.getuid() != 0 and re.search(r'^CapEff:\s+0+$', status, re.M)
             and re.search(r'^NoNewPrivs:\s+1$', status, re.M))
    for name in ('net', 'ipc'):
        valid = valid and os.readlink('/proc/self/ns/' + name) != expected[name]
    for name in ('/proc/1/status', '/proc/sys', '/run/dbus/system_bus_socket',
                 '/run/systemd/private', '/run/user'):
        valid = valid and not os.path.exists(name)
    mounts = Path('/proc/self/mountinfo').read_text().splitlines()
    valid = valid and any(' /proc ' in line and re.search(r'hidepid=(2|invisible)', line)
                          and 'subset=pid' in line for line in mounts)
    valid = valid and all(Path(path).stat().st_dev == Path('/work').stat().st_dev
                          for path in ('/tmp', '/var/tmp', '/dev/shm'))
    valid = valid and not os.access('/sys', os.R_OK)
    if not valid:
        raise RuntimeError('Extension check isolation is unavailable; user code was not loaded')
