import asyncio
import functools

class MockApplication():
    def get_sequence(self):
        return 123


def async(f):
    @functools.wraps(f)
    def inner(*args, **kwargs):
        loop = asyncio.get_event_loop()
        loop.run_until_complete(f(*args, **kwargs))

    return inner
