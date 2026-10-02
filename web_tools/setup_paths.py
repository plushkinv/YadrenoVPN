"""Stable ownership names shared by setup and autonomous protected backups."""
import uuid


def owned_paths(instance_id):
    if str(uuid.UUID(instance_id)) != instance_id:
        raise ValueError('invalid installation identity')
    name = 'yadreno-web-' + instance_id
    return {'name': name, 'nginx': '/etc/nginx/conf.d/' + name + '.conf',
            'acme': '/var/lib/' + name + '/acme', 'certbot': '/etc/letsencrypt/' + name,
            'renew_service': '/etc/systemd/system/' + name + '-renew.service',
            'renew_timer': '/etc/systemd/system/' + name + '-renew.timer'}


def marker(instance_id):
    return '# Yadreno web installation ' + instance_id + '\n'
