"""In-memory S3 contract fake; real MaxIO interoperability has a separate test."""

from __future__ import annotations

import hashlib
import io

from nautionette_backend import output_storage


class MemoryS3:
    def __init__(self):
        self.objects = {}
        self.deleted = []
        self.unavailable = False

    def ready(self):
        if self.unavailable:
            raise output_storage.failure("output_storage_unavailable", "Object storage unavailable", 503)

    def put(self, key, data, size):
        self.ready()
        content = data if isinstance(data, bytes) else data.read()
        assert len(content) == size
        self.objects[key] = content

    def open(self, key):
        self.ready()
        if key not in self.objects:
            raise output_storage.failure("output_storage_unavailable", "Object not uploaded", 503)
        return io.BytesIO(self.objects[key]), len(self.objects[key])

    def copy(self, source, target):
        self.ready()
        self.objects[target] = self.objects[source]

    def download(self, key, target, size, sha256):
        return output_storage.S3Store.download(self, key, target, size, sha256)

    def bytes(self, key, size, sha256):
        return output_storage.S3Store.bytes(self, key, size, sha256)

    def upload_url(self, key, size, sha256, expires_in=600):
        return {
            "url": f"http://object-storage.test/{key}?private-upload=secret",
            "headers": {
                "Content-Length": str(size),
                "Content-Type": "application/octet-stream",
                "x-amz-checksum-sha256": hashlib.sha256(sha256.encode()).hexdigest(),
            },
        }

    def download_url(self, key):
        return f"http://object-storage.test/{key}?private-download=secret"

    def delete(self, key):
        self.ready()
        self.objects.pop(key, None)
        self.deleted.append(key)
