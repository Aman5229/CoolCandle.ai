"""Kotak Neo adapter.

The SDK logs call parameters, including the MPIN, at INFO, and writes its own log file. These must
be set before it is first imported, and they override the environment on purpose.
"""

import os

os.environ["NEO_LOG_LEVEL"] = "ERROR"
os.environ["NEO_LOG_FILE_ENABLED"] = "false"
