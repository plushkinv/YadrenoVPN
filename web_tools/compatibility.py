"""Declarative release compatibility; never import target or custom Python code."""
from __future__ import annotations

import json
import re
from pathlib import Path


def bounds(value, version, description):
    if (not isinstance(value, dict) or set(value) != {'min', 'max'}
            or any(type(item) is not int for item in value.values())
            or not 1 <= value['min'] <= version <= value['max']):
        raise ValueError('incompatible ' + description + ' range')
    return value


def release_capabilities(value):
    names = {'format_version', 'core_api', 'frontend_api', 'environment_contract', 'module_api', 'package_formats'}
    if (not isinstance(value, dict) or set(value) != names or value['format_version'] != 1
            or any(type(value[name]) is not int or value[name] < 1 for name in names - {'package_formats'})
            or not isinstance(value['package_formats'], list) or not value['package_formats']
            or any(type(item) is not int or item < 1 for item in value['package_formats'])
            or len(value['package_formats']) != len(set(value['package_formats']))):
        raise ValueError('invalid release UI compatibility declaration')
    return value


def current_capabilities():
    return release_capabilities(json.loads(Path(__file__).with_name('compatibility.json').read_bytes()))


def check_requirements(value, *, api_version=1, frontend_version=1, environment_version=1, module_api_version=1):
    if not isinstance(value, dict) or set(value) != {'frontend_api', 'modules'}:
        raise ValueError('invalid UI requirements')
    bounds(value['frontend_api'], frontend_version, 'frontend API')
    if not isinstance(value['modules'], list) or len(value['modules']) > 200:
        raise ValueError('invalid UI module requirements')
    used = set()
    for module in value['modules']:
        if (not isinstance(module, dict) or set(module) != {'id', 'version', 'api_version', 'core_api', 'frontend_api', 'environment_contract'}
                or not isinstance(module['id'], str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', module['id'])
                or module['id'] in used or not isinstance(module['version'], str)
                or not re.fullmatch(r'\d+\.\d+\.\d+', module['version'])
                or type(module['api_version']) is not int or module['api_version'] != module_api_version
                or type(module['environment_contract']) is not int or module['environment_contract'] != environment_version):
            raise ValueError('incompatible/invalid UI module requirements')
        used.add(module['id'])
        bounds(module['core_api'], api_version, 'module core API')
        bounds(module['frontend_api'], frontend_version, 'module frontend API')
    return value


def verification_versions(capabilities):
    value = release_capabilities(capabilities)
    return {'api_version': value['core_api'], 'frontend_version': value['frontend_api'],
            'environment_version': value['environment_contract'], 'module_api_version': value['module_api'],
            'supported_formats': tuple(value['package_formats'])}
