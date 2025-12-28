# Cryptomator Vault Server

A small Flask application that exposes a web UI and API for browsing and downloading
files from Cryptomator vaults. The app can either use the Cryptomator CLI to mount a
vault or the `pycryptomator` adapter (when supported).

## Features

- Browse vault contents via the web UI.
- Download and upload files.
- Optional login-time index caching to speed up browsing.

## Requirements

- Python 3.11+
- Cryptomator CLI (when using the CLI adapter)
- A vaults configuration file

## Configuration

The server reads configuration from environment variables and a vaults YAML file.

### Vaults file

Create a YAML file (default location: `/config/vaults.yaml`) with the vaults you want
to expose:

```yaml
vaults:
  - id: personal
    path: /data/vaults/personal
  - id: work
    path: /data/vaults/work
```

### Environment variables

- `SECRET_KEY` (default: `dev-secret`): Flask session secret.
- `SESSION_TTL_SECONDS` (default: `1800`): session lifetime.
- `MAX_UPLOAD_MB` (default: `2048`): upload size limit.
- `ENABLE_LOGIN_INDEX_CACHE` (default: `true`): prebuild index on login.
- `INDEX_CACHE_MODE` (default: `recursive`): reserved for future use.
- `ADAPTER` (default: `python`): `python` or `cli`.
- `CRYPTOMATOR_CLI_PATH` (default: `/usr/bin/cryptomator-cli`): path to CLI.
- `UMOUNT_CLI_PATH` (default: `/usr/bin/umount`): unmount helper.
- `VAULT_MOUNT_ROOT` (default: `/tmp/mounts`): mount root for CLI adapter.
- `MOUNTER` (default: `org.cryptomator.frontend.fuse.mount.LinuxFuseMountProvider`):
  cryptomator mounter identifier.
- `VAULTS_CONFIG` (default: `/config/vaults.yaml`): vaults config location.

## Running locally (pip)

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e .
export FLASK_APP=app.main:create_app
flask run --host 0.0.0.0 --port 8000
```

## Running with Docker

```bash
docker build -t cryptomator-vault-server .
docker run --rm -p 8000:8000 \
  -e VAULTS_CONFIG=/config/vaults.yaml \
  -v /path/to/vaults.yaml:/config/vaults.yaml:ro \
  -v /path/to/vaults:/data/vaults:ro \
  cryptomator-vault-server
```

Visit `http://localhost:8000` and log in with the vault ID and passphrase.
