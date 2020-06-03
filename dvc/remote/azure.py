import logging
import os
import posixpath
import threading
from datetime import datetime, timedelta

from funcy import cached_property, wrap_prop

from dvc.path_info import CloudURLInfo
from dvc.progress import Tqdm
from dvc.remote.base import BaseRemote, BaseRemoteTree
from dvc.scheme import Schemes

logger = logging.getLogger(__name__)


class AzureRemoteTree(BaseRemoteTree):
    @property
    def blob_service(self):
        return self.remote.blob_service

    def _generate_download_url(self, path_info, expires=3600):
        from azure.storage.blob import BlobPermissions

        expires_at = datetime.utcnow() + timedelta(seconds=expires)

        sas_token = self.blob_service.generate_blob_shared_access_signature(
            path_info.bucket,
            path_info.path,
            permission=BlobPermissions.READ,
            expiry=expires_at,
        )
        download_url = self.blob_service.make_blob_url(
            path_info.bucket, path_info.path, sas_token=sas_token
        )
        return download_url

    def exists(self, path_info):
        paths = self.remote.list_paths(path_info.bucket, path_info.path)
        return any(path_info.path == path for path in paths)

    def remove(self, path_info):
        if path_info.scheme != self.scheme:
            raise NotImplementedError

        logger.debug(f"Removing {path_info}")
        self.blob_service.delete_blob(path_info.bucket, path_info.path)


class AzureRemote(BaseRemote):
    scheme = Schemes.AZURE
    path_cls = CloudURLInfo
    REQUIRES = {"azure-storage-blob": "azure.storage.blob"}
    PARAM_CHECKSUM = "etag"
    COPY_POLL_SECONDS = 5
    LIST_OBJECT_PAGE_SIZE = 5000
    TREE_CLS = AzureRemoteTree

    def __init__(self, repo, config):
        super().__init__(repo, config)

        url = config.get("url", "azure://")
        self.path_info = self.path_cls(url)

        if not self.path_info.bucket:
            container = os.getenv("AZURE_STORAGE_CONTAINER_NAME")
            self.path_info = self.path_cls(f"azure://{container}")

        self.connection_string = config.get("connection_string") or os.getenv(
            "AZURE_STORAGE_CONNECTION_STRING"
        )

    @wrap_prop(threading.Lock())
    @cached_property
    def blob_service(self):
        from azure.storage.blob import BlockBlobService
        from azure.common import AzureMissingResourceHttpError

        logger.debug(f"URL {self.path_info}")
        logger.debug(f"Connection string {self.connection_string}")
        blob_service = BlockBlobService(
            connection_string=self.connection_string
        )
        logger.debug(f"Container name {self.path_info.bucket}")
        try:  # verify that container exists
            blob_service.list_blobs(
                self.path_info.bucket, delimiter="/", num_results=1
            )
        except AzureMissingResourceHttpError:
            blob_service.create_container(self.path_info.bucket)
        return blob_service

    def get_etag(self, path_info):
        etag = self.blob_service.get_blob_properties(
            path_info.bucket, path_info.path
        ).properties.etag
        return etag.strip('"')

    def get_file_checksum(self, path_info):
        return self.get_etag(path_info)

    def list_paths(self, bucket, prefix, progress_callback=None):
        blob_service = self.blob_service
        next_marker = None
        while True:
            blobs = blob_service.list_blobs(
                bucket, prefix=prefix, marker=next_marker
            )

            for blob in blobs:
                if progress_callback:
                    progress_callback()
                yield blob.name

            if not blobs.next_marker:
                break

            next_marker = blobs.next_marker

    def list_cache_paths(self, prefix=None, progress_callback=None):
        if prefix:
            prefix = posixpath.join(
                self.path_info.path, prefix[:2], prefix[2:]
            )
        else:
            prefix = self.path_info.path
        return self.list_paths(
            self.path_info.bucket, prefix, progress_callback
        )

    def _upload(
        self, from_file, to_info, name=None, no_progress_bar=False, **_kwargs
    ):
        with Tqdm(desc=name, disable=no_progress_bar, bytes=True) as pbar:
            self.blob_service.create_blob_from_path(
                to_info.bucket,
                to_info.path,
                from_file,
                progress_callback=pbar.update_to,
            )

    def _download(
        self, from_info, to_file, name=None, no_progress_bar=False, **_kwargs
    ):
        with Tqdm(desc=name, disable=no_progress_bar, bytes=True) as pbar:
            self.blob_service.get_blob_to_path(
                from_info.bucket,
                from_info.path,
                to_file,
                progress_callback=pbar.update_to,
            )
