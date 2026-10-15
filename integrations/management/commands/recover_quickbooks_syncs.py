from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db.models.functions import Coalesce
from django.utils import timezone

from integrations.customer_sync import QuickBooksSyncError
from integrations.invoice_sync import execute_invoice_sync_attempt
from integrations.models import QuickBooksSyncAttempt
from integrations.payment_sync import execute_invoice_payment_sync_attempt


class Command(BaseCommand):
    help = "Recover interrupted invoice/payment syncs using their saved request identities."

    def add_arguments(self, parser):
        parser.add_argument("--stale-minutes", type=int, default=15)
        parser.add_argument("--limit", type=int, default=25)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        if options["stale_minutes"] < 1 or options["limit"] < 1:
            raise CommandError("Stale minutes and limit must be positive.")
        cutoff = timezone.now() - timedelta(minutes=options["stale_minutes"])
        candidates = list(
            QuickBooksSyncAttempt.objects.filter(
                status="running",
                entity_type__in=("invoice", "payment"),
            )
            .annotate(last_execution=Coalesce("last_execution_at", "started_at"))
            .filter(
                last_execution__lte=cutoff,
            )
            .order_by("last_execution")
            .values_list("pk", "entity_type")[: options["limit"]]
        )
        if options["dry_run"]:
            self.stdout.write(
                f"{len(candidates)} interrupted synchronization candidate(s)."
            )
            return
        succeeded = failed = skipped = 0
        for pk, entity_type in candidates:
            execute = (
                execute_invoice_sync_attempt
                if entity_type == "invoice"
                else execute_invoice_payment_sync_attempt
            )
            try:
                result = execute(pk, recovery_before=cutoff)
            except QuickBooksSyncError:
                if QuickBooksSyncAttempt.objects.filter(
                    pk=pk, status="failed"
                ).exists():
                    failed += 1
                else:
                    skipped += 1
                continue
            if result.status == "succeeded":
                succeeded += 1
            else:
                failed += 1
        self.stdout.write(
            f"Recovery complete: {succeeded} succeeded, {failed} need review, {skipped} skipped."
        )
