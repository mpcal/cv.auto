from pathlib import Path

import pytest

from cvopt.adapters.files import LocalFileStore
from cvopt.context import UserContext
from cvopt.errors import InputError


def test_runs_accept_owned_name_prefix_and_absolute_path(tmp_path: Path) -> None:
    files = LocalFileStore(tmp_path)
    user = UserContext.from_email("alice@example.com")
    run = files.new_run_dir(user, "owned-run")
    for ref in ("owned-run", "owned", str(run)):
        assert files.run_dir(user, ref) == run.resolve()


@pytest.mark.parametrize("kind", ["absolute", "traversal", "symlink", "nested"])
def test_runs_reject_paths_outside_owned_direct_children(tmp_path: Path, kind: str) -> None:
    files = LocalFileStore(tmp_path)
    alice = UserContext.from_email("alice@example.com")
    bob = UserContext.from_email("bob@example.com")
    foreign = files.new_run_dir(bob, "foreign")
    owned = files.new_run_dir(alice, "owned")
    (owned / "nested").mkdir()
    (owned.parent / "escape").symlink_to(foreign, target_is_directory=True)
    refs = {
        "absolute": str(foreign),
        "traversal": "../runs/owned",
        "symlink": "escape",
        "nested": str(owned / "nested"),
    }
    with pytest.raises(InputError):
        files.run_dir(alice, refs[kind])


@pytest.mark.parametrize("boundary", ["user", "runs"])
@pytest.mark.parametrize("destination", ["other-user", "outside"])
@pytest.mark.parametrize("operation", ["resolve", "create", "discover"])
def test_redirected_roots_rejected_before_run_access(
    tmp_path: Path, boundary: str, destination: str, operation: str
) -> None:
    from cvopt.services.discovery import RunDiscoveryService

    files = LocalFileStore(tmp_path / "data")
    alice = UserContext.from_email("alice@example.com")
    bob = UserContext.from_email("bob@example.com")
    private = files.new_run_dir(bob, "private")
    external = tmp_path / "external"
    external.mkdir()
    (external / "private").mkdir()
    home = files.user_dir(alice)
    target = private.parent if destination == "other-user" else external
    link = home / "runs"
    if boundary == "user":
        home.rmdir()
        link = home
        target = private.parent.parent if destination == "other-user" else external
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(InputError):
        if operation == "resolve":
            files.run_dir(alice, "private")
        elif operation == "create":
            files.new_run_dir(alice, "new")
        else:
            RunDiscoveryService(files).list(alice)
    assert not (target / "new").exists()
    assert not (target / "runs" / "new").exists()


def test_configured_data_root_symlink_remains_supported(tmp_path: Path) -> None:
    actual = tmp_path / "actual"
    actual.mkdir()
    link = tmp_path / "configured"
    link.symlink_to(actual, target_is_directory=True)
    files = LocalFileStore(link)
    user = UserContext.from_email("alice@example.com")
    run = files.new_run_dir(user, "owned")
    assert files.run_dir(user, "owned") == run
