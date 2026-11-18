# Heritage 360 backup restore drill — November 18, 2026

## Result

**Passed:** recovery of the current document snapshot and a fresh PostgreSQL
logical export, including reconciliation of restored database file references
against the document snapshot. This was an isolated data-recovery test, not a
production cutover or a complete application failover.

Live project records, live document objects, permissions, provider plans and
database network restrictions were unchanged. No new paid database or public
test application was created. Recovery data remained inside Heritage's existing
Render backup-service environment; no database export was downloaded to the
operator's computer. Temporary recovered data and the test database were removed.

## Evidence

| Check | Observed result |
| --- | --- |
| Database | Existing `heritage360-db`, PostgreSQL 18; external connections blocked |
| Recovery window | Dashboard showed three-day point-in-time recovery |
| Fresh database export | `2026-11-18T16:03Z.dir.tar.gz` completed and restored |
| Restored database | 72 public tables; 826 rows counted |
| Projects / users | 3 projects; 3 user accounts |
| Project documents | 7 documents and 7 document versions |
| Historical selections / change orders | 255 selections; 10 change orders, including synthetic project data |
| Invoices | 0 recorded invoices; this is not a statement about historical customer payments |
| Authenticator device records | 1; seed decryption and login against the restored database were not tested |
| Document backup | Manual run completed November 18 at 11:02 AM EST; 9 objects verified, 2 new copies |
| Document restore | All 9 objects reconstructed into a temporary ZIP; SHA-256, size and ZIP integrity verified |
| File reconciliation | All 7 database file references appeared in the verified backup snapshot; 0 missing |
| Cleanup | Temporary database stopped and removed; temporary recovered archive and database export removed |

Document snapshot:
`snapshots/20261118T160213Z-9c10c1dcb8f746559abf29614ef0f8a2.json`.
The backup completion heartbeat was accepted.

## Procedure exercised

1. Requested a new logical export from the production database's Render Recovery
   page. Did not change the live database or invoke an in-place restore.
2. Triggered the existing `heritage360-document-backup` cron job to include the
   two recent estimate uploads. Confirmed success and its snapshot identifier.
3. In the independent backup service's temporary Web Shell, ran the existing
   `ops.restore_documents.restore` implementation from deployed backup commit
   `adf49125861193febf799a41b1cf940ade00ddf2` into a newly created temporary ZIP.
   That implementation verifies recovered bytes against each manifest checksum
   and size. Also opened the ZIP, tested its integrity and counted all 9 members.
4. Prepared PostgreSQL 18.7 server binaries in a temporary directory from the
   official signed PostgreSQL Debian repository. Verified its signing-key
   fingerprint against PostgreSQL's published fingerprint. The required
   `liburing2` package came from the signed Debian repository. No system package
   installation or service configuration was changed.
5. Entered the Render export's temporary download URL through a non-echoing
   prompt. The URL was not saved in files or shell history. Validated its HTTPS
   host and database-specific path, and rejected redirects.
6. Downloaded the archive into the temporary environment. Enforced archive size
   limits and safe extraction. Initialized a separate PostgreSQL cluster with
   no TCP listener and a private Unix socket in its temporary directory.
7. Restored into a new, empty `heritage_restore_check` database using
   `pg_restore --no-owner --no-privileges --exit-on-error --single-transaction`.
   Counted every public table after the restore succeeded.
8. Read document, daily-log photo and selection attachment references from the
   restored database and reconciled them against the already verified snapshot.
9. Stopped the test server and removed temporary data and tools. No web app,
   notification worker, QuickBooks connection or email sender was started.

## Remaining recovery requirements

- Verify custody of the application encryption key and any historical fallback
  keys in an approved private recovery vault. Database backups contain encrypted
  authenticator seeds, not the keys needed to decrypt them. Do not put keys in
  source control, this report, chat messages or ordinary exported documents.
- This test deliberately omitted original database ownership and grants. Before
  a real cutover, configure the replacement database's application role and
  confirm its privileges, private networking and connection settings.
- Validate encrypted credentials, administrator login, client isolation, and
  application behavior before a real cutover. Provider settings and independent
  secret recovery are additional to a successful logical data restore.
- Keep the database export and document snapshot times together. For an actual
  recovery, require every restored file reference to match a verified object;
  the snapshot contains object history rather than a transactional database/file
  snapshot taken at exactly the same instant.
- Point-in-time recovery was available but was not exercised. This drill tested
  the logical export path. Provider-region/account-loss recovery was not tested.

References: [Render recovery and exports](https://render.com/docs/postgresql-backups),
[official PostgreSQL Debian packages](https://www.postgresql.org/download/linux/debian/),
and [Heritage administrator MFA recovery](administrator_mfa.md).
