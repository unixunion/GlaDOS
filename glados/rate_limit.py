from datetime import datetime, timedelta
from functools import wraps


def rate_limited(seconds: int):
    """
    Decorator to limit a function to be callable only once every `seconds`.
    """

    def decorator(func):
        last_called = [None]  # Use a list to maintain state across calls

        @wraps(func)
        def wrapped(*args, **kwargs):
            now = datetime.now()
            if last_called[0] is None or now - last_called[0] > timedelta(seconds=seconds):
                last_called[0] = now
                return func(*args, **kwargs)
            else:
                remaining_time = (last_called[0] + timedelta(seconds=seconds) - now).total_seconds()
                return {
                    "status": "rate_limited",
                    "message": f"This function is rate-limited. Try again in {int(remaining_time)} seconds. "
                               "Use previous context if available, otherwise respond with '(silence)'."
                }

        return wrapped

    return decorator
