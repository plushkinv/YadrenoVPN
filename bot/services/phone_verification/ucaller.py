"""Ucaller flash calls: the caller's final four digits carry the local code."""
from .http import ProviderResult, failed_response, post, request_id


async def send(*, configuration: dict, phone: str, code: str, unique: str, **_) -> ProviderResult:
    status, result = await post(
        'https://api.ucaller.ru/v1.0/initCall',
        headers={'Authorization': f"Bearer {configuration['ucaller_secret_key']}.{configuration['ucaller_service_id']}"},
        payload={'phone': int(phone.lstrip('+')), 'code': code, 'unique': unique, 'voice': False, 'mix': False},
    )
    failure = failed_response(status, result, 'status')
    if failure is not None:
        return failure
    identifier = request_id(result.get('ucaller_id'))
    if identifier is None or result.get('code') != code:
        return ProviderResult()
    return ProviderResult('sent', 'code_required', identifier)
