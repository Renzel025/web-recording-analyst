"""Video storage. Rows store a key (``p1p0/om_xxx.mp4``); the URL is resolved when a page renders."""

from functools import lru_cache
from typing import Optional

from app import config


class LocalStorage:
    def __init__(self, root):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, data: bytes, key: str) -> str:
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return key

    def url(self, key: str) -> str:
        return f"/media/{key}"


class OSSStorage:
    def __init__(self):
        import oss2  # optional dependency

        auth = oss2.Auth(config.OSS_ACCESS_KEY_ID, config.OSS_ACCESS_KEY_SECRET)
        self.bucket = oss2.Bucket(auth, config.OSS_ENDPOINT, config.OSS_BUCKET)

    def save(self, data: bytes, key: str) -> str:
        self.bucket.put_object(key, data, headers={"Content-Type": "video/mp4"})
        return key

    def url(self, key: str) -> str:
        # Private bucket: hand out a short-lived signed URL.
        return self.bucket.sign_url("GET", key, config.OSS_URL_EXPIRES, slash_safe=True)


@lru_cache(maxsize=1)
def get_storage():
    if config.STORAGE_BACKEND == "oss":
        return OSSStorage()
    return LocalStorage(config.LOCAL_STORAGE_DIR)


def video_url(key: Optional[str]) -> Optional[str]:
    if not key:
        return None
    if key.startswith(("http://", "https://")):
        return key
    return get_storage().url(key)
