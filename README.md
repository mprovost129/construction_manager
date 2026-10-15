# Construction Manager

Construction Manager is a Django application for residential builders and their clients. It concentrates the project workflows the initial pilot company uses instead of reproducing every Buildertrend module.

## Current capabilities

- Company roles, project assignments, invitations, and scoped access
- Portfolio dashboard, Action center, activity history, and CSV export
- Project messages and email notifications
- Private documents, client uploads, file versions, and approvals
- Estimates, cost-coded line items, tax, revisions, and client approval
- Finish selections, allowances, option details, packages, overages, and credits
- Change orders, approval history, revisions, invoice and credit-memo handoff
- Invoices, PDF downloads, payment records, and client-safe balances
- Project financial summaries and internal job costing
- Internal milestone schedule
- QuickBooks OAuth, customer and item mappings, invoice sync, and payment import
- Stripe organization subscriptions with staged enforcement

See [the implementation roadmap](docs/implementation_roadmap.md) for the exact delivered status, known gaps, and production gates. See [the first-time user guide](docs/user_guide.md) for the staff and client workflow.

## Local setup with Docker

1. Copy `.env.example` to `.env` and replace the development placeholders.
2. Change `DB_HOST` in `.env` to `db` when Django runs through Docker Compose.
3. Start the services.

```bash
docker compose up --build
```

The container entrypoint runs migrations and creates the environment-backed Django superuser when `DJANGO_SUPERUSER_EMAIL` and `DJANGO_SUPERUSER_PASSWORD` are set.

Create the first company, company administrator, and optional sample project:

```bash
docker compose exec web python manage.py bootstrap_company \
  --company-name "Example Builders" \
  --admin-email "admin@example.com" \
  --project-name "Sample Project"
```

Set `BOOTSTRAP_ADMIN_PASSWORD` before running the command if the administrator does not already exist. Without it, a new administrator receives an unusable password and must use the password-reset workflow.

Open `http://localhost:8000` after the services start.

## Local setup without Docker

Construction Manager requires Python 3.13 or newer, PostgreSQL, and Redis for the normal development configuration.

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
.venv/bin/python manage.py migrate
.venv/bin/python manage.py runserver
```

Configure the PostgreSQL, Redis, email, QuickBooks, and Stripe values in `.env` for the services you plan to exercise.

## Validation

The test settings use SQLite in memory. Base settings still require placeholder secret and database environment values during import.

```bash
SECRET_KEY=test-key \
DB_NAME=test \
DB_USER=test \
DB_PASSWORD=test \
.venv/bin/python -m pytest -q

.venv/bin/ruff check .
```

Check for model changes and verify static collection before release:

```bash
SECRET_KEY=test-key DB_NAME=test DB_USER=test DB_PASSWORD=test \
.venv/bin/python manage.py makemigrations --check --dry-run \
  --settings=config.settings.test

.venv/bin/python manage.py collectstatic --noinput \
  --settings=config.settings.build
```

## Deployment status

The application is suitable for controlled evaluation, not an unqualified production launch. Persistent private file storage, backups and restore testing, email delivery, monitoring, legal configuration, two-factor authentication, and live QuickBooks and Stripe acceptance remain release gates. Do not store live client records until the applicable gates in the implementation roadmap are complete.
