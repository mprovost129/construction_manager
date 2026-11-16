import os

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.files.storage import FileSystemStorage, Storage, storages
from django.utils.deconstruct import deconstructible
from django.utils.functional import cached_property


class LocalPrivateDocumentStorage(FileSystemStorage):
    @cached_property
    def base_location(self):
        return settings.PRIVATE_MEDIA_ROOT

    @cached_property
    def location(self):
        return os.path.abspath(self.base_location)

    @cached_property
    def base_url(self):
        return None


@deconstructible
class PrivateDocumentStorage(Storage):
    """Keep model/migration references stable while selecting private storage."""

    @property
    def backend(self):
        backend = settings.PRIVATE_STORAGE_BACKEND
        if backend == 'local':
            return LocalPrivateDocumentStorage()
        if backend == 'r2':
            return storages['private_documents']
        raise ImproperlyConfigured('Unsupported PRIVATE_STORAGE_BACKEND.')

    def _open(self, name, mode='rb'):
        return self.backend.open(name, mode)

    def _save(self, name, content):
        return self.backend.save(name, content)

    def delete(self, name):
        return self.backend.delete(name)

    def exists(self, name):
        return self.backend.exists(name)

    def size(self, name):
        return self.backend.size(name)

    def path(self, name):
        return self.backend.path(name)

    def listdir(self, path):
        return self.backend.listdir(path)

    def get_modified_time(self, name):
        return self.backend.get_modified_time(name)

    def url(self, name):
        raise ValueError('Private documents must be served through authorized views.')


private_document_storage = PrivateDocumentStorage()
