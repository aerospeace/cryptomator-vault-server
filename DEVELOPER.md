# Developer Guide

## Project layout

- `app/main.py`: Flask app factory, routes, and request handlers.
- `app/adapters.py`: Vault adapter abstractions and implementations.
- `app/config.py`: Configuration loading from environment variables and YAML.
- `app/session.py`: Session management for logins.
- `app/rate_limit.py`: Simple in-memory rate limiting for login attempts.
- `app/utils.py`: Path normalization and tree/index helpers for the UI.
- `app/process.py`: Background process helper for CLI mounting.
- `app/templates/`: Jinja templates for the UI.
- `requirements.txt`: Pinned runtime dependencies (Docker build uses this).
- `pyproject.toml`: Packaging metadata for pip-based installs.

## Runtime flow (high level)

1. `create_app` in `app/main.py` initializes config, session store, and routes.
2. Login verifies the vault passphrase using the configured adapter.
3. When enabled, the adapter builds an in-memory index during login to speed up browsing.
4. Browsing routes use the cached index or query the adapter directly.

## Adapter responsibilities

- `VaultAdapter.open(passphrase)`: mount/unlock and yield a root path.
- `VaultAdapter.list_dir(root, path)`: list directory entries.
- `VaultAdapter.build_index(passphrase)`: open once, traverse, and return a full index.

## Common tasks

### Run with Flask

```bash
export FLASK_APP=app.main:create_app
flask run --host 0.0.0.0 --port 8000
```

### Run with Gunicorn

```bash
gunicorn --bind 0.0.0.0:8000 app.main:create_app
```

### Configuration reference

See `app/config.py` for available environment variables and defaults.
