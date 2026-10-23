"""Finish snapshot bookkeeping: remote cleanup, server .env download, env compare.

Security: secrets are saved only to audit_snapshot/.env (gitignored via '.env'
pattern) and never printed. Compare output is equality-only.
"""
import json
import os

import paramiko

HOST = "49.13.20.118"
USER = "root"
PASSWORD = os.environ["SERVER_SSH_PASSWORD"]

LOCAL_DIR = os.path.dirname(os.path.abspath(__file__))
SERVER_ENV_PATH = os.path.join(LOCAL_DIR, ".env")
PROJECT_ROOT = os.path.dirname(LOCAL_DIR)


def field(text, name):
    for line in text.splitlines():
        if line.startswith(name + "="):
            v = line.split("=", 1)[1].strip()
            if len(v) >= 2 and v[0] == "'" and v[-1] == "'":
                v = v[1:-1]
            return v
    return None


def main():
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASSWORD, timeout=30)
    sftp = client.open_sftp()
    try:
        # Proper shell cleanup of the server-side temp snapshot.
        _, _, stderr = client.exec_command("rm -f /tmp/reportinsta_audit_snap.db")
        stderr.read()
        print("remote temp snapshot removed")

        with sftp.file("/opt/reportinsta/.env", "r") as fh:
            server_env = fh.read().decode("utf-8", "replace")
        with open(SERVER_ENV_PATH, "w", encoding="utf-8") as fh:
            fh.write(server_env)
        print(f"server .env saved -> {SERVER_ENV_PATH} (gitignored)")

        with open(os.path.join(PROJECT_ROOT, ".env"), "r", encoding="utf-8") as fh:
            local_env = fh.read()

        print("=== ENV COMPARE (normalized, equality only) ===")
        for name in ("BOT_TOKEN", "OPENAI_API_KEY", "OPENAI_MODEL", "INSTAGRAM_ACCOUNTS",
                     "ADMIN_TELEGRAM_ID", "ALLOWED_TELEGRAM_USERNAMES", "DATABASE_URL",
                     "APP_TIMEZONE"):
            sv, lv = field(server_env, name), field(local_env, name)
            if sv is None and lv is None:
                status = "absent-both"
            elif sv == lv:
                status = "same"
            elif sv and lv:
                status = "DIFFERENT"
            else:
                status = "only-server" if sv else "only-local"
            print(f"{name}: {status}")

        # Account-level token compare without printing values.
        s_acc = json.loads(field(server_env, "INSTAGRAM_ACCOUNTS") or "[]")
        l_acc = json.loads(field(local_env, "INSTAGRAM_ACCOUNTS") or "[]")
        s_map = {a["name"]: a.get("access_token") for a in s_acc}
        l_map = {a["name"]: a.get("access_token") for a in l_acc}
        print("=== ACCOUNTS (server vs local) ===")
        for name in sorted(set(s_map) | set(l_map)):
            if name not in l_map:
                print(f"{name}: only-server")
            elif name not in s_map:
                print(f"{name}: only-local")
            else:
                print(f"{name}: token {'same' if s_map[name] == l_map[name] else 'DIFFERENT'}"
                      f", user_id {'same' if next(a['user_id'] for a in s_acc if a['name']==name) == next((a['user_id'] for a in l_acc if a['name']==name), None) else 'DIFFERENT'}")
    finally:
        sftp.close()
        client.close()


if __name__ == "__main__":
    main()
