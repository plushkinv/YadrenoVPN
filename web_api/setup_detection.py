"""Recognize an ordinary prepared local Nginx route without adopting its files."""
from __future__ import annotations

from dataclasses import dataclass
import fnmatch
import ipaddress
from pathlib import PurePosixPath
import re
import shlex
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Directive:
    name: str
    args: tuple[str, ...]
    children: tuple | None
    source: str


def _parse(text, source):
    tokens = re.findall(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|#[^\n]*|[^\s{};#]+|[{};]', text)
    stack, current, words = [], [], []
    for token in tokens:
        if token.startswith('#'):
            continue
        if token == '{':
            if not words:
                raise ValueError('unnamed block')
            stack.append((current, words))
            current, words = [], []
        elif token == '}':
            if words or not stack:
                raise ValueError('unbalanced block')
            children = tuple(current)
            current, words = stack.pop()
            current.append(Directive(words[0], tuple(words[1:]), children, source))
            words = []
        elif token == ';':
            if not words:
                raise ValueError('empty directive')
            current.append(Directive(words[0], tuple(words[1:]), None, source))
            words = []
        else:
            words.append(shlex.split(token)[0] if token.startswith(('"', "'")) else token)
    if stack or words:
        raise ValueError('incomplete configuration')
    return tuple(current)


def _configuration(dump):
    chunks = re.split(r'^# configuration file (.+):\s*$', dump, flags=re.M)
    if len(chunks) < 3:
        raise ValueError('missing configuration sources')
    files = {chunks[i]: _parse(chunks[i + 1], chunks[i]) for i in range(1, len(chunks), 2)}
    main = chunks[1]
    prefix = str(PurePosixPath(main).parent)

    def expand(nodes, seen):
        expanded = []
        for node in nodes:
            if node.name == 'include':
                if node.children is not None or len(node.args) != 1:
                    raise ValueError('unsupported include')
                pattern = node.args[0]
                if not pattern.startswith('/'):
                    pattern = prefix + '/' + pattern
                matches = sorted(name for name in files if fnmatch.fnmatchcase(name, pattern))
                if not matches and not any(char in pattern for char in '*?['):
                    raise ValueError('include absent from dump')
                for name in matches:
                    if name in seen:
                        raise ValueError('recursive include')
                    expanded.extend(expand(files[name], seen | {name}))
            else:
                children = tuple(expand(node.children, seen)) if node.children is not None else None
                expanded.append(Directive(node.name, node.args, children, node.source))
        return expanded

    nodes = expand(files[main], {main})
    http = [node for node in nodes if node.name == 'http' and node.children is not None]
    if len(http) != 1:
        raise ValueError('ambiguous http context')
    return http[0].children


def _values(nodes, name):
    return [node.args for node in nodes if node.name == name and node.children is None]


def _endpoint(value):
    parsed = urlsplit('http://' + value)
    if parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment or not parsed.port:
        raise ValueError('unsupported upstream')
    address = str(ipaddress.ip_address(parsed.hostname))
    if not ipaddress.ip_address(address).is_loopback:
        raise ValueError('nonlocal upstream')
    return address, parsed.port


def _backend(server, upstreams):
    # A direct root proxy plus optional routes to the same backend is sufficient.
    # Rewrites, static roots, nested routing and other complex layouts stay manual.
    if any(node.name in {'return', 'rewrite', 'if', 'try_files', 'root', 'alias'} for node in server.children):
        return None
    locations = [node for node in server.children if node.name == 'location']
    if not any(node.args in {('/',), ('^~', '/')} for node in locations):
        return None
    targets = set()
    for location in locations:
        if any(node.children is not None or node.name in {'return', 'rewrite', 'try_files', 'root', 'alias'}
               for node in location.children):
            return None
        values = _values(location.children, 'proxy_pass')
        if len(values) != 1 or len(values[0]) != 1:
            return None
        parsed = urlsplit(values[0][0])
        if (parsed.scheme != 'http' or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in {'', '/'} or '$' in values[0][0]
                or parsed.path and location.args not in {('/',), ('^~', '/')}):
            return None
        target = upstreams.get(parsed.netloc, parsed.netloc)
        targets.add(_endpoint(target))
    return next(iter(targets)) if len(targets) == 1 else None


def _listen_hosts(server, port):
    hosts = []
    for args in _values(server.children, 'listen'):
        if 'ssl' not in args or 'proxy_protocol' in args:
            continue
        endpoint = args[0]
        if endpoint.isdecimal():
            host, found_port = '0.0.0.0', int(endpoint)
        else:
            host, found_port = endpoint.rsplit(':', 1)
            host, found_port = host.strip('[]'), int(found_port)
            host = '0.0.0.0' if host == '*' else host
        if found_port == port:
            hosts.append(ipaddress.ip_address(host))
    return hosts


def prepared_local_nginx(system, options, paths, resolved, occupied, expected_port):
    """Return one observed loopback destination, or leave ordinary preflight in charge."""
    if not system.which('nginx') or not system.active('nginx'):
        return None
    listeners = [item for item in occupied if item['port'] == options.https_port
                 and 'nginx' in item['process'] and 'docker' not in item['process']]
    if not listeners:
        return None
    dump = system.run(['nginx', '-T'], check=False)
    if dump.returncode:
        return None
    try:
        http = _configuration(dump.stdout)
        upstreams = {}
        for node in http:
            if node.name == 'upstream' and len(node.args) == 1:
                servers = _values(node.children, 'server')
                if len(servers) == 1 and len(servers[0]) == 1:
                    upstreams[node.args[0]] = servers[0][0]
        candidates = []
        for server in http:
            if (server.name != 'server' or server.children is None
                    or not any(options.domain in names for names in _values(server.children, 'server_name'))):
                continue
            hosts = _listen_hosts(server, options.https_port)
            if hosts:
                candidates.append((server, hosts))
        if len(candidates) != 1:
            return None
        server, hosts = candidates[0]
        if server.source == paths['nginx']:
            return None  # Retain managed ownership, certificate renewal and proof.
        for value in resolved:
            address = ipaddress.ip_address(value)
            matches = lambda host: host.version == address.version and (host.is_unspecified or host == address)
            if not any(matches(host) for host in hosts) or not any(matches(ipaddress.ip_address(item['host'])) for item in listeners):
                return None
        backend = _backend(server, upstreams)
        if not backend or backend[0] != options.backend_bind or expected_port and backend[1] != expected_port:
            return None
        return {'port': backend[1], 'file': server.source}
    except (ValueError, IndexError, TypeError, RecursionError):
        # Never print a raw Nginx directive; it may contain credentials.
        return None
