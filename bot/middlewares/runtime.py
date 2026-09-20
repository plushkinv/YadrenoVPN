"""Fence dispatcher entry before registration or other user-side effects."""
from runtime.readiness import is_active


class RuntimeIngressMiddleware:
    async def __call__(self, handler, event, data):
        if not is_active():
            return None
        return await handler(event, data)
