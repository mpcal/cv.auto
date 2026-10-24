import hashlib

import pytest

from cvopt.context import UserContext
from cvopt.errors import InputError


def test_email_is_normalized_and_keyed() -> None:
    u = UserContext.from_email("  Alice@Example.COM ")
    assert u.user_id == "alice@example.com"
    assert u.user_key == hashlib.sha256(b"alice@example.com").hexdigest()[:16]


@pytest.mark.parametrize("bad", ["", "alice", "@example.com", "alice@", "a b@example.com"])
def test_invalid_email(bad: str) -> None:
    with pytest.raises(InputError):
        UserContext.from_email(bad)
