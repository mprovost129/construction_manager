"""Save update photos as one batch, cleaning private files after a failed save."""

import logging
from contextlib import contextmanager

from django.db import transaction
from django.db.models import Max

from .models import ActivityEvent, DailyLogPhoto
from .services import record_activity

logger = logging.getLogger(__name__)


class PhotoUploadFormMixin:
    def post(self, request, *args, **kwargs):
        form = self.get_form()
        if not form.is_valid():
            return self.form_invalid(form)
        try:
            return self.form_valid(form)
        except OSError:
            logger.exception("Project update photo storage failed.")
            form.add_error(
                None,
                "The update could not be saved. Please select your photos again and retry.",
            )
            return self.form_invalid(form)


@contextmanager
def photo_batch():
    saved = []
    try:
        with transaction.atomic():
            yield saved
    except Exception:
        for field in saved:
            try:
                field.storage.delete(field.name)
            except Exception:
                logger.exception("Could not clean up a failed project photo upload.")
        raise


def save_update_photos(daily_log, uploads, user, saved):
    order = daily_log.photos.aggregate(last=Max("sort_order"))["last"]
    order = 0 if order is None else order + 1
    for index, upload in enumerate(uploads):
        photo = DailyLogPhoto(
            daily_log=daily_log,
            original_filename=upload.name,
            uploaded_by=user,
            sort_order=order + index,
        )
        photo.image.save(upload.name, upload, save=False)
        saved.append(photo.image)
        photo.full_clean()
        photo.save()
    if uploads:
        record_activity(
            organization=daily_log.project.organization,
            project=daily_log.project,
            actor=user,
            event_type=ActivityEvent.Type.DAILY_LOG_PHOTO_ADDED,
            summary=f'Added {len(uploads)} progress photos to "{daily_log.title}".',
            metadata={"daily_log_id": daily_log.pk, "photo_count": len(uploads)},
        )
