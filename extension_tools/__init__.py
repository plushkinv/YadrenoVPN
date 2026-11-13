"""Optional isolated checks for installed Python extensions."""

VERSION = '1'


def __getattr__(name):
    if name in {'ExtensionTestCase', 'EnvironmentUnavailable'}:
        from . import testing
        return getattr(testing, name)
    raise AttributeError(name)
