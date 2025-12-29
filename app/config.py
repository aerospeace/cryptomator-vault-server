import os
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class VaultConfig:
    vault_id: str
    path: Path


@dataclass(frozen=True)
class AppConfig:
    secret_key: str
    session_ttl_seconds: int
    max_upload_mb: int
    enable_login_index_cache: bool
    index_cache_mode: str
    adapter: str
    cryptomator_cli_path: str
    umount_cli_path: str
    vault_mount_root: Path
    vaults: dict[str, VaultConfig]
    mounter: str

def _load_vaults_config(path: Path) -> dict[str, VaultConfig]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    vaults = {}
    for entry in payload.get("vaults", []):
        vault_id = entry.get("id")
        vault_path = entry.get("path")
        if not vault_id or not vault_path:
            continue
        vaults[vault_id] = VaultConfig(vault_id=vault_id, path=Path(vault_path))
    return vaults


def load_config() -> AppConfig:
    vaults_config_path = Path(os.environ.get("VAULTS_CONFIG", "/config/vaults.yaml"))
    vaults = _load_vaults_config(vaults_config_path)
    return AppConfig(
        secret_key=os.environ.get("SECRET_KEY", "dev-secret"),
        session_ttl_seconds=int(os.environ.get("SESSION_TTL_SECONDS", "1800")),
        max_upload_mb=int(os.environ.get("MAX_UPLOAD_MB", "2048")),
        enable_login_index_cache=os.environ.get("ENABLE_LOGIN_INDEX_CACHE", "true").lower()
        == "true",
        index_cache_mode=os.environ.get("INDEX_CACHE_MODE", "recursive"),
        adapter=os.environ.get("ADAPTER", "cli").lower(),
        cryptomator_cli_path=os.environ.get("CRYPTOMATOR_CLI_PATH", "/usr/bin/cryptomator-cli"),
        umount_cli_path=os.environ.get("UMOUNT_CLI_PATH", "/usr/bin/umount"), # not yet a windows equivalent tested
        vault_mount_root=Path(os.environ.get("VAULT_MOUNT_ROOT", "/tmp/mounts")),
        vaults=vaults,
        mounter=os.environ.get("MOUNTER", "org.cryptomator.frontend.fuse.mount.LinuxFuseMountProvider")
    )
