"""Machine-scoped account writer ownership for Binance mutations.

The registry is coordination state only. It deliberately lives outside every
business database so a second SQLite runtime cannot acquire the same account.
"""

from __future__ import annotations

import json
import os
import socket
import tempfile
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any

REGISTRY_ENV = "V2_ACCOUNT_WRITER_REGISTRY_PATH"
ACCOUNT_SCOPE_ENV = "BINANCE_ACCOUNT_SCOPE_ID"
OPERATOR_IDENTITY_ENV = "BINANCE_OPERATOR_IDENTITY"
DEFAULT_LEASE_SECONDS = 360.0


class AccountWriterFenceError(RuntimeError):
    """Raised before a Binance mutation when account ownership is invalid."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        super().__init__(f"{code}{(': ' + detail) if detail else ''}")


@dataclass(frozen=True)
class AccountScope:
    exchange: str
    environment: str
    account_identity: str

    @property
    def key(self) -> str:
        return f"{self.exchange.upper()}:{self.environment.upper()}:{self.account_identity}"


@dataclass(frozen=True)
class AccountWriterCapability:
    account_scope_key: str
    database_identity: str
    owner_id: str
    generation: int
    registry_identity: str


@dataclass(frozen=True)
class AccountWriterLease:
    capability: AccountWriterCapability
    acquired_at: str
    lease_expires_at: str
    host_identity: str


def database_identity(database_url: str) -> str:
    """Return a stable non-secret identity for a database URL."""
    if database_url.startswith("sqlite:///"):
        raw_path = database_url.removeprefix("sqlite:///")
        normalized = os.path.normcase(os.path.abspath(os.path.normpath(raw_path)))
        return f"sqlite-path-sha256:{sha256(normalized.encode('utf-8')).hexdigest()}"
    return f"database-sha256:{sha256(database_url.strip().encode('utf-8')).hexdigest()}"


def registry_path() -> Path:
    configured = os.environ.get(REGISTRY_ENV, "").strip()
    if configured:
        return Path(configured).expanduser()
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    base = Path(local_app_data) if local_app_data else Path.home() / ".local" / "share"
    return base / "AIQuant" / "account-writer-registry.json"


def _lock_path(path: Path) -> Path:
    return path.with_name(f"{path.name}.lock")


@contextmanager
def _registry_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock_path(path).open("a+b") as lock_file:
        if os.name == "nt":
            import msvcrt

            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)  # type: ignore[attr-defined]
        try:
            yield
        finally:
            if os.name == "nt":
                import msvcrt

                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)  # type: ignore[attr-defined]


def _read(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        raise AccountWriterFenceError("ACCOUNT_WRITER_REGISTRY_UNREADABLE", str(exc)) from exc


def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f"{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        with suppress(FileNotFoundError):
            os.unlink(temporary)


def resolve_account_scope(
    *, account_identity: str | None = None, exchange: str = "BINANCE", environment: str = "TESTNET"
) -> AccountScope:
    identity = (account_identity or os.environ.get(ACCOUNT_SCOPE_ENV, "")).strip()
    if not identity:
        raise AccountWriterFenceError("ACCOUNT_SCOPE_IDENTITY_MISSING")
    scope = AccountScope(exchange=exchange, environment=environment, account_identity=identity)
    if scope.exchange.upper() != "BINANCE" or scope.environment.upper() != "TESTNET":
        raise AccountWriterFenceError("ACCOUNT_SCOPE_UNSUPPORTED", scope.key)
    return scope


def account_scope_from_binance_client(client: Any) -> AccountScope:
    """Resolve a stable Binance UID, falling back only to explicit scope config."""
    configured = os.environ.get(ACCOUNT_SCOPE_ENV, "").strip()
    response: Any = None
    try:
        account_endpoint = getattr(client, "fapiPrivateGetAccount", None)
        response = account_endpoint({}) if callable(account_endpoint) else None
    except Exception:
        response = None
    uid = response.get("uid") if isinstance(response, dict) else None
    if uid is None and isinstance(response, dict):
        uid = response.get("accountId") or response.get("userId")
    if uid is not None and str(uid).strip():
        scope = resolve_account_scope(account_identity=str(uid))
        if configured and configured != scope.account_identity:
            raise AccountWriterFenceError("ACCOUNT_SCOPE_IDENTITY_MISMATCH")
        return scope
    return resolve_account_scope(account_identity=configured)


def _now() -> datetime:
    return datetime.now(UTC)


def _parse(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _dead_local_supervisor_owner(owner_id: str) -> bool:
    """Allow crash recovery only for a known-dead, same-host supervisor owner."""
    if owner_id.count(":") != 1:
        return False
    owner_host, raw_pid = owner_id.rsplit(":", 1)
    if owner_host.casefold() != socket.gethostname().casefold():
        return False
    try:
        owner_pid = int(raw_pid)
    except ValueError:
        return False
    if owner_pid <= 0:
        return False
    try:
        os.kill(owner_pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    except OSError:
        # Windows reports a non-existent PID as a generic OSError on some
        # versions. Permission failures were handled above and remain fenced.
        return True
    return False


def bind_account(
    *, account_scope_key: str, database_id: str, operator_identity: str, operator_reason: str
) -> dict[str, Any]:
    if not operator_identity.strip() or not operator_reason.strip():
        raise AccountWriterFenceError("OPERATOR_BINDING_METADATA_MISSING")
    path = registry_path()
    with _registry_lock(path):
        root = _read(path) or {}
        accounts = dict(root.get("accounts") or {})
        current = dict(accounts.get(account_scope_key) or {})
        if current.get("bound_database_identity") not in (None, database_id):
            raise AccountWriterFenceError("ACCOUNT_ALREADY_BOUND_TO_DIFFERENT_DATABASE")
        value = dict(current)
        value.update(
            {
                "account_scope_key": account_scope_key,
                "bound_database_identity": database_id,
                "operator_binding_metadata": {
                    "operator_identity": operator_identity.strip(),
                    "operator_reason": operator_reason.strip(),
                    "bound_at": _now().isoformat(),
                },
            }
        )
        accounts[account_scope_key] = value
        root["accounts"] = accounts
        _write(path, root)
        return value


def rebind_account(
    *,
    account_scope_key: str,
    database_id: str,
    operator_identity: str,
    operator_reason: str,
    exchange_is_flat: Callable[[], bool],
    exchange_open_orders_empty: Callable[[], bool],
    new_database_recovery_clear: Callable[[], bool],
) -> dict[str, Any]:
    """Explicitly move a binding after exchange and recovery preflight."""
    if not operator_identity.strip() or not operator_reason.strip():
        raise AccountWriterFenceError("OPERATOR_BINDING_METADATA_MISSING")
    if not exchange_is_flat() or not exchange_open_orders_empty() or not new_database_recovery_clear():
        raise AccountWriterFenceError("ACCOUNT_REBIND_REQUIRES_MANUAL_RECOVERY")
    path = registry_path()
    with _registry_lock(path):
        root = _read(path) or {}
        accounts = dict(root.get("accounts") or {})
        current = dict(accounts.get(account_scope_key) or {})
        if not current:
            raise AccountWriterFenceError("ACCOUNT_BINDING_REQUIRED")
        if current.get("lease_expires_at") and _parse(str(current["lease_expires_at"])) > _now():
            raise AccountWriterFenceError("ACCOUNT_REBIND_REQUIRES_INACTIVE_WRITER")
        current.update(
            {
                "bound_database_identity": database_id,
                "operator_binding_metadata": {
                    "operator_identity": operator_identity.strip(),
                    "operator_reason": operator_reason.strip(),
                    "rebound_at": _now().isoformat(),
                },
            }
        )
        for field in (
            "owner_id",
            "lease_acquired_at",
            "lease_renewed_at",
            "lease_expires_at",
            "host_identity",
        ):
            current.pop(field, None)
        accounts[account_scope_key] = current
        root["accounts"] = accounts
        _write(path, root)
        return current


def acquire_account_writer(
    *,
    account_scope_key: str,
    database_id: str,
    owner_id: str | None = None,
    lease_seconds: float = DEFAULT_LEASE_SECONDS,
) -> AccountWriterLease:
    path = registry_path()
    owner = owner_id or f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex}"
    observed = _now()
    with _registry_lock(path):
        root = _read(path) or {}
        current = dict((root.get("accounts") or {}).get(account_scope_key) or {})
        if not current:
            raise AccountWriterFenceError("ACCOUNT_BINDING_REQUIRED")
        bound = current.get("bound_database_identity")
        if bound is None:
            raise AccountWriterFenceError("ACCOUNT_BINDING_REQUIRED")
        if bound != database_id:
            raise AccountWriterFenceError("ACCOUNT_BOUND_TO_DIFFERENT_DATABASE")
        existing_expiry = _parse(str(current["lease_expires_at"])) if current.get("lease_expires_at") else None
        existing_owner = str(current.get("owner_id") or "")
        existing_generation = int(current.get("generation") or 0)
        same_owner = existing_owner == owner
        if (
            existing_expiry is not None
            and existing_expiry > observed
            and not same_owner
            and not _dead_local_supervisor_owner(existing_owner)
        ):
            raise AccountWriterFenceError("ACCOUNT_WRITER_ALREADY_HELD")
        generation = (
            existing_generation
            if same_owner and existing_expiry and existing_expiry > observed
            else existing_generation + 1
        )
        acquired = observed.isoformat()
        expiry = (observed + timedelta(seconds=lease_seconds)).isoformat()
        value = dict(current)
        value.update(
            {
                "account_scope_key": account_scope_key,
                "bound_database_identity": database_id,
                "owner_id": owner,
                "generation": generation,
                "lease_acquired_at": acquired,
                "lease_renewed_at": acquired,
                "lease_expires_at": expiry,
                "host_identity": socket.gethostname(),
            }
        )
        accounts = dict(root.get("accounts") or {})
        accounts[account_scope_key] = value
        root["accounts"] = accounts
        _write(path, root)
        capability = AccountWriterCapability(account_scope_key, database_id, owner, generation, str(path))
        return AccountWriterLease(capability, acquired, expiry, socket.gethostname())


def renew_account_writer(
    lease: AccountWriterLease, *, lease_seconds: float = DEFAULT_LEASE_SECONDS
) -> AccountWriterLease:
    path = Path(lease.capability.registry_identity)
    observed = _now()
    with _registry_lock(path):
        root = _read(path) or {}
        current = dict((root.get("accounts") or {}).get(lease.capability.account_scope_key) or {})
        _assert_current(current, lease.capability, observed)
        expiry = (observed + timedelta(seconds=lease_seconds)).isoformat()
        current.update({"lease_renewed_at": observed.isoformat(), "lease_expires_at": expiry})
        accounts = dict(root.get("accounts") or {})
        accounts[lease.capability.account_scope_key] = current
        root["accounts"] = accounts
        _write(path, root)
    return AccountWriterLease(lease.capability, lease.acquired_at, expiry, lease.host_identity)


def release_account_writer(lease: AccountWriterLease) -> None:
    path = Path(lease.capability.registry_identity)
    with _registry_lock(path):
        root = _read(path) or {}
        current = dict((root.get("accounts") or {}).get(lease.capability.account_scope_key) or {})
        _assert_current(current, lease.capability, _now())
        current["lease_expires_at"] = _now().isoformat()
        accounts = dict(root.get("accounts") or {})
        accounts[lease.capability.account_scope_key] = current
        root["accounts"] = accounts
        _write(path, root)


def _assert_current(current: dict[str, Any], capability: AccountWriterCapability, observed: datetime) -> None:
    if current.get("account_scope_key") != capability.account_scope_key:
        raise AccountWriterFenceError("ACCOUNT_WRITER_FENCE_REJECTED", "scope")
    if current.get("bound_database_identity") != capability.database_identity:
        raise AccountWriterFenceError("ACCOUNT_WRITER_FENCE_REJECTED", "database")
    if current.get("owner_id") != capability.owner_id or int(current.get("generation") or 0) != capability.generation:
        raise AccountWriterFenceError("ACCOUNT_WRITER_FENCE_REJECTED", "owner or generation")
    if not current.get("lease_expires_at") or _parse(str(current["lease_expires_at"])) <= observed:
        raise AccountWriterFenceError("ACCOUNT_WRITER_FENCE_REJECTED", "lease expired")


@contextmanager
def mutation_guard(capability: AccountWriterCapability) -> Iterator[None]:
    if not isinstance(capability, AccountWriterCapability):
        raise AccountWriterFenceError("ACCOUNT_WRITER_FENCE_REJECTED", "capability type")
    path = Path(capability.registry_identity)
    if path.resolve() != registry_path().resolve():
        raise AccountWriterFenceError("ACCOUNT_WRITER_FENCE_REJECTED", "registry identity")
    with _registry_lock(path):
        root = _read(path) or {}
        current = dict((root.get("accounts") or {}).get(capability.account_scope_key) or {})
        _assert_current(current, capability, _now())
        yield


def capability_is_current(capability: AccountWriterCapability | None) -> bool:
    """Check a capability without exposing registry contents or raising to callers."""
    if not isinstance(capability, AccountWriterCapability):
        return False
    try:
        path = Path(capability.registry_identity)
        if path.resolve() != registry_path().resolve():
            return False
        with _registry_lock(path):
            root = _read(path) or {}
            current = dict((root.get("accounts") or {}).get(capability.account_scope_key) or {})
            _assert_current(current, capability, _now())
    except (AccountWriterFenceError, OSError, ValueError, TypeError):
        return False
    return True


def writer_status(
    *, account_scope_key: str, database_id: str, capability: AccountWriterCapability | None = None
) -> dict[str, Any]:
    """Return safe writer observability fields for runtime state and diagnostics."""
    path = registry_path()
    try:
        with _registry_lock(path):
            root = _read(path) or {}
            current = dict((root.get("accounts") or {}).get(account_scope_key) or {})
    except AccountWriterFenceError as exc:
        return {
            "account_scope_key": account_scope_key,
            "writer_status": "REGISTRY_UNREADABLE",
            "bound_database_identity": None,
            "current_database_identity": database_id,
            "generation": None,
            "lease_status": "UNKNOWN",
            "conflict_reason": exc.code,
        }
    if not current:
        return {
            "account_scope_key": account_scope_key,
            "writer_status": "UNBOUND",
            "bound_database_identity": None,
            "current_database_identity": database_id,
            "generation": None,
            "lease_status": "NONE",
            "conflict_reason": "ACCOUNT_BINDING_REQUIRED",
        }
    expiry = current.get("lease_expires_at")
    lease_active = False
    if expiry:
        with suppress(ValueError, TypeError):
            lease_active = _parse(str(expiry)) > _now()
    capability_valid = False
    if capability is not None:
        with suppress(AccountWriterFenceError, ValueError, TypeError):
            _assert_current(current, capability, _now())
            capability_valid = True
    bound_database = current.get("bound_database_identity")
    conflict = None
    if bound_database != database_id:
        conflict = "ACCOUNT_BOUND_TO_DIFFERENT_DATABASE"
    elif lease_active and not capability_valid:
        conflict = "ACCOUNT_WRITER_ALREADY_HELD"
    return {
        "account_scope_key": account_scope_key,
        "writer_status": "VALID" if capability_valid else ("HELD" if lease_active else "EXPIRED"),
        "bound_database_identity": bound_database,
        "current_database_identity": database_id,
        "generation": current.get("generation"),
        "lease_status": "ACTIVE" if lease_active else "EXPIRED",
        "conflict_reason": conflict,
    }


def capability_from_environment() -> AccountWriterCapability | None:
    names = (
        "V2_ACCOUNT_WRITER_SCOPE",
        "V2_ACCOUNT_WRITER_DB",
        "V2_ACCOUNT_WRITER_OWNER",
        "V2_ACCOUNT_WRITER_GENERATION",
        REGISTRY_ENV,
    )
    values = [os.environ.get(name, "").strip() for name in names]
    if not any(values):
        return None
    if not all(values):
        raise AccountWriterFenceError("ACCOUNT_WRITER_CAPABILITY_INCOMPLETE")
    return AccountWriterCapability(values[0], values[1], values[2], int(values[3]), values[4])


def capability_environment(capability: AccountWriterCapability) -> dict[str, str]:
    return {
        "V2_ACCOUNT_WRITER_SCOPE": capability.account_scope_key,
        "V2_ACCOUNT_WRITER_DB": capability.database_identity,
        "V2_ACCOUNT_WRITER_OWNER": capability.owner_id,
        "V2_ACCOUNT_WRITER_GENERATION": str(capability.generation),
        REGISTRY_ENV: capability.registry_identity,
    }
