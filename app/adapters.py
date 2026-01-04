import abc
import os
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Iterator
import importlib.util
import importlib
from io import BytesIO

from app.process import start_background_process

@dataclass(frozen=True)
class DirEntry:
    name: str
    path: str
    is_dir: bool
    size: int | None


class VaultAdapterError(RuntimeError):
    pass


class VaultAdapter(abc.ABC):
    def __init__(self, vault_path: Path) -> None:
        self.vault_path = vault_path

    @abc.abstractmethod
    @contextmanager
    def open(self, passphrase: str) -> Iterator[Path]:
        raise NotImplementedError

    def list_dir(self, root: Path, relative_path: str) -> list[DirEntry]:
        target = root / relative_path.lstrip("/")
        if not target.exists():
            raise VaultAdapterError(f"Path does not exist: {relative_path}")
        entries: list[DirEntry] = []
        for entry in sorted(target.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
            entries.append(
                DirEntry(
                    name=entry.name,
                    path=f"/{relative_path.strip('/')}/{entry.name}".replace("//", "/"),
                    is_dir=entry.is_dir(),
                    size=entry.stat().st_size if entry.is_file() else None,
                )
            )
        return entries

    def build_index(self, passphrase: str) -> dict[str, list[DirEntry]]:
        with self.open(passphrase) as root:
            root_entries = self.list_dir(root, "/")
            index: dict[str, list[DirEntry]] = {"/": root_entries}
            stack = [entry for entry in root_entries if entry.is_dir]
            while stack:
                entry = stack.pop()
                children = self.list_dir(root, entry.path)
                index[entry.path] = children
                stack.extend(child for child in children if child.is_dir)
            return index

    def open_file(self, root: Path, relative_path: str) -> BinaryIO:
        target = root / relative_path.lstrip("/")
        if not target.is_file():
            raise VaultAdapterError(f"File not found: {relative_path}")
        with open(target, "rb") as f:
            file_data = BytesIO(f.read())
        return file_data

    def write_file(self, root: Path, relative_path: str, stream: BinaryIO) -> None:
        target = root / relative_path.lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("wb") as handle:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                handle.write(chunk)

    def make_dir(self, root: Path, relative_path: str) -> None:
        target = root / relative_path.lstrip("/")
        target.mkdir(parents=True, exist_ok=False)

    def move_file(self, root: Path, source_path: str, destination_dir: str) -> None:
        source = root / source_path.lstrip("/")
        if not source.is_file():
            raise VaultAdapterError(f"File not found: {source_path}")
        dest_dir = root / destination_dir.lstrip("/")
        if not dest_dir.is_dir():
            raise VaultAdapterError(f"Destination is not a directory: {destination_dir}")
        destination = dest_dir / source.name
        os.replace(source, destination)

    def move_entry(self, root: Path, source_path: str, destination_dir: str) -> None:
        source = root / source_path.lstrip("/")
        if not source.exists():
            raise VaultAdapterError(f"Path not found: {source_path}")
        dest_dir = root / destination_dir.lstrip("/")
        if not dest_dir.is_dir():
            raise VaultAdapterError(f"Destination is not a directory: {destination_dir}")
        destination = dest_dir / source.name
        if destination.exists():
            raise VaultAdapterError(f"Destination already exists: {destination_dir}/{source.name}")
        shutil.move(str(source), str(destination))

    def copy_entry(self, root: Path, source_path: str, destination_dir: str) -> None:
        source = root / source_path.lstrip("/")
        if not source.exists():
            raise VaultAdapterError(f"Path not found: {source_path}")
        dest_dir = root / destination_dir.lstrip("/")
        if not dest_dir.is_dir():
            raise VaultAdapterError(f"Destination is not a directory: {destination_dir}")
        destination = dest_dir / source.name
        if destination.exists():
            raise VaultAdapterError(f"Destination already exists: {destination_dir}/{source.name}")
        if source.is_dir():
            shutil.copytree(source, destination)
        else:
            shutil.copy2(source, destination)

    def delete_entry(self, root: Path, source_path: str) -> None:
        source = root / source_path.lstrip("/")
        if not source.exists():
            raise VaultAdapterError(f"Path not found: {source_path}")
        if source.is_dir():
            shutil.rmtree(source)
        else:
            source.unlink()


class PyVaultAdapter(VaultAdapter):
    def __init__(self, vault_path: Path) -> None:
        super().__init__(vault_path)
        self._module = None

    def _load_module(self) -> None:
        if self._module is not None:
            return
        if importlib.util.find_spec("pycryptomator") is None:
            raise VaultAdapterError("pycryptomator is not installed")
        self._module = importlib.import_module("pycryptomator")

    @contextmanager
    def open(self, passphrase: str) -> Iterator[Path]:
        self._load_module()
        raise VaultAdapterError("pycryptomator adapter is not implemented for this vault format yet")


class CLIVaultAdapter(VaultAdapter):
    def __init__(self, vault_path: Path, cli_path: str, mount_root: Path, mounter: str, umount_cli_path: str) -> None:
        super().__init__(vault_path)
        self.cli_path = cli_path
        self.mount_root = mount_root
        self.mounter = mounter
        self.umount_cli_path = umount_cli_path
        
    @contextmanager
    def open(self, passphrase: str) -> Iterator[Path]:
        self.mount_root.mkdir(parents=True, exist_ok=True)
        mount_dir = Path(tempfile.mkdtemp(prefix="vault-", dir=self.mount_root))
        try:
            self._mount(passphrase, mount_dir)
            yield mount_dir
        finally:
            self._unmount(mount_dir)

    def _mount(self, passphrase: str, mount_dir: Path) -> None:
        os.environ["CRYPTOMATOR_PASSWORD"] = passphrase
        cmd = [self.cli_path, "unlock",
               "--mountPoint", str(mount_dir),
               "--password:env", "CRYPTOMATOR_PASSWORD",
               "--mounter", self.mounter,
               str(self.vault_path)]
        result = start_background_process(cmd)        
        return result
        # result = subprocess.run(
        #     cmd,
        #     check=False,
        #     capture_output=True,
        #     text=True,
        # )
        # if result.returncode != 0:
        #     raise VaultAdapterError(result.stderr.strip() or "Failed to unlock vault")

    def _unmount(self, mount_dir: Path) -> None:
        subprocess.run(
            [self.umount_cli_path, str(mount_dir)],
            check=False,
            capture_output=True,
            text=True,
        )
        try:
            for child in mount_dir.iterdir():
                if child.is_file():
                    child.unlink(missing_ok=True)
        finally:
            mount_dir.rmdir()
