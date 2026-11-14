"""Verify the running bot's parsed whitelist (read-only on server)."""
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from audit_snapshot.ssh import connect

CMD = "cd /opt/reportinsta && .venv/bin/python -c \"from config import settings; print(sorted(settings.ALLOWED_TELEGRAM_USERNAMES))\""

client = connect()
try:
    _, out, err = client.exec_command(CMD)
    print("parsed whitelist:", out.read().decode("utf-8", "replace").strip())
    e = err.read().decode("utf-8", "replace").strip()
    if e:
        print("stderr:", e)
finally:
    client.close()
