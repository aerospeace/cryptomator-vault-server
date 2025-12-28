from pathlib import PurePosixPath

from app.adapters import DirEntry


def normalize_path(raw_path: str) -> str:
    if not raw_path:
        return "/"
    if not raw_path.startswith("/"):
        raw_path = f"/{raw_path}"
    normalized = str(PurePosixPath(raw_path))
    if ".." in PurePosixPath(normalized).parts:
        raise ValueError("Invalid path traversal")
    return normalized


def build_tree(entries_by_path: dict[str, list[DirEntry]], root: str = "/") -> dict:
    def build_node(path: str) -> dict:
        children = []
        for entry in entries_by_path.get(path, []):
            if entry.is_dir:
                children.append(build_node(entry.path))
        return {"path": path, "children": children}

    return build_node(root)


def flatten_tree(tree: dict) -> list[dict]:
    items = [tree]
    for child in tree.get("children", []):
        items.extend(flatten_tree(child))
    return items
