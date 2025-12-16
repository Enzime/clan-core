"""Tests for the Bitwarden secret store backend using rbw."""

import base64
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from clan_cli.vars.generator import Generator
from clan_cli.vars.secret_modules.bitwarden import SecretStore
from clan_cli.vars.var import Var
from clan_lib.errors import ClanError
from clan_lib.flake import Flake


class MockCompletedProcess:
    """Mock subprocess.CompletedProcess for rbw commands."""

    def __init__(
        self,
        stdout: bytes = b"",
        stderr: bytes = b"",
        returncode: int = 0,
    ) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


@pytest.fixture
def mock_flake(tmp_path: Path) -> Flake:
    """Create a mock flake for testing."""
    flake_path = tmp_path / "flake"
    flake_path.mkdir()
    (flake_path / "flake.nix").write_text("{}")
    return Flake(str(flake_path))


@pytest.fixture
def mock_generator(mock_flake: Flake) -> Generator:
    """Create a mock generator for testing."""
    return Generator(
        name="test_generator",
        share=False,
        machines=["test_machine"],
        _flake=mock_flake,
    )


@pytest.fixture
def mock_shared_generator(mock_flake: Flake) -> Generator:
    """Create a mock shared generator for testing."""
    return Generator(
        name="shared_generator",
        share=True,
        machines=["test_machine"],
        _flake=mock_flake,
    )


@pytest.fixture
def mock_var() -> Var:
    """Create a mock var for testing."""
    var = MagicMock(spec=Var)
    var.name = "test_secret"
    return var


class TestSecretStoreProperties:
    """Test basic properties of the SecretStore."""

    def test_store_name(self, mock_flake: Flake) -> None:
        store = SecretStore(mock_flake)
        assert store.store_name == "bitwarden"

    def test_is_secret_store(self, mock_flake: Flake) -> None:
        store = SecretStore(mock_flake)
        assert store.is_secret_store is True

    def test_entry_prefix(self, mock_flake: Flake) -> None:
        store = SecretStore(mock_flake)
        assert store.entry_prefix == "clan-vars"


class TestFolderPath:
    """Test folder path generation."""

    def test_folder_path_per_machine(
        self, mock_flake: Flake, mock_generator: Generator
    ) -> None:
        store = SecretStore(mock_flake)
        path = store._folder_path(mock_generator)
        assert path == "clan-vars/per-machine/test_machine/test_generator"

    def test_folder_path_shared(
        self, mock_flake: Flake, mock_shared_generator: Generator
    ) -> None:
        store = SecretStore(mock_flake)
        path = store._folder_path(mock_shared_generator)
        assert path == "clan-vars/shared/shared_generator"


class TestRbwAvailability:
    """Test rbw availability checking."""

    def test_rbw_not_available(self, mock_flake: Flake) -> None:
        store = SecretStore(mock_flake)
        with patch("shutil.which", return_value=None):
            with pytest.raises(ClanError) as exc_info:
                store._ensure_rbw_available()
            assert "rbw not found" in str(exc_info.value)

    def test_rbw_available(self, mock_flake: Flake) -> None:
        store = SecretStore(mock_flake)
        with patch("shutil.which", return_value="/usr/bin/rbw"):
            # Should not raise
            store._ensure_rbw_available()


class TestRunRbw:
    """Test rbw command execution."""

    def test_run_rbw_success(self, mock_flake: Flake) -> None:
        store = SecretStore(mock_flake)
        with (
            patch("shutil.which", return_value="/usr/bin/rbw"),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MockCompletedProcess(
                stdout=b"success", returncode=0
            )
            result = store._run_rbw("get", "test")
            assert result.stdout == b"success"
            mock_run.assert_called_once()

    def test_run_rbw_not_logged_in(self, mock_flake: Flake) -> None:
        store = SecretStore(mock_flake)
        with (
            patch("shutil.which", return_value="/usr/bin/rbw"),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MockCompletedProcess(
                stderr=b"not logged in", returncode=1
            )
            with pytest.raises(ClanError) as exc_info:
                store._run_rbw("get", "test")
            assert "not logged in" in str(exc_info.value)

    def test_run_rbw_agent_error(self, mock_flake: Flake) -> None:
        store = SecretStore(mock_flake)
        with (
            patch("shutil.which", return_value="/usr/bin/rbw"),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MockCompletedProcess(
                stderr=b"agent not running", returncode=1
            )
            with pytest.raises(ClanError) as exc_info:
                store._run_rbw("get", "test")
            assert "locked or not logged in" in str(exc_info.value)


class TestSecretOperations:
    """Test secret CRUD operations."""

    def test_exists_true(
        self, mock_flake: Flake, mock_generator: Generator
    ) -> None:
        store = SecretStore(mock_flake)
        with (
            patch("shutil.which", return_value="/usr/bin/rbw"),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MockCompletedProcess(
                stdout=b"secret_value", returncode=0
            )
            assert store.exists(mock_generator, "test_secret") is True

    def test_exists_false(
        self, mock_flake: Flake, mock_generator: Generator
    ) -> None:
        store = SecretStore(mock_flake)
        with (
            patch("shutil.which", return_value="/usr/bin/rbw"),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MockCompletedProcess(
                stderr=b"not found", returncode=1
            )
            assert store.exists(mock_generator, "test_secret") is False

    def test_get_secret(
        self, mock_flake: Flake, mock_generator: Generator
    ) -> None:
        store = SecretStore(mock_flake)
        secret_value = b"my_secret_data"
        encoded = base64.b64encode(secret_value).decode()

        with (
            patch("shutil.which", return_value="/usr/bin/rbw"),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MockCompletedProcess(
                stdout=f"{encoded}\n".encode(), returncode=0
            )
            result = store.get(mock_generator, "test_secret")
            assert result == secret_value

    def test_get_secret_not_found(
        self, mock_flake: Flake, mock_generator: Generator
    ) -> None:
        store = SecretStore(mock_flake)
        with (
            patch("shutil.which", return_value="/usr/bin/rbw"),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MockCompletedProcess(
                stderr=b"not found", returncode=1
            )
            with pytest.raises(ClanError) as exc_info:
                store.get(mock_generator, "test_secret")
            assert "not found" in str(exc_info.value)

    def test_get_secret_non_base64(
        self, mock_flake: Flake, mock_generator: Generator
    ) -> None:
        """Test getting a secret that's not base64 encoded (manual entry)."""
        store = SecretStore(mock_flake)
        with (
            patch("shutil.which", return_value="/usr/bin/rbw"),
            patch("subprocess.run") as mock_run,
        ):
            # Return non-base64 data (like a manually created password)
            mock_run.return_value = MockCompletedProcess(
                stdout=b"plain_password\n", returncode=0
            )
            result = store.get(mock_generator, "test_secret")
            # Should return as UTF-8 bytes
            assert result == b"plain_password"

    def test_set_new_secret(
        self,
        mock_flake: Flake,
        mock_generator: Generator,
        mock_var: Var,
    ) -> None:
        store = SecretStore(mock_flake)
        secret_value = b"new_secret_data"
        call_count = 0

        def mock_run_side_effect(*args: object, **kwargs: object) -> MockCompletedProcess:
            nonlocal call_count
            call_count += 1
            cmd = args[0]
            # First call: check exists (get)
            if "get" in cmd:
                return MockCompletedProcess(stderr=b"not found", returncode=1)
            # Second call: add
            if "add" in cmd:
                return MockCompletedProcess(returncode=0)
            return MockCompletedProcess(returncode=0)

        with (
            patch("shutil.which", return_value="/usr/bin/rbw"),
            patch("subprocess.run", side_effect=mock_run_side_effect),
        ):
            result = store._set(mock_generator, mock_var, secret_value, "test_machine")
            assert result is None  # Files managed outside git

    def test_set_existing_secret(
        self,
        mock_flake: Flake,
        mock_generator: Generator,
        mock_var: Var,
    ) -> None:
        store = SecretStore(mock_flake)
        secret_value = b"updated_secret"
        calls = []

        def mock_run_side_effect(*args: object, **kwargs: object) -> MockCompletedProcess:
            cmd = args[0]
            calls.append(cmd)
            # First call: check exists (get) - exists
            if "get" in cmd:
                return MockCompletedProcess(stdout=b"old_value", returncode=0)
            # Second call: rm (delete old)
            if "rm" in cmd:
                return MockCompletedProcess(returncode=0)
            # Third call: add (create new)
            if "add" in cmd:
                return MockCompletedProcess(returncode=0)
            return MockCompletedProcess(returncode=0)

        with (
            patch("shutil.which", return_value="/usr/bin/rbw"),
            patch("subprocess.run", side_effect=mock_run_side_effect),
        ):
            store._set(mock_generator, mock_var, secret_value, "test_machine")
            # Should have called rm to delete existing entry
            assert any("rm" in str(cmd) for cmd in calls)

    def test_delete_secret(
        self, mock_flake: Flake, mock_generator: Generator
    ) -> None:
        store = SecretStore(mock_flake)
        with (
            patch("shutil.which", return_value="/usr/bin/rbw"),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MockCompletedProcess(returncode=0)
            result = store.delete(mock_generator, "test_secret")
            assert list(result) == []
            # Verify rm was called with correct arguments
            call_args = mock_run.call_args[0][0]
            assert "rm" in call_args
            assert "test_secret" in call_args


class TestDeleteStore:
    """Test delete_store functionality."""

    def test_delete_store(self, mock_flake: Flake) -> None:
        store = SecretStore(mock_flake)
        calls = []

        def mock_run_side_effect(*args: object, **kwargs: object) -> MockCompletedProcess:
            cmd = args[0]
            calls.append(cmd)
            if "list" in cmd:
                # Return entries in the machine's folders
                return MockCompletedProcess(
                    stdout=b"clan-vars/per-machine/test_machine/gen1\tsecret1\n"
                    b"clan-vars/per-machine/test_machine/gen2\tsecret2\n"
                    b"clan-vars/per-machine/other_machine/gen1\tsecret3\n",
                    returncode=0,
                )
            return MockCompletedProcess(returncode=0)

        with (
            patch("shutil.which", return_value="/usr/bin/rbw"),
            patch("subprocess.run", side_effect=mock_run_side_effect),
        ):
            result = store.delete_store("test_machine")
            assert list(result) == []
            # Should have deleted secrets for test_machine but not other_machine
            rm_calls = [c for c in calls if "rm" in c]
            assert len(rm_calls) == 2  # Only 2 secrets for test_machine


class TestSync:
    """Test sync functionality."""

    def test_sync(self, mock_flake: Flake) -> None:
        store = SecretStore(mock_flake)
        with (
            patch("shutil.which", return_value="/usr/bin/rbw"),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MockCompletedProcess(returncode=0)
            store._sync()
            call_args = mock_run.call_args[0][0]
            assert "sync" in call_args


class TestBase64Encoding:
    """Test base64 encoding/decoding for binary data."""

    def test_binary_data_roundtrip(
        self,
        mock_flake: Flake,
        mock_generator: Generator,
        mock_var: Var,
    ) -> None:
        """Test that binary data is correctly encoded and decoded."""
        store = SecretStore(mock_flake)
        # Binary data with non-UTF8 bytes
        binary_data = b"\x00\x01\x02\xff\xfe\xfd"
        stored_value = None

        def mock_run_side_effect(*args: object, **kwargs: object) -> MockCompletedProcess:
            nonlocal stored_value
            cmd = args[0]
            input_data = kwargs.get("input")

            if "get" in cmd:
                if stored_value:
                    return MockCompletedProcess(
                        stdout=stored_value + b"\n", returncode=0
                    )
                return MockCompletedProcess(stderr=b"not found", returncode=1)
            if "add" in cmd and input_data:
                stored_value = input_data
                return MockCompletedProcess(returncode=0)
            return MockCompletedProcess(returncode=0)

        with (
            patch("shutil.which", return_value="/usr/bin/rbw"),
            patch("subprocess.run", side_effect=mock_run_side_effect),
        ):
            # Set the secret
            store._set(mock_generator, mock_var, binary_data, "test_machine")

            # Get the secret back
            result = store.get(mock_generator, mock_var.name)
            assert result == binary_data


class TestSharedGenerators:
    """Test handling of shared generators."""

    def test_shared_generator_folder_path(
        self, mock_flake: Flake, mock_shared_generator: Generator
    ) -> None:
        store = SecretStore(mock_flake)
        path = store._folder_path(mock_shared_generator)
        assert "shared" in path
        assert "per-machine" not in path

    def test_per_machine_generator_folder_path(
        self, mock_flake: Flake, mock_generator: Generator
    ) -> None:
        store = SecretStore(mock_flake)
        path = store._folder_path(mock_generator)
        assert "per-machine" in path
        assert "test_machine" in path
