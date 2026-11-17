"""Import reviewed Buildertrend history without manufacturing portal decisions.

The private staging file is deliberately supplied at runtime, never bundled with
source code. Imports are atomic, organization-scoped and repeatable. Native
notifications, billing and approval services are intentionally not invoked.
"""
import hashlib
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q

from projects.models import (
    ActivityEvent,
    ChangeOrder,
    FinishSelection,
    Organization,
    OrganizationMembership,
    Project,
    SelectionPackage,
)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def amount(value):
    try:
        result = Decimal(str(value))
        if not result.is_finite() or abs(result) >= Decimal('10000000000'):
            raise ValueError
        if result != result.quantize(Decimal('.01')):
            raise ValueError
        return result
    except (InvalidOperation, ValueError):
        raise CommandError('Invalid or out-of-range source amount.') from None


def category_title(raw):
    # OCR sometimes reads the location label as the category; retain the raw
    # text in provenance rather than inventing a category from that label.
    title = (raw or '').strip().rstrip(' _—').strip()
    if not title or title.lower().startswith('location:'):
        return 'Imported selections: category needs review'
    return title


def validate_payload(data):
    if data.get('schema_version') != 1 or not data.get('projects'):
        raise CommandError('Expected a reviewed version 1 staging file.')
    for flag in ('send_invitations', 'send_notifications', 'create_invoices_or_payments'):
        if data.get(flag) is not False:
            raise CommandError('History imports must disable invitations, notifications and billing.')
    source_keys, codes = set(), set()
    for project in data['projects']:
        key, code = project['staging_key'], project['proposed_code']
        if not key or not code or key in source_keys or code.casefold() in codes:
            raise CommandError('Duplicate or missing project identity.')
        source_keys.add(key)
        codes.add(code.casefold())
        if project['contract_amount'] is not None or project['status'] != 'active':
            raise CommandError('This importer requires active projects with unknown base contracts.')
        co_keys, co_numbers, selection_keys, selection_numbers = set(), set(), set(), set()
        for co in project['change_orders']:
            if co['status'] != 'approved' or co['approval_actor_verified'] is not False:
                raise CommandError('Only historical approvals without verified portal actors are supported.')
            if co['staging_key'] in co_keys or co['number'] in co_numbers or co['number'] < 1:
                raise CommandError('Duplicate or invalid change-order identity.')
            co_keys.add(co['staging_key'])
            co_numbers.add(co['number'])
            amount(co['total_price'])
            amount(co['builder_cost'])
        for item in project['selections']:
            if item['source_status'] != 'Selected' or item['selected_markers_in_ocr'] != 1:
                raise CommandError('Every imported selection must have one source Selected marker.')
            if (item['staging_key'] in selection_keys
                    or item['source_ordinal'] in selection_numbers or item['source_ordinal'] < 1):
                raise CommandError('Duplicate or invalid selection identity.')
            selection_keys.add(item['staging_key'])
            selection_numbers.add(item['source_ordinal'])
            amount(item['source_selection_total'])
        expected = project['reconciliation']
        actual = (
            len(co_keys), sum((amount(c['total_price']) for c in project['change_orders']), Decimal(0)),
            sum((amount(c['builder_cost']) for c in project['change_orders']), Decimal(0)),
            len(selection_keys), sum((amount(s['source_selection_total']) for s in project['selections']), Decimal(0)),
        )
        if actual != expected_totals(expected):
            raise CommandError('Source counts or signed amounts do not reconcile.')


def expected_totals(expected):
    return (
        expected['approved_change_order_count'], amount(expected['approved_change_order_price']),
        amount(expected['approved_change_order_cost']), expected['selection_count'],
        amount(expected['source_printed_selection_net']),
    )


def reconcile(project, source):
    orders = list(project.change_orders.filter(status=ChangeOrder.Status.IMPORTED))
    selections = list(project.finish_selections.filter(status=FinishSelection.Status.IMPORTED))
    actual = (len(orders), sum((c.price_delta for c in orders), Decimal(0)),
              sum((c.cost_delta for c in orders), Decimal(0)), len(selections),
              sum((s.imported_total for s in selections), Decimal(0)))
    if actual != expected_totals(source['reconciliation']):
        raise CommandError('Stored history does not reconcile; no changes committed.')
    if {c.import_source.get('source_key') for c in orders} != {c['staging_key'] for c in source['change_orders']}:
        raise CommandError('Stored change-order identities differ from the source.')
    if {s.import_source.get('source_key') for s in selections} != {s['staging_key'] for s in source['selections']}:
        raise CommandError('Stored selection identities differ from the source.')
    return actual


def save_validated(instance):
    instance.full_clean()
    instance.save()
    return instance


class Command(BaseCommand):
    help = 'Validate or atomically import a private, reviewed Buildertrend staging JSON file.'

    def add_arguments(self, parser):
        parser.add_argument('--file', required=True)
        parser.add_argument('--organization', required=True)
        parser.add_argument('--actor', required=True, help='Existing active company administrator email.')
        parser.add_argument('--apply', action='store_true', help='Commit; default validates then rolls back.')

    def handle(self, *args, **options):
        try:
            data = json.loads(Path(options['file']).read_text())
            validate_payload(data)
            with transaction.atomic():
                organization = Organization.objects.select_for_update().get(slug=options['organization'])
                membership = OrganizationMembership.objects.select_related('user').get(
                    organization=organization, user__email__iexact=options['actor'],
                    user__is_active=True, is_active=True, role=OrganizationMembership.Role.ADMIN,
                )
                for source in data['projects']:
                    project = self.import_project(organization, membership.user, source, data['sources'])
                    totals = reconcile(project, source)
                    self.stdout.write(f'{project.code}: {totals[0]} orders, price {totals[1]:.2f}; '
                                      f'{totals[3]} selections, signed net {totals[4]:.2f}')
                if not options['apply']:
                    transaction.set_rollback(True)
            self.stdout.write('Import committed.' if options['apply'] else 'Dry run passed; no records saved.')
        except (KeyError, TypeError, ValueError, ValidationError,
                Organization.DoesNotExist, OrganizationMembership.DoesNotExist) as exc:
            # Avoid echoing private record contents into monitoring/log transports.
            raise CommandError(f'Import validation failed ({type(exc).__name__}); no records committed.') from None

    def import_project(self, organization, actor, source, sources):
        source_hash = fingerprint(source)
        existing = Project.objects.filter(organization=organization,
                                          import_source__source_key=source['staging_key']).first()
        if existing:
            if existing.import_source.get('payload_sha256') != source_hash:
                raise CommandError('Previously imported source changed; manual reconciliation required.')
            return existing
        if Project.objects.filter(organization=organization).filter(
            Q(code__iexact=source['proposed_code']) | Q(name__iexact=source['name'])
        ).exists():
            raise CommandError('An existing project uses this name or code; refusing to overwrite it.')
        address = ', '.join(str(value) for value in source['site_address'].values() if value)
        contact = source['client']
        managers = ', '.join(pm['name'] for pm in source['project_managers'])
        project = save_validated(Project(
            organization=organization, name=source['name'].strip(), code=source['proposed_code'],
            status=Project.Status.ACTIVE, created_by=actor, contract_amount=None,
            description=(f'Imported from Buildertrend.\nSite: {address}\n'
                         f'Source client: {contact["name"]} ({contact["email"]}).\n'
                         f'Source project managers: {managers}. Account assignments require confirmation.\n'
                         'Original contract amount, invoicing and payment history were not supplied.'),
            import_source={'source_key': source['staging_key'], 'payload_sha256': source_hash,
                           'jobsite_row': source['source_jobsite_row'], 'sources': sources},
        ))
        for co in source['change_orders']:
            provenance = dict(co)
            provenance['source_key'] = provenance.pop('staging_key')
            save_validated(ChangeOrder(
                project=project, number=co['number'], title=co['title'],
                description=co['description'] or 'Approved in Buildertrend. Detailed scope was not included in the export.',
                price_delta=amount(co['total_price']), cost_delta=amount(co['builder_cost']),
                status=ChangeOrder.Status.IMPORTED, created_by=actor, import_source=provenance,
            ))
        packages = {}
        for item in source['selections']:
            title = category_title(item['category_ocr'])
            if title.casefold() not in packages:
                packages[title.casefold()] = save_validated(SelectionPackage(
                    project=project, title=title, created_by=actor,
                    description='Historical Buildertrend selections. Verify extracted specifications against the original PDF.',
                ))
            provenance = {key: value for key, value in item.items() if key != 'source_text_ocr'}
            provenance['source_key'] = provenance.pop('staging_key')
            provenance['source_file'] = item['source_pdf']
            save_validated(FinishSelection(
                project=project, package=packages[title.casefold()], number=item['source_ordinal'],
                title=item['title'], location=item['location_ocr'] or '',
                description=item['source_text_ocr'], imported_total=amount(item['source_selection_total']),
                status=FinishSelection.Status.IMPORTED, created_by=actor, import_source=provenance,
            ))
        save_validated(ActivityEvent(
            organization=organization, project=project, actor=actor,
            event_type=ActivityEvent.Type.HISTORY_IMPORTED,
            summary='Imported Buildertrend history; no new approvals, invitations, invoices or payments.',
            metadata={'payload_sha256': source_hash, 'reconciliation': source['reconciliation']},
        ))
        return project
