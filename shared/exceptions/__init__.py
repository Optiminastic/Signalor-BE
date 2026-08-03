"""The single error envelope: {detail, code, status_code}.

Wired as DRF's EXCEPTION_HANDLER in config/settings/base.py.
"""

from .handlers import *  # noqa: F401,F403
from .handlers import custom_exception_handler  # noqa: F401
