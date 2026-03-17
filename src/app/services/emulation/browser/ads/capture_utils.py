import hashlib
from urllib.parse import urlparse

ASSET_CONTENT_TYPES = frozenset({
    "text/css", "text/javascript", "application/javascript",
    "application/x-javascript",
})
IMAGE_PREFIX = "image/"

_CT_TO_EXT = {
    "text/css": ".css",
    "text/javascript": ".js",
    "application/javascript": ".js",
    "application/x-javascript": ".js",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/svg+xml": ".svg",
    "image/webp": ".webp",
}


def ext_from_content_type(ct: str) -> str:
    return _CT_TO_EXT.get(ct, ".bin")


def asset_filename(url: str, content_type: str) -> str:
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    if path:
        name = path.split("/")[-1]
        if "." in name and len(name) < 120:
            return name

    ext = ext_from_content_type(content_type)
    url_hash = hashlib.md5(url.encode(), usedforsecurity=False).hexdigest()[:10]
    return f"asset_{url_hash}{ext}"
