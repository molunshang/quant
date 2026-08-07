"""Test isolation: redirect the default agent DB to a session-scoped shadow DB.

Must set QUANT_AGENT_DB at IMPORT time (not in a fixture): api.main and the
store modules construct their default stores at import time, before any pytest
fixture runs. Setting it here means even a test that forgets to inject its own
stores can never touch the real data/agent.db.
"""
import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="quant-test-db-")
os.environ["QUANT_AGENT_DB"] = os.path.join(_TMP, "agent.db")
