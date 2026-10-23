"""Verify the running bot's parsed whitelist (read-only on server)."""
import os

import paramiko

PASSWORD = os.environ["SERVER_SSH_PASSWORD"]
CMD = "cd /opt/reportinsta && .venv/bin/python -c \"from config import settings; print(sorted(settings.ALLOWED_TELEGRAM_USERNAMES))\""

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect("49.13.20.118", username="root", password=PASSWORD, timeout=30)
try:
    _, out, err = client.exec_command(CMD)
    print("parsed whitelist:", out.read().decode("utf-8", "replace").strip())
    e = err.read().decode("utf-8", "replace").strip()
    if e:
        print("stderr:", e)
finally:
    client.close()
