import base64
import io
import logging
import shutil
import subprocess
import tarfile
from collections.abc import Iterable
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import override

from clan_cli.vars._types import StoreBase
from clan_cli.vars.generator import Generator, Var
from clan_lib.cmd import Log, RunOpts
from clan_lib.errors import ClanError
from clan_lib.flake import Flake
from clan_lib.ssh.host import Host
from clan_lib.ssh.upload import upload

log = logging.getLogger(__name__)


class SecretStore(StoreBase):
    """Bitwarden secret store backend for clan vars using rbw.

    This backend stores secrets in Bitwarden using rbw (unofficial Bitwarden CLI).
    Secrets are stored as password entries organized by folder structure.

    Requirements:
    - rbw must be installed and available in PATH
    - rbw must be configured and logged in (`rbw config` and `rbw login`)
    - rbw-agent must be running (`rbw unlock`)

    Storage structure in Bitwarden:
    - Folder: clan-vars/per-machine/<machine>/<generator>
    - Entry name: <var-name>
    - Content stored as password (base64 encoded for binary safety)
    """

    @property
    def is_secret_store(self) -> bool:
        return True

    def __init__(self, flake: Flake) -> None:
        super().__init__(flake)
        self.entry_prefix = "clan-vars"

    @property
    def store_name(self) -> str:
        return "bitwarden"

    def _ensure_rbw_available(self) -> None:
        """Check if rbw is available."""
        if not shutil.which("rbw"):
            msg = "rbw not found in PATH. Please install it first: https://github.com/doy/rbw"
            raise ClanError(msg)

    def _run_rbw(
        self,
        *args: str,
        input: bytes | None = None,  # noqa: A002
        check: bool = True,
    ) -> subprocess.CompletedProcess[bytes]:
        """Run an rbw command."""
        self._ensure_rbw_available()
        cmd = ["rbw", *args]
        result = subprocess.run(
            cmd,
            input=input,
            capture_output=True,
            check=False,
        )
        if check and result.returncode != 0:
            stderr = result.stderr.decode() if result.stderr else ""
            stdout = result.stdout.decode() if result.stdout else ""
            if "not logged in" in stderr.lower() or "agent" in stderr.lower():
                msg = (
                    "rbw vault is locked or not logged in. "
                    "Please run 'rbw login' and 'rbw unlock' first."
                )
                raise ClanError(msg)
            msg = f"rbw command failed: {' '.join(cmd)}\nstderr: {stderr}\nstdout: {stdout}"
            raise ClanError(msg)
        return result

    def _sync(self) -> None:
        """Sync the local rbw cache with the server."""
        self._run_rbw("sync")

    def _folder_path(self, generator: Generator) -> str:
        """Get the Bitwarden folder path for a generator."""
        if generator.share:
            return f"{self.entry_prefix}/shared/{generator.name}"
        machine = self.get_machine(generator)
        return f"{self.entry_prefix}/per-machine/{machine}/{generator.name}"

    def _entry_name(self, generator: Generator, name: str) -> str:
        """Get the full entry identifier for rbw (folder/name format)."""
        folder = self._folder_path(generator)
        return f"{folder}/{name}"

    def _set(
        self,
        generator: Generator,
        var: Var,
        value: bytes,
        machine: str,  # noqa: ARG002
    ) -> Path | None:
        """Store a secret in Bitwarden via rbw."""
        folder = self._folder_path(generator)

        # Base64 encode for safe storage of binary data
        encoded_value = base64.b64encode(value).decode("ascii")

        # Check if entry already exists
        if self.exists(generator, var.name):
            # Remove existing entry first (rbw doesn't have an update command)
            self._run_rbw("rm", var.name, "--folder", folder, check=False)

        # Add new entry with the secret as password
        # rbw add takes password from stdin
        self._run_rbw(
            "add",
            "--folder", folder,
            var.name,
            input=encoded_value.encode(),
        )

        return None  # Files managed outside git repo

    def get(self, generator: Generator, name: str) -> bytes:
        """Retrieve a secret from Bitwarden via rbw."""
        folder = self._folder_path(generator)

        result = self._run_rbw("get", "--folder", folder, name, check=False)

        if result.returncode != 0:
            msg = f"Secret not found: {generator.name}/{name}"
            raise ClanError(msg)

        # Decode from base64
        encoded_value = result.stdout.decode().strip()
        try:
            return base64.b64decode(encoded_value)
        except Exception:
            # If not base64 encoded, return as-is (for backwards compat or manual entries)
            return encoded_value.encode("utf-8")

    def exists(self, generator: Generator, name: str) -> bool:
        """Check if a secret exists in Bitwarden."""
        folder = self._folder_path(generator)
        result = self._run_rbw("get", "--folder", folder, name, check=False)
        return result.returncode == 0

    def delete(self, generator: Generator, name: str) -> Iterable[Path]:
        """Delete a secret from Bitwarden."""
        folder = self._folder_path(generator)
        self._run_rbw("rm", name, "--folder", folder, check=False)
        return []

    def delete_store(self, machine: str) -> Iterable[Path]:
        """Delete all secrets for a machine.

        Note: rbw doesn't have a bulk delete by folder, so we list and delete individually.
        """
        folder_prefix = f"{self.entry_prefix}/per-machine/{machine}/"

        # List all entries and filter by folder prefix
        result = self._run_rbw("list", "--fields", "folder,name", check=False)
        if result.returncode != 0:
            return []

        # Parse output and delete matching entries
        for line in result.stdout.decode().splitlines():
            if "\t" in line:
                entry_folder, entry_name = line.split("\t", 1)
                if entry_folder.startswith(folder_prefix):
                    self._run_rbw("rm", entry_name, "--folder", entry_folder, check=False)

        return []

    def generate_hash(self, machine: str) -> bytes:
        """Generate a hash to track if secrets need to be uploaded."""
        generators = Generator.get_machine_generators([machine], self.flake)
        manifest = [
            f"{generator.name}/{file.name}".encode()
            for generator in generators
            for file in generator.files
            if file.secret
        ]

        # Sync to ensure we have latest data
        self._run_rbw("sync", check=False)

        return b"\n".join(sorted(manifest))

    def needs_upload(self, machine: str, host: Host) -> bool:
        """Check if secrets need to be uploaded to the target machine."""
        local_hash = self.generate_hash(machine)
        if not local_hash:
            return True

        secret_location = self.flake.select_machine(
            machine, "config.clan.core.vars.bitwarden.secretLocation"
        )
        remote_hash = host.run(
            ["cat", f"{secret_location}/.bw_info"],
            RunOpts(log=Log.STDERR, check=False),
        ).stdout.strip()

        if not remote_hash:
            return True

        return local_hash != remote_hash.encode()

    def populate_dir(self, machine: str, output_dir: Path, phases: list[str]) -> None:
        """Populate a directory with secrets for deployment."""
        vars_generators = Generator.get_machine_generators([machine], self.flake)

        if "users" in phases:
            with tarfile.open(
                output_dir / "secrets_for_users.tar.gz",
                "w:gz",
            ) as user_tar:
                for generator in vars_generators:
                    for file in generator.files:
                        if not file.deploy:
                            continue
                        if not file.secret:
                            continue
                        tar_file = tarfile.TarInfo(name=f"{generator.name}/{file.name}")
                        content = self.get(generator, file.name)
                        tar_file.size = len(content)
                        tar_file.mode = file.mode
                        user_tar.addfile(tarinfo=tar_file, fileobj=io.BytesIO(content))

        if "services" in phases:
            with tarfile.open(output_dir / "secrets.tar.gz", "w:gz") as tar:
                for generator in vars_generators:
                    dir_exists = False
                    for file in generator.files:
                        if not file.deploy:
                            continue
                        if not file.secret:
                            continue
                        if not dir_exists:
                            tar_dir = tarfile.TarInfo(name=generator.name)
                            tar_dir.type = tarfile.DIRTYPE
                            tar_dir.mode = 0o511
                            tar.addfile(tarinfo=tar_dir)
                            dir_exists = True
                        tar_file = tarfile.TarInfo(name=f"{generator.name}/{file.name}")
                        content = self.get(generator, file.name)
                        tar_file.size = len(content)
                        tar_file.mode = file.mode
                        tar_file.uname = file.owner
                        tar_file.gname = file.group
                        tar.addfile(tarinfo=tar_file, fileobj=io.BytesIO(content))

        if "activation" in phases:
            for generator in vars_generators:
                for file in generator.files:
                    if file.needed_for == "activation":
                        out_file = (
                            output_dir / "activation" / generator.name / file.name
                        )
                        out_file.parent.mkdir(parents=True, exist_ok=True)
                        out_file.write_bytes(file.value)

        if "partitioning" in phases:
            for generator in vars_generators:
                for file in generator.files:
                    if file.needed_for == "partitioning":
                        out_file = (
                            output_dir / "partitioning" / generator.name / file.name
                        )
                        out_file.parent.mkdir(parents=True, exist_ok=True)
                        out_file.write_bytes(file.value)

        hash_data = self.generate_hash(machine)
        if hash_data:
            (output_dir / ".bw_info").write_bytes(hash_data)

    @override
    def get_upload_directory(self, machine: str) -> str:
        """Return the target directory on the remote machine for secrets."""
        return self.flake.select_machine(
            machine,
            "config.clan.core.vars.bitwarden.secretLocation",
        )

    def upload(self, machine: str, host: Host, phases: list[str]) -> None:
        """Upload secrets to a remote machine."""
        if "partitioning" in phases:
            msg = "Cannot upload partitioning secrets"
            raise NotImplementedError(msg)

        if not self.needs_upload(machine, host):
            log.info("Secrets already uploaded")
            return

        with TemporaryDirectory(prefix="vars-upload-") as _tempdir:
            bw_dir = Path(_tempdir).resolve()
            self.populate_dir(machine, bw_dir, phases)
            upload(host, bw_dir, Path(self.get_upload_directory(machine)))
