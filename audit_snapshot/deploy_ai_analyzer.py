"""One-off deploy: upload local analytics/ai_analyzer.py to the server.

Verifies the uploaded checksum, restarts reportinsta and shows the service
state + last log lines. Auth uses the SSH key (see audit_snapshot/ssh.py);
SERVER_SSH_PASSWORD remains as a fallback and is never stored.
"""
import hashlib
import os
import sys
import time

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from audit_snapshot.ssh import connect

LOCAL = os.path.join(_PROJECT_ROOT, "analytics", "ai_analyzer.py")
REMOTE = "/opt/reportinsta/analytics/ai_analyzer.py"


def md5(path):
    with open(path, "rb") as fh:
        return hashlib.md5(fh.read()).hexdigest()


def main():
    local_hash = md5(LOCAL)
    client = connect()
    try:
        sftp = client.open_sftp()
        sftp.put(LOCAL, REMOTE)
        sftp.close()
        _, out, _ = client.exec_command(f"md5sum {REMOTE}")
        remote_hash = out.read().decode().split()[0]
        print(f"local  md5: {local_hash}")
        print(f"remote md5: {remote_hash}")
        print("upload:", "OK" if local_hash == remote_hash else "MISMATCH!")

        client.exec_command("systemctl restart reportinsta")
        time.sleep(5)
        _, out, _ = client.exec_command(
            "systemctl is-active reportinsta && tail -5 /opt/reportinsta/logs/bot.log")
        print(out.read().decode("utf-8", "replace"))
    finally:
        client.close()


if __name__ == "__main__":
    main()
