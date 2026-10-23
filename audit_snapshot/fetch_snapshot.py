"""Fetch a consistent read-only snapshot of the production DB (read-only on server).

Security: the SSH password comes from the SERVER_SSH_PASSWORD env var and is
never written to disk. The server-side temp snapshot is deleted after download.

Actions on the server (all read-only except a temp copy in /tmp):
1. Inventory: pragma checks, table counts, data ranges, last sync runs.
2. VACUUM INTO /tmp/<snap>.db  -> consistent snapshot of the live WAL DB.
3. SFTP download of the snapshot + tail of the bot log.
4. rm /tmp/<snap>.db
"""
import json
import os
import sys

import paramiko

HOST = "49.13.20.118"
USER = "root"
PASSWORD = os.environ["SERVER_SSH_PASSWORD"]
REMOTE_DB = "/opt/reportinsta/data/instagram_bot.db"
REMOTE_ENV = "/opt/reportinsta/.env"
REMOTE_LOG = "/opt/reportinsta/logs/bot.log"
REMOTE_SNAP = "/tmp/reportinsta_audit_snap.db"

LOCAL_DIR = os.path.dirname(os.path.abspath(__file__))
LOCAL_SNAP = os.path.join(LOCAL_DIR, "server_snapshot.db")
LOCAL_LOG_TAIL = os.path.join(LOCAL_DIR, "bot_log_tail.log")

INVENTORY = r"""
import sqlite3, json
con = sqlite3.connect("file:/opt/reportinsta/data/instagram_bot.db?mode=ro", uri=True)
con.row_factory = sqlite3.Row
cur = con.cursor()
out = {}
out["quick_check"] = cur.execute("pragma quick_check").fetchone()[0]
out["fk_violations"] = len(cur.execute("pragma foreign_key_check").fetchall())
ts = [r[0] for r in cur.execute("select name from sqlite_master where type='table'")]
out["counts"] = {t: cur.execute("select count(*) from " + t).fetchone()[0] for t in ts}
out["accounts"] = [dict(r) for r in cur.execute(
    "select id, username, sync_status, last_sync_at, current_followers, current_followers_at"
    " from accounts order by id")]
out["daily"] = [dict(r) for r in cur.execute(
    "select account_id, min(date) as min_d, max(date) as max_d, count(*) as n"
    " from daily_stats group by account_id")]
out["posts"] = [dict(r) for r in cur.execute(
    "select account_id, min(timestamp) as min_t, max(timestamp) as max_t, count(*) as n"
    " from posts group by account_id")]
out["stories"] = dict(zip(("total", "active"), cur.execute(
    "select count(*), coalesce(sum(is_active),0) from stories").fetchone()))
out["period_snapshots"] = [dict(r) for r in cur.execute(
    "select account_id, min(period_start) as min_p, max(period_end) as max_p, count(*) as n"
    " from period_snapshots group by account_id")]
out["sync_runs"] = [dict(r) for r in cur.execute(
    "select account_id, sync_type, status, started_at, finished_at, is_partial, error"
    " from sync_runs order by id desc limit 10")]
print(json.dumps(out, default=str, ensure_ascii=False))
"""

SNAPSHOT = r"""
import sqlite3
src = sqlite3.connect("/opt/reportinsta/data/instagram_bot.db")
src.execute("vacuum into '/tmp/reportinsta_audit_snap.db'")
src.close()
print("SNAP_OK")
"""


def run(client, script, label):
    _, stdout, stderr = client.exec_command("python3 - <<'PY'\n" + script + "\nPY")
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    if err.strip():
        print(f"[{label}] stderr: {err.strip()}", file=sys.stderr)
    return out


def main():
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASSWORD, timeout=30)
    sftp = client.open_sftp()
    try:
        inventory = run(client, INVENTORY, "inventory")
        print("=== SERVER DB INVENTORY ===")
        print(inventory)

        print("=== SNAPSHOT ===")
        print(run(client, SNAPSHOT, "snapshot").strip())
        sftp.get(REMOTE_SNAP, LOCAL_SNAP)
        print(f"downloaded: {LOCAL_SNAP} ({os.path.getsize(LOCAL_SNAP)} bytes)")
        run(client, "rm -f /tmp/reportinsta_audit_snap.db", "cleanup")

        # Log tail (read-only) — recent errors/health of the live bot.
        try:
            with sftp.file(REMOTE_LOG, "rb") as fh:
                fh.seek(0, 2)
                size = fh.tell()
                fh.seek(max(0, size - 200_000))
                tail = fh.read().decode("utf-8", "replace")
            with open(LOCAL_LOG_TAIL, "w", encoding="utf-8") as fh:
                fh.write(tail)
            print(f"log tail saved: {LOCAL_LOG_TAIL} ({len(tail)} chars of {size})")
        except IOError as e:
            print(f"log tail unavailable: {e}")

        # Compare server .env with local .env — equality only, no secrets printed.
        try:
            with sftp.file(REMOTE_ENV, "r") as fh:
                server_env = fh.read().decode("utf-8", "replace")
        except IOError:
            server_env = ""
        local_env_path = os.path.join(os.path.dirname(LOCAL_DIR), ".env")
        with open(local_env_path, "r", encoding="utf-8") as fh:
            local_env = fh.read()

        def field(text, name):
            for line in text.splitlines():
                if line.startswith(name + "="):
                    return line.split("=", 1)[1].strip()
            return None

        print("=== ENV COMPARE (server vs local, equality only) ===")
        for name in ("BOT_TOKEN", "OPENAI_API_KEY", "INSTAGRAM_ACCOUNTS",
                     "ADMIN_TELEGRAM_ID", "ALLOWED_TELEGRAM_USERNAMES", "DATABASE_URL"):
            sv, lv = field(server_env, name), field(local_env, name)
            status = "same" if sv == lv else ("DIFFERENT" if sv and lv else
                      ("only-server" if sv else "only-local"))
            print(f"{name}: {status}")
    finally:
        sftp.close()
        client.close()


if __name__ == "__main__":
    main()
