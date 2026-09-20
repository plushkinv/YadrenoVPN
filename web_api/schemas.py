"""One closed transport contract for validation and OpenAPI 3.1 discovery."""
from __future__ import annotations

import json
import re

from core.results import CoreError

S, B, I, N = {'type': 'string'}, {'type': 'boolean'}, {'type': 'integer'}, {'type': 'number'}


def obj(properties=None, required=None):
    properties = properties or {}
    return {'type': 'object', 'properties': properties, 'additionalProperties': False,
            'required': list(properties) if required is None else required}


def array(items):
    return {'type': 'array', 'items': items}


def nullable(schema):
    return {'anyOf': [schema, {'type': 'null'}]}


def ref(name):
    return {'$ref': '#/components/schemas/' + name}


def text_bound(size):
    return {'type': 'string', 'maxLength': size}


ID = {'type': 'integer', 'minimum': 1, 'maximum': 2**52 - 1}
NS, NI = nullable(S), nullable(I)
METHOD = obj({'id': S, 'presentation': S})
PAGING = {'limit': I, 'offset': I}
COMPOSITION = obj({'ok': B, 'status': S, 'binding_id': NI, 'applied': B, 'already_applied': B,
                   'error_code': NS, 'host_count': I, 'host_key_id': I, 'operation_id': S}, ['ok', 'status'])
PAYMENT_FIELDS = {'order_id': S, 'purpose': S, 'status': S, 'payment_type': NS, 'base_currency': S,
                  'nominal_amount_minor': I, 'payable_amount_minor': I, 'balance_deduct_minor': I,
                  'vpn_key_id': NI, 'period_days': NI, 'created_at': NS, 'paid_at': NS}
SCHEMAS = {
    'Error': obj({'code': S, 'details': {'type': 'object', 'additionalProperties': True},
                  'retryable': B, 'operation_id': S}, ['code', 'details', 'retryable']),
    'AuthSettings': obj({'phone_format': S, 'sms_available': B, 'sms_registration_required': B,
                         'password_recovery_available': B, 'unverified_phone_warning_required': B}),
    'Session': obj({'account_id': I, 'telegram_id': NI, 'source': S, 'expires_at': I, 'csrf': S},
                   ['account_id', 'telegram_id', 'source', 'expires_at']),
    'Bootstrap': obj({'api_version': I, 'auth': ref('AuthSettings'), 'features': obj({name: B for name in
        ('subscriptions', 'trial', 'promotions', 'referrals', 'balance', 'subscription_import', 'support_chat')})}),
    'Profile': obj({'account_id': I, 'telegram_id': NI, 'username': NS, 'first_name': NS, 'last_name': NS,
        'created_at': NS, 'credentials': obj({'present': B, 'phone': NS, 'phone_verified': B})}),
    'Catalog': obj({'purpose': S, 'subscription_id': NI,
        'groups': array(obj({'id': I, 'name': S})),
        'tariffs': array(obj({'id': I, 'name': S, 'group_id': I, 'duration_days': I, 'traffic_limit_gb': N,
            'max_ips': I, 'base_currency': S, 'nominal_amount_minor': I, 'payable_amount_minor': I,
            'payment_methods': array(METHOD), 'available': B, 'reason': NS}))}),
    'TrialOffer': obj({'eligible': B, 'reason': NS, 'scope': S, 'offer': nullable(obj({
        'offer_id': I, 'tariff_id': NI, 'is_primary': I, 'is_enabled': I, 'created_at': NS, 'updated_at': NS,
        'resolved_tariff_id': NI, 'tariff_name': NS, 'duration_days': NI, 'traffic_limit_gb': nullable(N),
        'max_ips': NI, 'tariff_is_active': NI, 'system_type': NS, 'group_id': NI, 'group_name': NS}))}),
    'TrialResult': obj({'ok': B, 'reason': NS, 'operation_id': S, 'key_id': I, 'order_id': S, 'state': S,
        'server_ids': array(I), 'composition': nullable(COMPOSITION), 'scope': NS}, ['ok', 'operation_id']),
    'KeyResult': obj({'operation_id': S, 'key_id': I, 'state': S, 'deleted': B, 'reason': S, 'error': S}, ['operation_id']),
    'KeyOperation': obj({'operation_id': S, 'kind': S, 'key_id': I, 'state': S,
        'result': nullable(obj({'key_id': I, 'state': S, 'deleted': B, 'reason': S, 'error': S}, []))}),
    'Subscription': obj({'id': I, 'name': NS, 'tariff_id': NI, 'tariff_name': NS, 'tariff_known': B,
        'server_id': NI, 'server_name': NS, 'expires_at': NS, 'created_at': NS, 'state': S, 'access_status': S,
        'imported': B, 'traffic': obj({'used_bytes': NI, 'limit_bytes': NI, 'known': B, 'updated_at': NS, 'source': S}),
        'devices_available': B, 'actions': obj({name: obj({'allowed': B, 'reason': NS}) for name in
            ('key.rename.start', 'key.renew.start', 'key.delete', 'key.configure.start', 'key.replace.start')}),
        'pending_operations': array(obj({'id': S, 'kind': S, 'created_at': I})),
        'servers': array(obj({'id': I, 'name': S}))}),
    'KeyHistoryItem': obj({'id': S, 'kind': S, 'action': S, 'source': NS, 'status': S,
        'payable_amount_minor': NI, 'currency': NS, 'delta_days': NI, 'expires_before': NS, 'expires_after': NS, 'created_at': NS}),
    'Device': obj({'id': S, 'first_seen': NI, 'last_seen': NI, 'user_agent': S,
                   'device_os': S, 'os_version': S, 'device_model': S}),
    'Host': obj({'id': I, 'custom_name': NS, 'tariff_name': NS, 'server_name': NS, 'expires_at': NS}),
    'Import': obj({'id': NI, 'state': S, 'key_ids': array(I)}),
    'Quote': obj({'quote_id': S, 'expires_at': I, 'version': I, 'purpose': S, 'tariff_id': NI, 'key_id': NI,
        'payment_type': S, 'base_currency': S, 'nominal_amount_minor': I, 'payable_amount_minor': I,
        'charge_minor': I, 'charge_currency': S, 'balance_deduct_minor': I, 'duration_days': NI,
        'traffic_limit_gb': nullable(N), 'max_ips': NI}),
    'Order': obj({'order_id': S, 'operation_id': NS, 'purpose': S, 'status': S, 'fulfillment_status': S,
        'provider_confirmed': B, 'provider_status': NS, 'payment_type': NS, 'base_currency': S,
        'nominal_amount_minor': I, 'payable_amount_minor': I, 'balance_deduct_minor': I,
        'charge_amount': NS, 'charge_currency': NS, 'subscription_id': NI, 'access_status': NS,
        'payment_url': NS, 'presentation': S}, ['order_id', 'status', 'purpose', 'fulfillment_status', 'provider_confirmed',
            'base_currency', 'nominal_amount_minor', 'payable_amount_minor', 'balance_deduct_minor', 'access_status']),
    'Balance': obj({'amount_minor': I, 'currency': S, 'spending_enabled': B, **PAGING,
        'history': array(obj({'id': I, 'operation_type': S, 'delta_minor': I, 'currency': S,
            'balance_before': I, 'balance_after': I, 'reason': NS, 'created_at': NS}))}),
    'Promo': obj({'ok': B, 'reason': NS, 'operation_id': S,
        'promo': obj({'code': S, 'type': S, 'discount_percent': I, 'expires_at': NS})}, ['ok']),
    'Referrals': obj({'enabled': B, 'code': S, 'reward_type': S, 'site_url': NS, 'telegram_url': NS,
        'levels': array(obj({'level_number': I, 'percent': I, 'enabled': I})),
        'statistics': array(obj({'level': I, 'paying_count': I, 'total_reward_cents': I, 'total_reward_minor': I,
            'reward_currency': NS, 'total_reward_days': I, 'count': I})), 'coefficient': N, 'conditions_html': S}, ['enabled']),
    'Help': obj({'content_html': S, 'links': array(obj({'label': NS, 'url': S}))}),
}


def page(item):
    return obj({'items': array(item), **PAGING})


# method, path, input, output, anonymous, durable-idempotency-header
ROUTES = []


def route(method, path, output, inputs=None, *, anonymous=False, idempotent=False, paging=False, query=None):
    ROUTES.append({'method': method, 'path': '/api/v1/' + path, 'input': inputs, 'output': output,
        'anonymous': anonymous, 'idempotent': idempotent, 'query': query or ({
            'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100},
            'offset': {'type': 'integer', 'minimum': 0, 'maximum': 1000000}} if paging else {})})


route('GET', 'bootstrap', ref('Bootstrap'), anonymous=True)
route('GET', 'auth/settings', ref('AuthSettings'), anonymous=True)
route('POST', 'auth/register', ref('Session'), obj({'phone': text_bound(64), 'password': text_bound(128),
    'proof': nullable(text_bound(128)), 'referral_code': nullable(text_bound(128))}, ['phone', 'password']), anonymous=True)
route('POST', 'auth/login', ref('Session'), obj({'phone': text_bound(64), 'password': text_bound(128)}), anonymous=True)
route('POST', 'auth/telegram', ref('Session'), obj({'init_data': text_bound(8192)}), anonymous=True)
route('POST', 'auth/logout', obj({'logged_out': B}), obj())
route('GET', 'auth/session', ref('Session'))
route('POST', 'account/credentials', ref('Session'), obj({'phone': text_bound(64), 'password': text_bound(128),
    'current_password': nullable(text_bound(128)), 'proof': nullable(text_bound(128))}, ['phone', 'password']))
route('POST', 'auth/sms/request', obj({'challenge_id': S, 'expires_at': I, 'send_state': S}),
    obj({'phone': text_bound(64), 'purpose': {'type': 'string', 'enum': ['register', 'credentials', 'reset']}}), anonymous=True)
route('POST', 'auth/sms/verify', obj({'proof': S}), obj({'challenge_id': text_bound(64), 'code': text_bound(6)}), anonymous=True)
route('POST', 'auth/password/reset', obj({'password_reset': B}),
    obj({'phone': text_bound(64), 'password': text_bound(128), 'proof': text_bound(128)}), anonymous=True)
route('POST', 'account/telegram/link', obj({'token': S, 'telegram_url': S, 'expires_at': I}), obj())
route('POST', 'account/telegram/link/status', obj({'state': S, 'expires_at': I, 'telegram_id': NI,
    'username': NS, 'first_name': NS, 'last_name': NS}), obj({'token': text_bound(64)}))
route('POST', 'account/telegram/link/finish', obj({'account_id': I, 'telegram_id': I, 'pending_renames': I}),
    obj({'token': text_bound(64), 'telegram_id': ID}))
route('GET', 'me', ref('Profile'))
route('GET', 'catalog', ref('Catalog'), query={'subscription_id': ID})
route('GET', 'trial-offers', obj({'offers': array(ref('TrialOffer'))}))
route('POST', 'trials/{id}/activate', ref('TrialResult'), obj(), idempotent=True)
route('GET', 'subscriptions', page(ref('Subscription')), paging=True)
route('GET', 'subscriptions/{id}', ref('Subscription'))
route('GET', 'subscriptions/{id}/history', page(ref('KeyHistoryItem')), paging=True)
route('GET', 'subscriptions/{id}/access', obj({'subscription_id': I, 'url': S}))
route('GET', 'subscriptions/{id}/devices', obj({'devices': array(ref('Device'))}))
route('GET', 'subscriptions/{id}/host-candidates', obj({'hosts': array(ref('Host'))}))
for action, fields in [('configure', {'server_id': ID}), ('replace', {'server_id': ID}),
                       ('rename', {'name': {'type': 'string', 'minLength': 1, 'maxLength': 30}}), ('delete', {})]:
    route('POST', 'subscriptions/{id}/' + action, ref('KeyResult'), obj(fields), idempotent=True)
route('POST', 'subscriptions/{id}/devices/{device}/delete', ref('KeyResult'), obj(), idempotent=True)
route('POST', 'subscriptions/{id}/host', COMPOSITION, obj({'host_id': ID}), idempotent=True)
route('GET', 'key-operations/{id}', ref('KeyOperation'))
route('POST', 'subscription-imports', ref('Import'), obj({'url': text_bound(2048)}))
route('GET', 'subscription-imports/{id}', ref('Import'))
route('POST', 'quotes', ref('Quote'), obj({'purpose': {'type': 'string', 'enum': ['key_purchase', 'key_renewal', 'balance_topup']},
    'payment_type': text_bound(128), 'tariff_id': nullable(ID), 'key_id': nullable(ID),
    'nominal_amount_minor': nullable(ID), 'use_balance': B}, ['purpose', 'payment_type']))
route('POST', 'orders', ref('Order'), obj({'quote_id': text_bound(128)}), idempotent=True)
route('GET', 'orders/{id}', ref('Order'))
route('POST', 'orders/{id}/method', ref('Order'), obj({'payment_type': text_bound(128)}))
for action in ('check', 'cancel'):
    route('POST', 'orders/{id}/' + action, ref('Order'), obj())
route('GET', 'balance', ref('Balance'), paging=True)
route('POST', 'balance/topups', ref('Order'), obj({'quote_id': text_bound(128)}), idempotent=True)
route('GET', 'payments', page(obj(PAYMENT_FIELDS)), paging=True)
for action in ('check', 'activate', 'clear'):
    route('POST', 'promotions/' + action, ref('Promo'), obj({'code': text_bound(128)} if action != 'clear' else {}),
          idempotent=action != 'check')
route('GET', 'referrals', ref('Referrals'))
route('POST', 'referrals/attribute', obj({'attributed': B}), obj({'code': text_bound(128)}))
route('GET', 'help', ref('Help'))
route('POST', 'modules/{module_id}/operations/{name}', obj({'operation_id': S, 'result': {}}),
    {'type': 'object', 'additionalProperties': True}, idempotent=True)
route('GET', 'modules/{module_id}/support/attachments/{message_id}', {'type': 'string', 'format': 'binary'})
for entry in ROUTES:
    entry['pattern'] = re.compile(re.sub(r'\{[^}]+\}', '[^/]+', entry['path']) + r'\Z')


def contract(request):
    method = 'GET' if request.method == 'HEAD' else request.method
    return next((item for item in ROUTES if item['method'] == method and item['pattern'].fullmatch(request.path)), None)


def validate(value, schema, path='$'):
    """Validate the subset emitted above; custom operation schemas also validate in core."""
    if '$ref' in schema:
        return validate(value, SCHEMAS[schema['$ref'].rsplit('/', 1)[1]], path)
    if 'anyOf' in schema:
        for choice in schema['anyOf']:
            try:
                validate(value, choice, path)
                return
            except ValueError:
                pass
        raise ValueError(path)
    kind = schema.get('type')
    types = {'string': str, 'boolean': bool, 'integer': int, 'object': dict, 'array': list, 'null': type(None)}
    if kind == 'number':
        if type(value) not in (int, float) or not -2**53 <= value <= 2**53:
            raise ValueError(path)
    elif kind and type(value) is not types[kind]:
        raise ValueError(path)
    if 'enum' in schema and value not in schema['enum']:
        raise ValueError(path)
    if kind == 'object':
        properties = schema.get('properties', {})
        if set(schema.get('required', [])) - set(value):
            raise ValueError(path)
        for key, child in value.items():
            if key in properties:
                validate(child, properties[key], path + '.' + key)
            elif schema.get('additionalProperties') is False:
                raise ValueError(path + '.' + key)
    elif kind == 'array':
        for child in value:
            validate(child, schema['items'], path + '[]')
    elif kind == 'string':
        if not schema.get('minLength', 0) <= len(value) <= schema.get('maxLength', float('inf')):
            raise ValueError(path)
    elif kind in ('number', 'integer'):
        if not schema.get('minimum', -2**53) <= value <= schema.get('maximum', 2**53):
            raise ValueError(path)


async def validate_request(request):
    entry = contract(request)
    if not entry:
        return
    try:
        if set(request.query) - set(entry['query']) or any(len(request.query.getall(key)) != 1 for key in request.query):
            raise ValueError('query')
        for key, value in request.query.items():
            validate(int(value), entry['query'][key], key)
        if entry['idempotent'] and not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', request.headers.get('Idempotency-Key', '')):
            raise ValueError('Idempotency-Key')
        if entry['input'] is not None:
            validate(await request.json(), entry['input'])
    except (ValueError, TypeError, OverflowError) as error:
        raise CoreError('invalid_request', details={'field': str(error)[:128]}) from None


def validate_response(request, response):
    entry = contract(request)
    if entry and response.status < 400 and response.content_type == 'application/json':
        try:
            validate(json.loads(response.body), entry['output'])
        except (ValueError, TypeError, KeyError):
            raise CoreError('invalid_response', retryable=True) from None


def openapi_document():
    paths = {}
    for entry in ROUTES:
        parameters = [{'name': name, 'in': 'path', 'required': True, 'schema': text_bound(128)}
                      for name in re.findall(r'\{([^}]+)\}', entry['path'])]
        parameters += [{'name': name, 'in': 'query', 'required': False, 'schema': schema} for name, schema in entry['query'].items()]
        if entry['method'] == 'POST':
            parameters += [{'name': 'Origin', 'in': 'header', 'required': True, 'schema': S},
                           {'name': 'X-CSRF-Token', 'in': 'header', 'required': not entry['anonymous'], 'schema': S,
                            'description': 'Required whenever a valid session cookie is sent, including anonymous auth endpoints.'}]
        if entry['idempotent']:
            parameters.append({'name': 'Idempotency-Key', 'in': 'header', 'required': True, 'schema': text_bound(128)})
        binary = entry['output'].get('format') == 'binary'
        operation = {'operationId': entry['method'].lower() + '_' + re.sub(r'[^a-zA-Z0-9]+', '_', entry['path']).strip('_'),
            'security': [] if entry['anonymous'] else [{'SessionCookie': []}], 'parameters': parameters,
            'responses': {'200': {'description': 'Successful own-account result. Sensitive responses are never cached.',
                'content': {'application/octet-stream' if binary else 'application/json': {'schema': entry['output']}}},
                **{str(code): {'description': description, 'content': {'application/json': {'schema': ref('Error')}}}
                   for code, description in ((400, 'Domain rejection'), (401, 'Authentication required'), (403, 'Origin, CSRF or access denied'),
                       (404, 'Resource unavailable to this account'), (405, 'Method not allowed'),
                       (409, 'Conflict or unavailable action'), (413, 'Request body too large'), (422, 'Invalid input'),
                       (429, 'Rate limited'), (500, 'Internal failure'), (503, 'Retryable operation or unavailable service'))}}}
        if entry['input'] is not None:
            operation['requestBody'] = {'required': True, 'content': {'application/json': {'schema': entry['input']}}}
        paths.setdefault(entry['path'], {})[entry['method'].lower()] = operation
    from core.extensions.registry import POLICIES
    for (kind, module_id, name), policy in POLICIES.items():
        if kind == 'user_operation':
            # Public declarations only: no handlers, settings, policy inputs or secrets.
            paths[f'/api/v1/modules/{module_id}/operations/{name}'] = {'post': {
                **paths['/api/v1/modules/{module_id}/operations/{name}']['post'],
                'operationId': f'module_{module_id}_{name}',
                'parameters': [value for value in paths['/api/v1/modules/{module_id}/operations/{name}']['post']['parameters'] if value['in'] != 'path'],
                'requestBody': {'required': True, 'content': {'application/json': {'schema': policy['input_schema']}}},
                'responses': {'200': {'description': 'Schema-validated module result', 'content': {'application/json': {
                    'schema': obj({'operation_id': S, 'result': policy['result_schema']})}}}}}}
    return {'openapi': '3.1.0', 'info': {'title': 'YadrenoVPN Account API', 'version': '1.0.0'},
        'servers': [{'url': '/'}], 'paths': paths, 'components': {'schemas': SCHEMAS,
        'securitySchemes': {'SessionCookie': {'type': 'apiKey', 'in': 'cookie', 'name': '__Host-yadreno_session'}}}}


async def openapi(request):
    from aiohttp import web
    return web.json_response(openapi_document())
