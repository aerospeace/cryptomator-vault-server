import abc
import os
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Iterator
import importlib.util
import importlib


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

    def open_file(self, root: Path, relative_path: str) -> BinaryIO:
        target = root / relative_path.lstrip("/")
        if not target.is_file():
            raise VaultAdapterError(f"File not found: {relative_path}")
        return target.open("rb")

    def write_file(self, root: Path, relative_path: str, stream: BinaryIO) -> None:
        target = root / relative_path.lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("wb") as handle:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                handle.write(chunk)


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
    def __init__(self, vault_path: Path, cli_path: str, mount_root: Path, mounter: str) -> None:
        super().__init__(vault_path)
        self.cli_path = cli_path
        self.mount_root = mount_root
        self.mounter = mounter
        
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
        result = subprocess.run(
            [
                self.cli_path,
                "unlock",
                "--mountPoint", str(mount_dir),
                "--password:env", "CRYPTOMATOR_PASSWORD",
                "--mounter", self.mounter,
                str(self.vault_path),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise VaultAdapterError(result.stderr.strip() or "Failed to unlock vault")

    def _unmount(self, mount_dir: Path) -> None:
        subprocess.run(
            [self.cli_path, "lock", "--mount-point", str(mount_dir)],
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
