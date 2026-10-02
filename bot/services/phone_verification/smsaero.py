"""SMS Aero SMS delivery and operator-owned Mobile ID confirmation."""
import aiohttp

from .http import InvalidProviderCode, ProviderResult, failed_response, post, request_id

BASE_URL = 'https://gate.smsaero.ru/v2/'


async def _request(configuration: dict, operation: str, payload: dict):
    return await post(BASE_URL + operation, payload=payload,
                      auth=aiohttp.BasicAuth(configuration['smsaero_email'], configuration['smsaero_api_key']))


def _mobile_result(status, result, *, phone, identifier=None):
    failure = failed_response(status, result)
    if failure is not None:
        return failure
    data = result.get('data')
    if not isinstance(data, dict):
        return ProviderResult()
    found_id = request_id(data.get('id'))
    if (found_id is None or identifier is not None and found_id != identifier
            or str(data.get('number')) != phone.lstrip('+') or type(data.get('status')) is not int):
        return ProviderResult()
    state = {0: 'waiting', 8: 'waiting', 3: 'code_required', 1: 'confirmed',
             2: 'failed', 16: 'failed'}.get(data['status'])
    if state is None:
        return ProviderResult()
    return ProviderResult('failed' if state == 'failed' else 'sent', state, found_id)


async def send_mobile(*, configuration: dict, phone: str, callback_url: str, **_) -> ProviderResult:
    status, result = await _request(configuration, 'mobile-id/send',
        {'number': phone.lstrip('+'), 'sign': configuration['smsaero_mobile_sign'], 'callbackUrl': callback_url})
    return _mobile_result(status, result, phone=phone)


async def mobile_status(*, configuration: dict, phone: str, identifier: str) -> ProviderResult:
    status, result = await _request(configuration, 'mobile-id/status', {'id': int(identifier)})
    return _mobile_result(status, result, phone=phone, identifier=identifier)


async def verify_mobile(*, configuration: dict, phone: str, identifier: str, code: str) -> ProviderResult:
    status, result = await _request(configuration, 'mobile-id/verify',
        {'id': int(identifier), 'sign': configuration['smsaero_mobile_sign'], 'code': code})
    if status == 400:
        raise InvalidProviderCode()
    return _mobile_result(status, result, phone=phone, identifier=identifier)


async def send_sms(*, configuration: dict, phone: str, code: str, title: str, **_) -> ProviderResult:
    status, result = await _request(configuration, 'sms/send',
        {'number': phone.lstrip('+'), 'sign': configuration['smsaero_sms_sign'],
         'text': f'{title}: код подтверждения {code}. Никому не сообщайте код.'})
    failure = failed_response(status, result)
    if failure is not None:
        return failure
    data = result.get('data')
    rows = data if isinstance(data, list) else [data]
    matches = [row for row in rows if isinstance(row, dict) and str(row.get('number')) == phone.lstrip('+')]
    if len(matches) != 1:
        return ProviderResult()
    item = matches[0]
    identifier = request_id(item.get('id'))
    if type(item.get('status')) is not int or identifier is None:
        return ProviderResult()
    if item['status'] in (2, 6):
        return ProviderResult('failed', 'failed', identifier)
    if item['status'] not in (0, 1, 3, 4, 8):
        return ProviderResult()
    return ProviderResult('sent', 'code_required', identifier)
