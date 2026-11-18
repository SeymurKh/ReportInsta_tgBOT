"""Replace Instagram access tokens for many accounts in one atomic pass.

The bot reads INSTAGRAM_ACCOUNTS from .env at startup, so a new token is
written there and picked up on restart (seed_accounts() syncs it into the
accounts table). Unlike add_account.py (one account at a time), this helper
replaces several tokens at once: one backup, one write, one config validation,
and an automatic rollback if anything goes wrong. Accounts that are not listed
in the tokens file are left byte-for-byte untouched.

Usage:
    python audit_snapshot/replace_account_tokens.py --local --tokens-file tokens.json
    python audit_snapshot/replace_account_tokens.py --remote --apply --tokens-file -
    Get-Content tokens.json -Raw | python audit_snapshot/replace_account_tokens.py --remote --apply --tokens-file -

The tokens file is a JSON object mapping the account ``name`` from
INSTAGRAM_ACCOUNTS to a new token; use ``-`` to read it from stdin so the
secrets never touch the disk:
    {"room.airport": "IGAA...", "cms.space": "IGAA..."}

Each target (.env) is read on its own before a plan is built, so --local and
--remote never borrow each other's state. Without --apply only the planned
change is printed. Tokens are never printed — only a mask. Every token is
verified against GET /me first: a token that belongs to another account aborts
the run before anything is written.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

REMOTE_DIR = "/opt/reportinsta"
REMOTE_ENV = f"{REMOTE_DIR}/.env"
LOCAL_ENV = os.path.join(_PROJECT_ROOT, ".env")
ENV_KEY = "INSTAGRAM_ACCOUNTS"
GRAPH_URL = "https://graph.instagram.com/v25.0/me"
VERIFY_FIELDS = "user_id,username,name,account_type"


def _mask(token: str) -> str:
    return f"{token[:6]}…{token[-4:]} ({len(token)} chars)" if token else "(empty)"


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _read_tokens(path: str) -> dict[str, str]:
    """Load the {account name: token} mapping; ``-`` reads stdin."""
    if path == "-":
        raw = sys.stdin.read()
        source = "stdin"
    else:
        with open(path, "r", encoding="utf-8") as fh:
            raw = fh.read()
        source = path
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as error:
        raise SystemExit(f"tokens file is not valid JSON ({source}): {error}")
    if not isinstance(data, dict) or not data:
        raise SystemExit(f"tokens file must be a non-empty JSON object ({source})")
    result: dict[str, str] = {}
    for name, token in data.items():
        token = (token or "").strip()
        if not token:
            raise SystemExit(f"empty token for account '{name}'")
        result[name] = token
    return result


# ── .env handling ──

def _extract_value(raw: str) -> tuple[str, str]:
    """Return (raw JSON value, quote character) of the INSTAGRAM_ACCOUNTS line."""
    for line in raw.splitlines():
        if line.startswith(ENV_KEY + "="):
            value = line.split("=", 1)[1].strip()
            quote = value[0] if value[:1] in {"'", '"'} else ""
            if quote and value.endswith(quote):
                value = value[1:-1]
            return value, quote
    raise SystemExit(f"{ENV_KEY} not found")


def _replace_value(raw: str, new_value: str, quote: str) -> str:
    """Replace the JSON array on the INSTAGRAM_ACCOUNTS line, keeping the rest
    of the file (including CRLF endings) untouched."""
    lines = raw.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.startswith(ENV_KEY + "="):
            ending = line[len(line.rstrip("\r\n")):]
            lines[index] = f"{ENV_KEY}={quote}{new_value}{quote}{ending}"
            break
    return "".join(lines)


def _decode_accounts(raw: str) -> list[dict]:
    value, _quote = _extract_value(raw)
    try:
        accounts = json.loads(value or "[]")
    except json.JSONDecodeError as error:
        raise SystemExit(f"{ENV_KEY} contains invalid JSON: {error}")
    if not isinstance(accounts, list):
        raise SystemExit(f"{ENV_KEY} is not a JSON array")
    return accounts


def _encode_accounts(accounts: list[dict]) -> str:
    return json.dumps(accounts, ensure_ascii=False, separators=(",", ":"))


# ── preflight via Graph API ──

def _verify_token(token: str) -> dict:
    url = f"{GRAPH_URL}?fields={VERIFY_FIELDS}&access_token={token}"
    try:
        with urllib.request.urlopen(url, timeout=25) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", "replace")[:200]
        raise SystemExit(f"token rejected by API (HTTP {error.code}): {body}")
    except Exception as error:  # network problems must not look like success
        raise SystemExit(f"token could not be verified: {error!r}")


# ── plan ──

def _build_plan(accounts: list[dict], tokens: dict[str, str], verify: bool,
                cache: dict | None = None) -> list[dict]:
    """Match tokens to accounts by ``name`` and prove every token belongs to the
    account it is assigned to. Aborts before anything is written on mismatch."""
    by_name = {str(acc.get("name")): acc for acc in accounts}
    unknown = sorted(set(tokens) - set(by_name))
    if unknown:
        raise SystemExit(
            "unknown account name(s) in tokens file: "
            + ", ".join(unknown)
            + " — known: "
            + ", ".join(sorted(by_name))
        )

    plan: list[dict] = []
    for name, token in tokens.items():
        account = by_name[name]
        old = str(account.get("access_token") or "")
        user_id = str(account.get("user_id") or "")
        entry = {
            "name": name,
            "user_id": user_id,
            "old": old,
            "new": token,
            "changed": token != old,
        }
        if verify:
            info = (cache or {}).get(name) or _verify_token(token)
            api_user_id = str(info.get("user_id") or info.get("id") or "")
            if not api_user_id or api_user_id != user_id:
                raise SystemExit(
                    f"token for '{name}' belongs to user_id {api_user_id or '(none)'}, "
                    f"but .env has {user_id} — nothing was written"
                )
            if cache is not None:
                cache[name] = info
            print(
                f"  verified @{info.get('username')} "
                f"(type={info.get('account_type')}, user_id={api_user_id})"
            )
        plan.append(entry)
    return plan


def _print_plan(plan: list[dict], label: str) -> None:
    print(f"--- plan for {label} ---")
    for entry in plan:
        state = "replace" if entry["changed"] else "noop (same token)"
        print(f"  {entry['name']}: {state}")
        print(f"    old: {_mask(entry['old'])}")
        print(f"    new: {_mask(entry['new'])}")


def _apply_plan(accounts: list[dict], plan: list[dict]) -> list[dict]:
    """Return a new accounts list with the planned tokens, keeping every other
    field and every other account untouched."""
    new_tokens = {entry["name"]: entry["new"] for entry in plan}
    updated: list[dict] = []
    for account in accounts:
        name = str(account.get("name"))
        if name in new_tokens:
            clone = dict(account)
            clone["access_token"] = new_tokens[name]
            updated.append(clone)
        else:
            updated.append(account)
    return updated


def _check_untouched(before: list[dict], after: list[dict], plan: list[dict]) -> None:
    """Every account outside the plan must be identical, field by field."""
    targets = {entry["name"] for entry in plan}
    old_by_name = {str(acc.get("name")): acc for acc in before}
    new_by_name = {str(acc.get("name")): acc for acc in after}
    if list(old_by_name) != list(new_by_name):
        raise SystemExit("account list changed unexpectedly")
    for name, old_account in old_by_name.items():
        if name in targets:
            continue
        if old_account != new_by_name[name]:
            raise SystemExit(f"untouched account '{name}' changed unexpectedly")


# ── local .env ──

CHECK_CODE = (
    "from config import settings; "
    "errors = settings.validate()[0]; "
    "print('accounts', len(settings.ACCOUNTS)); "
    "print('errors', errors); "
    "raise SystemExit(1 if errors else 0)"
)


def _check_config_locally(label: str) -> None:
    """Validate the .env with the bot's own Settings in a fresh interpreter."""
    result = subprocess.run(
        [sys.executable, "-c", CHECK_CODE],
        cwd=_PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    output = (result.stdout + result.stderr).strip()
    print(f"--- config check ({label}) ---")
    print(output or "(no output)")
    if result.returncode != 0:
        raise SystemExit(f"config validation failed for {label}")


def apply_local(tokens: dict[str, str], verify: bool, apply: bool,
                cache: dict | None = None) -> None:
    print(f"=== LOCAL {LOCAL_ENV} ===")
    with open(LOCAL_ENV, "r", encoding="utf-8", newline="") as fh:
        raw = fh.read()
    accounts = _decode_accounts(raw)
    before = [dict(acc) for acc in accounts]
    _value, quote = _extract_value(raw)

    plan = _build_plan(accounts, tokens, verify, cache)
    _print_plan(plan, "local")
    changed = [entry for entry in plan if entry["changed"]]
    print(f"accounts to change: {len(changed)} of {len(plan)}")
    if not changed:
        print("nothing to change")
        return
    if not apply:
        print("dry run: re-run with --apply to write the change")
        return

    updated = _apply_plan(accounts, plan)
    new_raw = _replace_value(raw, _encode_accounts(updated), quote)
    _check_untouched(before, _decode_accounts(new_raw), plan)

    backup = f"{LOCAL_ENV}.bak-account-{_stamp()}"
    shutil.copy2(LOCAL_ENV, backup)
    print("backup:", backup)

    tmp_path = LOCAL_ENV + ".new"
    with open(tmp_path, "w", encoding="utf-8", newline="") as fh:
        fh.write(new_raw)
    os.replace(tmp_path, LOCAL_ENV)

    with open(LOCAL_ENV, "r", encoding="utf-8", newline="") as fh:
        written = _decode_accounts(fh.read())
    try:
        _check_untouched(before, written, plan)
        _check_stored(plan, written)
        _check_config_locally("local")
    except SystemExit:
        shutil.copy2(backup, LOCAL_ENV)
        print("rollback: local .env restored from backup")
        raise
    print("local .env updated")


def _check_stored(plan: list[dict], written: list[dict]) -> None:
    """Every planned account must now carry exactly the new token."""
    stored_by_name = {str(acc.get("name")): acc for acc in written}
    for entry in plan:
        stored = stored_by_name.get(entry["name"])
        if stored is None:
            raise SystemExit(f"account '{entry['name']}' disappeared from .env")
        if stored.get("access_token") != entry["new"]:
            raise SystemExit(f"token for '{entry['name']}' was not stored")


# ── remote (production) .env ──

def _run(client, command: str, timeout: int = 60) -> tuple[int, str, str]:
    _, out, err = client.exec_command(command, timeout=timeout)
    stdout = out.read().decode("utf-8", "replace").strip()
    stderr = err.read().decode("utf-8", "replace").strip()
    return out.channel.recv_exit_status(), stdout, stderr


def _remote_read(client, path: str = REMOTE_ENV) -> str:
    sftp = client.open_sftp()
    try:
        with sftp.file(path, "r") as fh:
            return fh.read().decode("utf-8", "replace")
    finally:
        sftp.close()


def _remote_write(client, path: str, text: str) -> None:
    sftp = client.open_sftp()
    try:
        with sftp.file(path, "w") as fh:
            fh.write(text.encode("utf-8"))
    finally:
        sftp.close()


def _remote_check(client) -> None:
    """Validate the production .env with the bot's own config in its venv."""
    command = (
        f'cd {REMOTE_DIR} && .venv/bin/python -c "'
        "from config import settings; errors = settings.validate()[0]; "
        "print('accounts', len(settings.ACCOUNTS)); "
        "print('errors', errors); "
        'raise SystemExit(1 if errors else 0)"'
    )
    code, out, err = _run(client, command, timeout=120)
    print("--- config check (remote) ---")
    print(out or err)
    if code != 0 or "[]" not in out:
        raise SystemExit("remote config validation failed")


def apply_remote(tokens: dict[str, str], verify: bool, apply: bool,
                 cache: dict | None = None) -> None:
    from audit_snapshot.ssh import connect

    print(f"=== REMOTE {REMOTE_ENV} ===")
    client = connect()
    try:
        raw = _remote_read(client)
        accounts = _decode_accounts(raw)
        before = [dict(acc) for acc in accounts]
        _value, quote = _extract_value(raw)
        print(f"accounts on server: {len(accounts)}")

        plan = _build_plan(accounts, tokens, verify, cache)
        _print_plan(plan, "remote")
        changed = [entry for entry in plan if entry["changed"]]
        print(f"accounts to change: {len(changed)} of {len(plan)}")
        if not changed:
            print("nothing to change")
            return
        if not apply:
            print("dry run: re-run with --apply to write the change")
            return

        updated = _apply_plan(accounts, plan)
        new_raw = _replace_value(raw, _encode_accounts(updated), quote)
        _check_untouched(before, _decode_accounts(new_raw), plan)

        stamp = _run(client, "date -u +%Y%m%d-%H%M%S")[1]
        backup = f"{REMOTE_ENV}.bak-account-{stamp}"
        code, _, err = _run(client, f"cp -p {REMOTE_ENV} {backup} && chmod 600 {backup}")
        if code != 0:
            raise SystemExit(f"backup failed: {err}")
        print("backup:", backup)

        _remote_write(client, REMOTE_ENV + ".new", new_raw)
        code, _, err = _run(
            client,
            f"chmod 600 {REMOTE_ENV}.new && chown reportbot:reportbot {REMOTE_ENV}.new "
            f"&& mv {REMOTE_ENV}.new {REMOTE_ENV}",
        )
        if code != 0:
            _run(client, f"rm -f {REMOTE_ENV}.new")
            raise SystemExit(f"write failed: {err}")

        try:
            written = _decode_accounts(_remote_read(client))
            _check_untouched(before, written, plan)
            _check_stored(plan, written)
            _remote_check(client)
        except SystemExit:
            _run(client, f"cp -p {backup} {REMOTE_ENV}")
            print("rollback: remote .env restored from backup")
            raise
        print("remote .env updated (restart the service to apply)")
    finally:
        client.close()


# ── main ──

def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--tokens-file", required=True,
                        help="JSON {account name: token}; '-' reads stdin")
    parser.add_argument("--local", action="store_true", help="update the local .env")
    parser.add_argument("--remote", action="store_true", help="update the production .env")
    parser.add_argument("--apply", action="store_true",
                        help="write the change (default: dry run)")
    parser.add_argument("--skip-verify", action="store_true",
                        help="do not call GET /me for every token")
    args = parser.parse_args()

    if not (args.local or args.remote):
        raise SystemExit("choose at least one target: --local and/or --remote")

    tokens = _read_tokens(args.tokens_file)
    print("tokens received: %d -> %s" % (len(tokens), ", ".join(tokens)))

    verified: dict = {}  # one GET /me per account even with --local --remote
    verify = not args.skip_verify
    if args.local:
        apply_local(tokens, verify, args.apply, verified)
    if args.remote:
        apply_remote(tokens, verify, args.apply, verified)

    print("done" if args.apply else "dry run finished (nothing was written)")


if __name__ == "__main__":
    main()




