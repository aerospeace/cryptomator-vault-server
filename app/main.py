import os
from dataclasses import asdict
from typing import Any

from flask import Flask, Response, abort, flash, redirect, render_template, request, send_file, url_for

from app.adapters import CLIVaultAdapter, DirEntry, PyVaultAdapter, VaultAdapter, VaultAdapterError
from app.config import load_config
from app.rate_limit import RateLimiter
from app.session import SessionStore
from app.utils import build_breadcrumbs, build_tree, flatten_tree, normalize_path


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

    @app.context_processor
    def inject_vault_name() -> dict[str, str | None]:
        session = getattr(request, "session", None)
        vault_name = None
        if session:
            vault_name = session.data.get("vault_id")
        return {"vault_name": vault_name}

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
            index = None
            if config.enable_login_index_cache:
                index = adapter.build_index(passphrase)
            else:
                with adapter.open(passphrase) as root:
                    adapter.list_dir(root, "/")
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
            session.data["index"] = index

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
            breadcrumbs=build_breadcrumbs(path),
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
            return render_template("partials/status.html", status="No file uploaded", status_level="error")

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
            "partials/action_result.html",
            current_path=path,
            entries=entries,
            status="Upload complete",
            status_level="success",
        )

    @app.post("/hx/mkdir")
    def hx_mkdir() -> str:
        session = request.session
        path = normalize_path(request.args.get("path", "/"))
        folder_name = (request.form.get("folder_name") or "").strip()
        if not folder_name:
            return render_template("partials/status.html", status="Folder name is required", status_level="error")
        if "/" in folder_name or folder_name in {".", ".."}:
            return render_template(
                "partials/status.html",
                status="Folder name must be a single directory name",
                status_level="error",
            )

        adapter = get_adapter(session.data["vault_path"])
        new_folder_path = f"{path.rstrip('/')}/{folder_name}".replace("//", "/")
        try:
            with adapter.open(session.data["passphrase"]) as root:
                adapter.make_dir(root, new_folder_path)
        except VaultAdapterError as exc:
            return render_template("partials/status.html", status=str(exc), status_level="error")
        except FileExistsError:
            return render_template("partials/status.html", status="Folder already exists", status_level="error")

        update_index_after_mkdir(session, path, folder_name)
        entries = list_entries(session, path)
        return render_template(
            "partials/action_result.html",
            current_path=path,
            entries=entries,
            status="Folder created",
            status_level="success",
        )

    @app.post("/hx/move")
    def hx_move() -> str:
        session = request.session
        current_path = normalize_path(request.args.get("path", "/"))
        destination_input = (request.form.get("destination_dir") or "").strip()
        selected_paths = request.form.getlist("selected_paths")
        if not selected_paths or not destination_input:
            return render_template(
                "partials/status.html",
                status="Select at least one item and a destination folder",
                status_level="error",
            )

        destination_dir = normalize_path(
            destination_input if destination_input.startswith("/") else f"{current_path.rstrip('/')}/{destination_input}"
        )
        sources = [
            normalize_path(path if path.startswith("/") else f"{current_path.rstrip('/')}/{path}")
            for path in selected_paths
        ]

        adapter = get_adapter(session.data["vault_path"])
        try:
            with adapter.open(session.data["passphrase"]) as root:
                for source_path in sources:
                    adapter.move_entry(root, source_path, destination_dir)
        except VaultAdapterError as exc:
            return render_template("partials/status.html", status=str(exc), status_level="error")

        refresh_index(session)
        entries = list_entries(session, current_path)
        return render_template(
            "partials/action_result.html",
            current_path=current_path,
            entries=entries,
            status="Items moved",
            status_level="success",
        )

    @app.post("/hx/copy")
    def hx_copy() -> str:
        session = request.session
        current_path = normalize_path(request.args.get("path", "/"))
        destination_input = (request.form.get("destination_dir") or "").strip()
        selected_paths = request.form.getlist("selected_paths")
        if not selected_paths or not destination_input:
            return render_template(
                "partials/status.html",
                status="Select at least one item and a destination folder",
                status_level="error",
            )

        destination_dir = normalize_path(
            destination_input if destination_input.startswith("/") else f"{current_path.rstrip('/')}/{destination_input}"
        )
        sources = [
            normalize_path(path if path.startswith("/") else f"{current_path.rstrip('/')}/{path}")
            for path in selected_paths
        ]

        adapter = get_adapter(session.data["vault_path"])
        try:
            with adapter.open(session.data["passphrase"]) as root:
                for source_path in sources:
                    adapter.copy_entry(root, source_path, destination_dir)
        except VaultAdapterError as exc:
            return render_template("partials/status.html", status=str(exc), status_level="error")

        refresh_index(session)
        entries = list_entries(session, current_path)
        return render_template(
            "partials/action_result.html",
            current_path=current_path,
            entries=entries,
            status="Items copied",
            status_level="success",
        )

    @app.post("/hx/delete")
    def hx_delete() -> str:
        session = request.session
        current_path = normalize_path(request.args.get("path", "/"))
        selected_paths = request.form.getlist("selected_paths")
        if not selected_paths:
            return render_template(
                "partials/status.html", status="Select at least one item to delete", status_level="error"
            )

        sources = [
            normalize_path(path if path.startswith("/") else f"{current_path.rstrip('/')}/{path}")
            for path in selected_paths
        ]
        adapter = get_adapter(session.data["vault_path"])
        try:
            with adapter.open(session.data["passphrase"]) as root:
                for source_path in sources:
                    adapter.delete_entry(root, source_path)
        except VaultAdapterError as exc:
            return render_template("partials/status.html", status=str(exc), status_level="error")

        refresh_index(session)
        entries = list_entries(session, current_path)
        return render_template(
            "partials/action_result.html",
            current_path=current_path,
            entries=entries,
            status="Items deleted",
            status_level="success",
        )

    @app.get("/hx/picker")
    def hx_picker() -> str:
        session = request.session
        path = normalize_path(request.args.get("path", "/"))
        entries = list_entries(session, path)
        directories = [entry for entry in entries if entry.is_dir]
        return render_template(
            "partials/destination_picker.html",
            current_path=path,
            breadcrumbs=build_breadcrumbs(path),
            directories=directories,
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


def update_index_after_mkdir(session: Any, folder_path: str, folder_name: str) -> None:
    index = session.data.get("index")
    if not index:
        return
    new_entry = DirEntry(name=folder_name, path=f"{folder_path.rstrip('/')}/{folder_name}", is_dir=True, size=None)
    entries = index.setdefault(folder_path, [])
    entries.append(new_entry)
    entries.sort(key=lambda item: (not item.is_dir, item.name.lower()))
    index.setdefault(new_entry.path, [])


def refresh_index(session: Any) -> None:
    if not session.data.get("index"):
        return
    adapter = get_adapter_for_session(session)
    try:
        session.data["index"] = adapter.build_index(session.data["passphrase"])
    except VaultAdapterError:
        session.data["index"] = None


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
