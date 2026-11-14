"""Add (or re-token) an Instagram account in the production .env.

The bot reads INSTAGRAM_ACCOUNTS from /opt/reportinsta/.env at startup, so a
new account is added there and picked up on restart. The helper is idempotent
(matched by user_id), keeps a timestamped backup, preserves ownership and 600
permissions, validates the resulting config with the bot's own Settings, and
never prints a token.

Usage:
    set NEW_IG_TOKEN=<long-lived token>
    python audit_snapshot/add_account.py --name roomguesthouses \
        --user-id 17841461739841151 --apply

Without --apply only the planned change is printed (dry run).
"""
import argparse
import json
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from audit_snapshot.ssh import connect

REMOTE_DIR = "/opt/reportinsta"
REMOTE_ENV = f"{REMOTE_DIR}/.env"
ENV_KEY = "INSTAGRAM_ACCOUNTS"


def _mask(token: str) -> str:
    return f"{token[:6]}…{token[-4:]} ({len(token)} chars)" if token else "(empty)"


def _read_env(client) -> str:
    sftp = client.open_sftp()
    try:
        with sftp.file(REMOTE_ENV, "r") as fh:
            return fh.read().decode("utf-8", "replace")
    finally:
        sftp.close()


def _run(client, command: str, timeout: int = 60) -> tuple[int, str, str]:
    _, out, err = client.exec_command(command, timeout=timeout)
    stdout = out.read().decode("utf-8", "replace").strip()
    stderr = err.read().decode("utf-8", "replace").strip()
    return out.channel.recv_exit_status(), stdout, stderr


def _extract_value(raw: str) -> tuple[str, str]:
    """Return (raw JSON value, quote character) of the INSTAGRAM_ACCOUNTS line."""
    for line in raw.splitlines():
        if line.startswith(ENV_KEY + "="):
            value = line.split("=", 1)[1].strip()
            quote = value[0] if value[:1] in {"'", '"'} else ""
            if quote and value.endswith(quote):
                value = value[1:-1]
            return value, quote
    raise SystemExit(f"{ENV_KEY} not found in {REMOTE_ENV}")


def _replace_value(raw: str, new_value: str, quote: str) -> str:
    lines = raw.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.startswith(ENV_KEY + "="):
            ending = "\n" if line.endswith("\n") else ""
            lines[index] = f"{ENV_KEY}={quote}{new_value}{quote}{ending}"
            break
    return "".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True, help="Display name / @handle")
    parser.add_argument("--user-id", required=True, help="Instagram Business Account ID")
    parser.add_argument("--username", default=None,
                        help="Real @handle when it differs from --name")
    parser.add_argument("--token-env", default="NEW_IG_TOKEN",
                        help="Env var that holds the long-lived access token")
    parser.add_argument("--apply", action="store_true", help="Write the change")
    args = parser.parse_args()

    token = os.getenv(args.token_env, "").strip()
    if not token:
        raise SystemExit(f"Set {args.token_env} to the account's long-lived access token")

    client = connect()
    try:
        raw = _read_env(client)
        value, quote = _extract_value(raw)
        accounts = json.loads(value or "[]")
        if not isinstance(accounts, list):
            raise SystemExit(f"{ENV_KEY} is not a JSON array")

        entry = {"name": args.name, "user_id": args.user_id, "access_token": token}
        if args.username:
            entry["username"] = args.username

        existing = next(
            (acc for acc in accounts if str(acc.get("user_id")) == args.user_id), None
        )
        if existing is None:
            accounts.append(entry)
            action = "append"
            print(f"accounts before: {len(accounts) - 1}")
        elif existing.get("access_token") != token or existing.get("name") != args.name:
            existing.update(entry)
            action = "update"
            print(f"accounts before: {len(accounts)}")
        else:
            action = "noop"
            print(f"accounts before: {len(accounts)}")

        print(f"action: {action} ({args.name}, user_id={args.user_id})")
        print(f"token: {_mask(token)}")
        if action == "noop":
            print("nothing to change")
            return
        print("accounts after:", len(accounts))

        if not args.apply:
            print("dry run: re-run with --apply to write the change")
            return

        stamp = _run(client, "date -u +%Y%m%d-%H%M%S")[1]
        backup = f"{REMOTE_ENV}.bak-account-{stamp}"
        code, _, err = _run(client, f"cp -p {REMOTE_ENV} {backup} && chmod 600 {backup}")
        if code != 0:
            raise SystemExit(f"backup failed: {err}")
        print("backup:", backup)

        encoded = json.dumps(accounts, ensure_ascii=False, separators=(",", ":"))
        new_raw = _replace_value(raw, encoded, quote)

        sftp = client.open_sftp()
        try:
            with sftp.file(REMOTE_ENV + ".new", "w") as fh:
                fh.write(new_raw.encode("utf-8"))
        finally:
            sftp.close()

        code, _, err = _run(
            client,
            f"chmod 600 {REMOTE_ENV}.new && chown reportbot:reportbot {REMOTE_ENV}.new "
            f"&& mv {REMOTE_ENV}.new {REMOTE_ENV}",
        )
        if code != 0:
            raise SystemExit(f"write failed: {err}")

        check = (
            f"cd {REMOTE_DIR} && .venv/bin/python -c "
            "\"from config import settings; print(len(settings.ACCOUNTS)); "
            "print(settings.validate()[0])\""
        )
        code, out, err = _run(client, check, timeout=120)
        print("--- config check ---")
        print(out or err)
        if code != 0 or "[]" not in out:
            _run(client, f"cp -p {backup} {REMOTE_ENV}")
            raise SystemExit("config validation failed — .env restored from backup")
        print("config check: OK (restart the service to apply)")
    finally:
        client.close()


if __name__ == "__main__":
    main()
