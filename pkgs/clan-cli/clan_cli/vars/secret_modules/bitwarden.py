import io
import json
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
    """Bitwarden secret store backend for clan vars.

    This backend stores secrets in Bitwarden using the Bitwarden CLI (bw).
    Secrets are stored as secure notes with custom fields, organized by
    a folder structure that mirrors the vars hierarchy.

    Requirements:
    - Bitwarden CLI (bw) must be installed and available in PATH
    - User must be logged in (`bw login`) and have their vault unlocked
    - BW_SESSION environment variable must be set with the session key

    Storage structure in Bitwarden:
    - Folder: clan-vars/per-machine/<machine>/<generator>
    - Item name: <var-name>
    - Item type: Secure Note
    - Content stored in notes field (base64 encoded for binary data)
    """

    @property
    def is_secret_store(self) -> bool:
        return True

    def __init__(self, flake: Flake) -> None:
        super().__init__(flake)
        self.entry_prefix = "clan-vars"
        self._organization_id: str | None = None

    @property
    def store_name(self) -> str:
        return "bitwarden"

    def _ensure_bw_available(self) -> None:
        """Check if Bitwarden CLI is available."""
        if not shutil.which("bw"):
            msg = "Bitwarden CLI (bw) not found in PATH. Please install it first."
            raise ClanError(msg)

    def _run_bw(
        self,
        *args: str,
        input: bytes | None = None,  # noqa: A002
        check: bool = True,
    ) -> subprocess.CompletedProcess[bytes]:
        """Run a Bitwarden CLI command."""
        self._ensure_bw_available()
        cmd = ["bw", *args]
        result = subprocess.run(
            cmd,
            input=input,
            capture_output=True,
            check=False,
        )
        if check and result.returncode != 0:
            stderr = result.stderr.decode() if result.stderr else ""
            if "not logged in" in stderr.lower() or "vault is locked" in stderr.lower():
                msg = (
                    "Bitwarden vault is locked or not logged in. "
                    "Please run 'bw login' and 'bw unlock', then set BW_SESSION environment variable."
                )
                raise ClanError(msg)
            msg = f"Bitwarden command failed: {' '.join(cmd)}\n{stderr}"
            raise ClanError(msg)
        return result

    def _sync(self) -> None:
        """Sync the local Bitwarden cache with the server."""
        self._run_bw("sync")

    def _get_or_create_folder(self, folder_path: str) -> str:
        """Get or create a folder in Bitwarden, returning its ID.

        Args:
            folder_path: Path like "clan-vars/per-machine/myhost/mygen"

        Returns:
            The folder ID
        """
        # List existing folders
        result = self._run_bw("list", "folders")
        folders = json.loads(result.stdout.decode())

        # Check if folder exists
        for folder in folders:
            if folder.get("name") == folder_path:
                return folder["id"]

        # Create folder
        folder_data = json.dumps({"name": folder_path})
        encoded = subprocess.run(
            ["bw", "encode"],
            input=folder_data.encode(),
            capture_output=True,
            check=True,
        ).stdout.decode().strip()

        result = self._run_bw("create", "folder", encoded)
        created_folder = json.loads(result.stdout.decode())
        return created_folder["id"]

    def _get_item(self, generator: Generator, name: str) -> dict | None:
        """Get a Bitwarden item by generator and var name.

        Returns the item dict or None if not found.
        """
        folder_path = self._folder_path(generator)
        item_name = name

        # Search for the item
        result = self._run_bw("list", "items", "--search", item_name, check=False)
        if result.returncode != 0:
            return None

        items = json.loads(result.stdout.decode())

        # Find item with matching name in correct folder
        result = self._run_bw("list", "folders")
        folders = json.loads(result.stdout.decode())
        folder_id = None
        for folder in folders:
            if folder.get("name") == folder_path:
                folder_id = folder["id"]
                break

        if folder_id is None:
            return None

        for item in items:
            if item.get("name") == item_name and item.get("folderId") == folder_id:
                return item

        return None

    def _folder_path(self, generator: Generator) -> str:
        """Get the Bitwarden folder path for a generator."""
        if generator.share:
            return f"{self.entry_prefix}/shared/{generator.name}"
        machine = self.get_machine(generator)
        return f"{self.entry_prefix}/per-machine/{machine}/{generator.name}"

    def _set(
        self,
        generator: Generator,
        var: Var,
        value: bytes,
        machine: str,  # noqa: ARG002
    ) -> Path | None:
        """Store a secret in Bitwarden."""
        folder_path = self._folder_path(generator)
        folder_id = self._get_or_create_folder(folder_path)

        # Check if item already exists
        existing_item = self._get_item(generator, var.name)

        # Encode binary data as base64 for safe storage
        import base64

        # Store raw bytes, encode to base64 string for JSON storage
        encoded_value = base64.b64encode(value).decode("ascii")

        if existing_item:
            # Update existing item
            existing_item["notes"] = encoded_value
            existing_item["fields"] = [
                {"name": "encoding", "value": "base64", "type": 0},
            ]
            item_json = json.dumps(existing_item)
            encoded = subprocess.run(
                ["bw", "encode"],
                input=item_json.encode(),
                capture_output=True,
                check=True,
            ).stdout.decode().strip()
            self._run_bw("edit", "item", existing_item["id"], encoded)
        else:
            # Create new secure note
            # Type 2 = Secure Note
            item_data = {
                "type": 2,
                "secureNote": {"type": 0},
                "name": var.name,
                "notes": encoded_value,
                "folderId": folder_id,
                "fields": [
                    {"name": "encoding", "value": "base64", "type": 0},
                ],
            }
            item_json = json.dumps(item_data)
            encoded = subprocess.run(
                ["bw", "encode"],
                input=item_json.encode(),
                capture_output=True,
                check=True,
            ).stdout.decode().strip()
            self._run_bw("create", "item", encoded)

        return None  # Files managed outside git repo

    def get(self, generator: Generator, name: str) -> bytes:
        """Retrieve a secret from Bitwarden."""
        import base64

        item = self._get_item(generator, name)
        if item is None:
            msg = f"Secret not found: {generator.name}/{name}"
            raise ClanError(msg)

        notes = item.get("notes", "")

        # Check if base64 encoded
        fields = item.get("fields", [])
        is_base64 = any(
            f.get("name") == "encoding" and f.get("value") == "base64" for f in fields
        )

        if is_base64:
            return base64.b64decode(notes)
        return notes.encode("utf-8")

    def exists(self, generator: Generator, name: str) -> bool:
        """Check if a secret exists in Bitwarden."""
        return self._get_item(generator, name) is not None

    def delete(self, generator: Generator, name: str) -> Iterable[Path]:
        """Delete a secret from Bitwarden."""
        item = self._get_item(generator, name)
        if item:
            self._run_bw("delete", "item", item["id"])
        return []

    def delete_store(self, machine: str) -> Iterable[Path]:
        """Delete all secrets for a machine."""
        folder_prefix = f"{self.entry_prefix}/per-machine/{machine}/"

        # Get all folders for this machine
        result = self._run_bw("list", "folders")
        folders = json.loads(result.stdout.decode())

        folder_ids_to_delete = []
        for folder in folders:
            if folder.get("name", "").startswith(folder_prefix):
                folder_ids_to_delete.append(folder["id"])

        # Delete all items in these folders
        for folder_id in folder_ids_to_delete:
            result = self._run_bw("list", "items", "--folderid", folder_id)
            items = json.loads(result.stdout.decode())
            for item in items:
                self._run_bw("delete", "item", item["id"])

        # Delete the folders
        for folder_id in folder_ids_to_delete:
            self._run_bw("delete", "folder", folder_id)

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

        # Include sync timestamp
        result = self._run_bw("sync", check=False)

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
