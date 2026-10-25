"""The per-install secret that signs the session cookie."""
import pathlib
import secrets


def load_or_create_secret_key(path: pathlib.Path) -> bytes:
    """Return the key at ``path``, generating and saving a new one on first run."""
    path = pathlib.Path(path)
    if path.exists():
        return path.read_bytes()
    path.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_bytes(32)
    path.write_bytes(key)
    return key
