import hashlib
from dataclasses import dataclass

from cvopt.errors import InputError


@dataclass(frozen=True)
class UserContext:
    user_id: str  # normalized email: stripped, lower-case
    user_key: str  # sha256(user_id).hexdigest()[:16]

    @classmethod
    def from_email(cls, email: str) -> "UserContext":
        user_id = email.strip().lower()
        local, _, domain = user_id.partition("@")
        if not local or not domain or " " in user_id:
            raise InputError(f"Not a valid email address: {email!r}")
        return cls(user_id, hashlib.sha256(user_id.encode()).hexdigest()[:16])
