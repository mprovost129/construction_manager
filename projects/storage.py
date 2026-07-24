import os

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.utils.functional import cached_property


class PrivateDocumentStorage(FileSystemStorage):
    @cached_property
    def base_location(self):
        return settings.PRIVATE_MEDIA_ROOT

    @cached_property
    def location(self):
        return os.path.abspath(self.base_location)

    @cached_property
    def base_url(self):
        return None


private_document_storage = PrivateDocumentStorage()
