"""One-off deploy: upload local analytics/ai_analyzer.py to the server.

Verifies the uploaded checksum, restarts reportinsta and shows the service
state + last log lines. Password comes from SERVER_SSH_PASSWORD (never stored).
"""
import hashlib
import os
import time

import paramiko

PASSWORD = os.environ["SERVER_SSH_PASSWORD"]
LOCAL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "analytics", "ai_analyzer.py")
REMOTE = "/opt/reportinsta/analytics/ai_analyzer.py"


def md5(path):
    with open(path, "rb") as fh:
        return hashlib.md5(fh.read()).hexdigest()


def main():
    local_hash = md5(LOCAL)
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect("49.13.20.118", username="root", password=PASSWORD, timeout=30)
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
