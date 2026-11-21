# Heritage document backups and recovery

## Deployment status

Activated on November 17, 2026. The private `heritage360-backups` bucket exists
in Heritage's Cloudflare account. Its `protect-backup-history` rule is enabled,
covers the entire bucket, and prevents overwrites/deletes indefinitely until an
account administrator changes that rule. Public access remains disabled.

The Render cron service `crn-dbu96pe8bjmc73av3140` is active in the Heritage 360
workspace, running code commit `adf49125861193febf799a41b1cf940ade00ddf2`.
Two account tokens are restricted to the source and backup buckets respectively
and stored only in the cron service. They have no expiry and can be revoked.
The first manual run finished successfully at 12:49 PM EST on November 17.
It verified all three source objects, storing two unique content copies and a
snapshot inventory. An independent recovery into a temporary ZIP then verified
all three recovered files byte-for-byte against live source objects, including
the known synthetic upload document. The temporary ZIP was removed and the
recovery shell was closed. No production files or database records were replaced.

Snapshot: `snapshots/20261117T174915Z-8b09c29f6166461eae5c3ee386f760f8.json`.
The next scheduled run is November 18 at 4 AM EST. A manual success does not
prove that a scheduled run has occurred. The existing web application was
unaffected by this separate deployment.

## Access and schedule

Deploy `ops/document-backup.render.yaml` as a separate Blueprint, or configure
the equivalent native Python cron job in the Heritage 360 workspace:

- Service: `heritage360-document-backup`, Ohio, smallest cron compute.
- Source branch: `feature/heritage-backup-readiness`; auto-deploy off.
- Build: `pip install -r ops/backup-requirements.txt`.
- Run: `python ops/backup_documents.py`.
- Schedule: `0 9 * * *` UTC, daily at 4 AM EST / 5 AM EDT.
- Render cron minimum: $1/month, plus runtime above that minimum and R2 usage.
- The service inherits the workspace's **Only failure notifications** setting,
  verified in Render on activation. No deliberate failure email was sent, so
  inbox delivery of backup failure alerts has not been tested.
- Better Stack missed-run monitoring is active, expecting a completion every
  day with one hour of grace. Email alerts target the primary responder;
  the team has one member, Michael Provost (`slaterwoodsdesign@gmail.com`).
  No test alert was sent, so inbox delivery remains unverified.

### Completion monitoring (activated and verified)

Better Stack heartbeat `534645` in team `t636247`, named
`Heritage 360 document backup`, was created on the existing free plan on
November 17, 2026. Its secret completion URL is stored as `BACKUP_HEARTBEAT_URL`
in the Render cron service, not in this repository.

Deployment `dep-dbu9e9gu01pc73deqfdg` completed at 1:04:39 PM EST. The manual
verification run completed at 1:05:39 PM EST, verifying all three source objects
with zero new content copies and creating snapshot
`snapshots/20261117T180536Z-efadec772375417bac451be2a0849e7a.json`.
The run logged `Backup completion heartbeat accepted.` at 1:05:36 PM EST.
Better Stack then showed **Up**, a received heartbeat, and zero incidents for
this heartbeat. The endpoint was called only by the completed backup job.
The first scheduled run and actual alert email delivery remain unverified.

The code sends an empty HTTPS GET only after every object and the snapshot
inventory pass readback verification. No file contents, file names, snapshot
identifier, error details, or database data are sent to Better Stack. Only the
documented Better Stack heartbeat endpoints are accepted; redirects are rejected.
Network/server failures receive at most three attempts with ten-second socket
timeouts. If notification fails, the verified backup remains intact but the job
exits nonzero with a generic warning, allowing Render failure notifications.

Monitoring starts with the first received signal. Missing completion is detected
25 hours after the last accepted signal, rather than at a fixed wall-clock time.
Manual runs also move that deadline. Do not disable the heartbeat or send success
signals from an unrelated process. An empty environment setting disables heartbeat
delivery for backward compatibility, so absence of that signal must still alert
externally once the monitor is activated.

Create TWO bucket-restricted R2 credentials, with no account administration:

1. Object Read only for `heritage360-private`. Store as `SOURCE_ACCESS_KEY_ID`
   and `SOURCE_SECRET_ACCESS_KEY` in the cron service only.
2. Object Read & Write for `heritage360-backups`. Store as `BACKUP_ACCESS_KEY_ID`
   and `BACKUP_SECRET_ACCESS_KEY` in the cron service only. R2's bucket lock
   blocks deletion/overwrite even though this permission bundle includes them.

Set `R2_ACCOUNT_ID`, `SOURCE_BUCKET=heritage360-private`, and
`BACKUP_BUCKET=heritage360-backups`. Do not reuse the web app's write-capable
source credential or give the web app access to the backup bucket. Do not put
credentials, private manifests, files, or database exports in Git.

## What the job verifies

The job reads all source objects, hashes their contents, and stores each unique
content once under `objects/<sha256>`. Unchanged files do not consume another
stored copy. It reads each backup object back and verifies its hash and size
before publishing a uniquely named, private `snapshots/*.json` inventory.
The manifest is also read back and checked. Previous inventories and copies
remain when a source object is changed or removed. The code never deletes an
object or writes to the source bucket.

A failed run exits nonzero with a generic message, without private filenames,
credential details, or exception text in logs. No successful run is reported
unless the full readback succeeds. Partial orphan copies are safe to retain.
Empty buckets, missing access, concurrent object changes, corrupt copies,
objects above 250 MiB, inventories above 100,000 objects, and manifests above
32 MiB fail visibly. Review limits before importing large Buildertrend exports.
This is a sequential file inventory, not an atomic database/filesystem snapshot.
New objects created during the scan may be captured on the next successful run.

History is retained without automatic expiry initially. Review storage growth
and agree a retention policy before adding garbage collection. Old manifests
may reference old content, so age-based deletion of content blobs is unsafe.
The same Cloudflare account remains a shared failure/administrative boundary;
this does not provide an independent second-provider disaster-recovery copy.

## Recover documents without changing production

Use a trusted Linux recovery environment with sufficient temporary disk space.
Install `ops/backup-requirements.txt`, supply the account ID, backup bucket and
a READ-ONLY credential for that bucket. Select a successful snapshot from the
job's output or Cloudflare's private bucket browser, then run from repository root:

```sh
python -m ops.restore_documents --snapshot snapshots/SELECTED-SNAPSHOT.json --output /secure/recovery/heritage-documents.zip
```

Replace the snapshot argument with an actual existing snapshot. The parent
directory must already exist and the output file must not exist. The command
downloads every referenced object, checks hash and size, and publishes a new ZIP
only after every check passes. Unsafe paths, corrupt files and missing objects
fail recovery. It does not write into the source bucket or modify Django records.

For a recovery drill, inspect the resulting archive and compare a known test
document with its original. For an actual incident, restore the database into an
isolated instance first, map its file names to the snapshot inventory, and verify
the complete referenced file set before approving any production replacement.
Never overwrite the live database or bucket merely to test recovery.

## Database recovery evidence

Render's three-day point-in-time recovery was available on November 17, 2026.
A logical export created at 10:15 AM EST was restored with PostgreSQL 18 tools
into a uniquely named test database on the same server. It restored 62 tables
and 76 migration records. Seven important table counts matched the live sample
data: users, organizations, projects, documents, document versions, daily logs,
and change orders. The test database was then removed; production was not replaced.

The earlier 9:56 AM export lacked the newest test change order. Export completion
time alone is not proof that the latest transaction is recoverable. Check actual
recovered records and timestamps. Render exports can contain multiple database
directories, including temporary databases captured in the provider snapshot;
select the `heritage360` directory, not an arbitrary `toc.dat`.

This drill verified a logical export, not a full Render instance failover, and
does not replace a document backup drill or ongoing schedule monitoring.
