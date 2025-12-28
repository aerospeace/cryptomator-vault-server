import os
from dataclasses import asdict
from pathlib import Path
from typing import Any

from flask import Flask, Response, abort, flash, redirect, render_template, request, send_file, url_for

from app.adapters import CLIVaultAdapter, DirEntry, PyVaultAdapter, VaultAdapter, VaultAdapterError
from app.config import load_config
from app.rate_limit import RateLimiter
from app.session import SessionStore
from app.utils import build_tree, flatten_tree, normalize_path


def create_app() -> Flask:
    app = Flask(__name__)
    if "SECRET_KEY" in os.environ:
        app.secret_key = os.environ["SECRET_KEY"]
    config = load_config()
    app.config["MAX_CONTENT_LENGTH"] = config.max_upload_mb * 1024 * 1024

    session_store = SessionStore(secret_key=config.secret_key, ttl_seconds=config.session_ttl_seconds)
    rate_limiter = RateLimiter(max_attempts=5, window_seconds=300)

    def get_adapter(vault_path: str) -> VaultAdapter:
        if config.adapter == "cli":
            return CLIVaultAdapter(
                vault_path=Path(vault_path),
                cli_path=config.cryptomator_cli_path,
                mount_root=config.vault_mount_root,
                mounter=config.mounter,
                umount_cli_path=config.umount_cli_path,
            )
        return PyVaultAdapter(Path(vault_path))

    def load_session() -> tuple[Any, bool]:
        session = session_store.get(request.cookies.get("session"))
        return session, session is not None

    @app.before_request
    def enforce_session() -> None:
        if request.path in {"/login", "/logout"} or request.path.startswith("/static"):
            return
        session, ok = load_session()
        if not ok:
            return redirect(url_for("login"))
        request.session = session

    @app.get("/login")
    def login() -> str:
        return render_template("login.html", vaults=config.vaults.values())

    @app.post("/login")
    def login_post() -> Response:
        client_key = request.remote_addr or "unknown"
        if not rate_limiter.allow(client_key):
            flash("Too many login attempts. Try again later.", "error")
            return redirect(url_for("login"))

        vault_id = request.form.get("vault_id")
        passphrase = request.form.get("passphrase", "")
        vault = config.vaults.get(vault_id or "")
        if not vault:
            flash("Invalid vault selection", "error")
            return redirect(url_for("login"))

        adapter = get_adapter(str(vault.path))
        try:
            with adapter.open(passphrase) as root:
                entries = adapter.list_dir(root, "/")
        except VaultAdapterError as exc:
            flash(str(exc), "error")
            return redirect(url_for("login"))

        session = session_store.create()
        session.data.update(
            {
                "vault_id": vault.vault_id,
                "vault_path": str(vault.path),
                "passphrase": passphrase,
                "index": None,
            }
        )
        if config.enable_login_index_cache:
            session.data["index"] = build_index(adapter, passphrase, entries)

        response = redirect(url_for("browse"))
        response.set_cookie(
            "session",
            session_store.sign(session.session_id),
            httponly=True,
            secure=os.environ.get("COOKIE_SECURE", "false").lower() == "true",
        )
        return response

    @app.post("/logout")
    def logout() -> Response:
        session = session_store.get(request.cookies.get("session"))
        if session:
            session_store.destroy(session.session_id)
        response = redirect(url_for("login"))
        response.delete_cookie("session")
        return response

    @app.get("/")
    def browse() -> str:
        return browse_path("/")

    @app.get("/browse")
    def browse_path(path: str | None = None) -> str:
        session = request.session
        path = normalize_path(path or request.args.get("path", "/"))
        entries = list_entries(session, path)
        tree = None
        if session.data.get("index"):
            tree = build_tree_from_index(session.data["index"])
        return render_template(
            "browser.html",
            current_path=path,
            entries=entries,
            tree=tree,
        )

    @app.get("/hx/list")
    def hx_list() -> str:
        session = request.session
        path = normalize_path(request.args.get("path", "/"))
        entries = list_entries(session, path)
        return render_template("partials/file_list.html", current_path=path, entries=entries)

    @app.post("/hx/upload")
    def hx_upload() -> str:
        session = request.session
        path = normalize_path(request.args.get("path", "/"))
        upload = request.files.get("file")
        if not upload:
            return render_template(
                "partials/status.html",
                status="No file uploaded",
                status_level="error",
            )

        adapter = get_adapter(session.data["vault_path"])
        try:
            with adapter.open(session.data["passphrase"]) as root:
                target_path = f"{path.rstrip('/')}/{upload.filename}".replace("//", "/")
                adapter.write_file(root, target_path, upload.stream)
        except VaultAdapterError as exc:
            return render_template("partials/status.html", status=str(exc), status_level="error")

        update_index_after_upload(session, path, upload.filename)
        entries = list_entries(session, path)
        return render_template(
            "partials/upload_result.html",
            current_path=path,
            entries=entries,
            status="Upload complete",
            status_level="success",
        )

    @app.get("/download")
    def download() -> Response:
        session = request.session
        path = normalize_path(request.args.get("path", "/"))
        adapter = get_adapter(session.data["vault_path"])
        try:
            with adapter.open(session.data["passphrase"]) as root:
                handle = adapter.open_file(root, path)
                return send_file(
                    handle,
                    as_attachment=True,
                    download_name=path.split("/")[-1],
                )
        except VaultAdapterError as exc:
            abort(404, description=str(exc))

    @app.get("/api/v1/fs/list")
    def api_list() -> dict[str, Any]:
        session = request.session
        path = normalize_path(request.args.get("path", "/"))
        entries = list_entries(session, path)
        return {"path": path, "entries": [asdict(entry) for entry in entries]}

    return app


def list_entries(session: Any, path: str) -> list[DirEntry]:
    if session.data.get("index"):
        return session.data["index"].get(path, [])
    adapter = get_adapter_for_session(session)
    try:
        with adapter.open(session.data["passphrase"]) as root:
            return adapter.list_dir(root, path)
    except VaultAdapterError:
        return []


def build_index(adapter: VaultAdapter, passphrase: str, root_entries: list[DirEntry]) -> dict[str, list[DirEntry]]:
    index: dict[str, list[DirEntry]] = {"/": root_entries}
    stack = [entry for entry in root_entries if entry.is_dir]
    while stack:
        entry = stack.pop()
        with adapter.open(passphrase) as root:
            children = adapter.list_dir(root, entry.path)
        index[entry.path] = children
        stack.extend(child for child in children if child.is_dir)
    return index


def build_tree_from_index(index: dict[str, list[DirEntry]]) -> dict:
    return build_tree(index, "/")


def update_index_after_upload(session: Any, folder_path: str, filename: str) -> None:
    index = session.data.get("index")
    if not index:
        return
    new_entry = DirEntry(name=filename, path=f"{folder_path.rstrip('/')}/{filename}", is_dir=False, size=None)
    entries = index.setdefault(folder_path, [])
    entries.append(new_entry)
    entries.sort(key=lambda item: (not item.is_dir, item.name.lower()))


def get_adapter_for_session(session: Any) -> VaultAdapter:
    config = load_config()
    if config.adapter == "cli":
        return CLIVaultAdapter(
            vault_path=Path(session.data["vault_path"]),
            cli_path=config.cryptomator_cli_path,
            mount_root=config.vault_mount_root,
            mounter=config.mounter,
            umount_cli_path=config.umount_cli_path,
        )
    return PyVaultAdapter(Path(session.data["vault_path"]))

if __name__ == "__main__":
    flask_app = create_app()
    flask_app.run(host="0.0.0.0", port=8000)