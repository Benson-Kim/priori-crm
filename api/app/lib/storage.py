"""
Object storage service abstraction.
Currently backed by local filesystem but designed to support S3-compatible APIs later.
"""
import os
import shutil
from typing import BinaryIO

from fastapi import UploadFile

from app.lib.config import settings

class StorageService:
    """Service to handle file uploads and downloads."""

    def __init__(self, base_dir: str = "uploads"):
        self.base_dir = base_dir

    def upload_file(self, file_obj: BinaryIO, directory: str, filename: str) -> str:
        """
        Upload a file to storage and return the storage key (path).
        """
        upload_dir = os.path.join(self.base_dir, directory)
        os.makedirs(upload_dir, exist_ok=True)
        storage_key = os.path.join(upload_dir, filename)

        with open(storage_key, "wb") as buffer:
            shutil.copyfileobj(file_obj, buffer)

        return storage_key

    def delete_file(self, storage_key: str) -> bool:
        """
        Delete a file from storage. Returns True if successfully deleted or already absent.
        """
        if not os.path.exists(storage_key):
            return True
        try:
            os.remove(storage_key)
            return True
        except OSError:
            return False

    def file_exists(self, storage_key: str) -> bool:
        """Check if file exists in storage."""
        return os.path.exists(storage_key)


storage_service = StorageService()
