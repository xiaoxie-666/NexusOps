import base64
import hashlib
import hmac
import secrets
from typing import Annotated, Callable

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.entities import UserAccount


_PBKDF2_ITERATIONS = 310_000


def hash_password(password: str) -> str:
    """Hash a password using salted PBKDF2-SHA256."""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        _PBKDF2_ITERATIONS,
        base64.urlsafe_b64encode(salt).decode("ascii").rstrip("="),
        base64.urlsafe_b64encode(digest).decode("ascii").rstrip("="),
    )


def verify_password(password: str, hashed: str) -> bool:
    """Verify new PBKDF2 hashes and legacy SHA-256 hashes during migration."""
    if hashed.startswith("pbkdf2_sha256$"):
        try:
            _, iterations, salt_text, digest_text = hashed.split("$", 3)
            padding = "=" * (-len(salt_text) % 4)
            salt = base64.urlsafe_b64decode(salt_text + padding)
            padding = "=" * (-len(digest_text) % 4)
            expected = base64.urlsafe_b64decode(digest_text + padding)
            actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iterations))
            return hmac.compare_digest(actual, expected)
        except (ValueError, TypeError):
            return False
    # Backward compatibility with pre-PBKDF2 accounts; a password reset upgrades them.
    legacy = hashlib.sha256(password.encode("utf-8")).hexdigest()
    return hmac.compare_digest(legacy, hashed)


def _credentials(request: Request) -> tuple[str, str]:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("basic "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing Basic authorization", headers={"WWW-Authenticate": "Basic"})
    try:
        decoded = base64.b64decode(header.split(" ", 1)[1], validate=True).decode("utf-8")
        username, password = decoded.split(":", 1)
        if not username:
            raise ValueError("empty username")
        return username, password
    except Exception as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid Basic authorization", headers={"WWW-Authenticate": "Basic"}) from exc


def current_user(request: Request, db: Annotated[Session, Depends(get_db)]) -> UserAccount:
    username, password = _credentials(request)
    user = db.query(UserAccount).filter(UserAccount.username == username).first()
    if user is None or not verify_password(password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Bad credentials", headers={"WWW-Authenticate": "Basic"})
    return user


def require_roles(*allowed_roles: str) -> Callable:
    """Create a FastAPI dependency allowing any one of the supplied roles."""
    allowed = frozenset(allowed_roles)

    def dependency(user: Annotated[UserAccount, Depends(current_user)]) -> UserAccount:
        if not allowed.intersection(set(user.roles)):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient role permissions")
        return user

    return dependency


def require_admin(user: Annotated[UserAccount, Depends(current_user)]) -> UserAccount:
    if "ROLE_ADMIN" not in user.roles:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin role required")
    return user


def require_incident_operator(user: Annotated[UserAccount, Depends(current_user)]) -> UserAccount:
    allowed = {"ROLE_ADMIN", "ROLE_ENGINEER", "ROLE_ONCALL"}
    if not allowed.intersection(set(user.roles)):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Incident operator role required")
    return user


def require_incident_editor(user: Annotated[UserAccount, Depends(current_user)]) -> UserAccount:
    allowed = {"ROLE_ADMIN", "ROLE_ENGINEER"}
    if not allowed.intersection(set(user.roles)):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Incident editor role required")
    return user


def require_approval_reviewer(user: Annotated[UserAccount, Depends(current_user)]) -> UserAccount:
    if not {"ROLE_ADMIN", "ROLE_ONCALL"}.intersection(set(user.roles)):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Approval reviewer role required")
    return user
