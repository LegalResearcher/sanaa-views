"""Shared persistent rotation for the scheduled and manual workflows."""
import json
import os
import tempfile

ACCOUNT_COUNT = 5
STATE_FILENAME = "account_rotation.json"


def _state_path(state_dir):
    return os.path.join(state_dir, STATE_FILENAME)


def _read_next_account(state_dir):
    try:
        with open(_state_path(state_dir), "r", encoding="utf-8") as file:
            value = json.load(file)
        next_account = value.get("next_account") if isinstance(value, dict) else None
        if isinstance(next_account, int) and not isinstance(next_account, bool):
            if 1 <= next_account <= ACCOUNT_COUNT:
                return next_account
    except (OSError, ValueError, TypeError):
        pass
    return 1


def _write_next_account(state_dir, next_account):
    os.makedirs(state_dir, exist_ok=True)
    path = _state_path(state_dir)
    fd, temporary = tempfile.mkstemp(prefix=".rotation-", dir=state_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump({"next_account": next_account}, file, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def select_account(state_dir, environ=None):
    """Return (account_number, username, password) and advance the shared pointer.

    The pointer advances only after the selected account has both required secrets.
    No secret is written to disk or included in the returned error messages.
    """
    environ = os.environ if environ is None else environ
    account_number = _read_next_account(state_dir)
    username = environ.get(f"SITE_USERNAME_{account_number}", "").strip()
    password = environ.get(f"SITE_PASSWORD_{account_number}", "")
    if not username or not password:
        raise RuntimeError(
            f"بيانات دخول الحساب {account_number} ناقصة: "
            f"SITE_USERNAME_{account_number} وSITE_PASSWORD_{account_number}"
        )
    _write_next_account(state_dir, account_number % ACCOUNT_COUNT + 1)
    return account_number, username, password
