# BenchFlow local operations (2026-10-23 checkpoint)

**Not release acceptance.** Windows x64 CPython 3.13 only. No containers,
system service, deployment, paid API or automatic startup. Backend application,
schema inspection and snapshot seams now exist. The isolated in-process
snapshot/restore/recovery/restart journey and a bounded live demo
Start/Health/Stop lifecycle are tested. This is not release acceptance. Do not
use real customer records until confidentiality/encryption policy is approved.

## Fresh dependency setup — offline reproducibility evidence

From PowerShell:

```powershell
Set-Location C:\Projects\BenchFlow
.\infra\Setup.ps1 -Dev -Offline
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m unittest infra.test_operations infra.test_recovery_integration -v
```

Existing pinned SHA-256 manifests and 28-wheel local cache are for this platform
only. Setup creates `.venv` if absent and uses its Python, never global pip.
Do not activate global environments or upgrade packages to bypass a failure.
Without the downloaded cache, offline setup cannot be reproduced; return that
gap to Lead instead of silently switching to a network install. `-Dev` includes
test clients. Runtime alone uses `Setup.ps1 -Offline`.

Pristine reproduction was tested in a disposable temporary venv using the
existing CPython 3.13 interpreter and local wheel cache, without network access
or changing the project `.venv`. Check the unique path before creating it, and
remove only that fixture after the test command completes:

```powershell
$project = 'C:\Projects\BenchFlow'
Set-Location $project
$fresh = Join-Path $env:TEMP ('benchflow-pristine-' + [guid]::NewGuid().ToString('N'))
if (Test-Path $fresh) { throw 'Choose a new temporary path' }
.\.venv\Scripts\python.exe -m venv $fresh
if ($LASTEXITCODE -ne 0) { throw 'venv creation failed' }
$py = Join-Path $fresh 'Scripts\python.exe'
& $py -m pip --disable-pip-version-check install --no-index `
  --find-links infra/wheels --require-hashes --only-binary=:all: `
  -r requirements-dev.txt
if ($LASTEXITCODE -ne 0) { throw 'offline hash-locked installation failed' }
& $py -m pip check
if ($LASTEXITCODE -ne 0) { throw 'pip check failed' }
& $py -m unittest infra.test_operations infra.test_recovery_integration -v
if ($LASTEXITCODE -ne 0) { throw 'Infra tests failed' }
# Only after tests finish and the exact temporary path is confirmed:
Remove-Item -LiteralPath $fresh -Recurse -Force
```

Observed: offline install from the 28 local wheels succeeded, pip check was
clean, runtime/dev imports and New York January UTC−05 / July UTC−04 probes
passed, and **12/12 Infra tests passed** in 145.378 seconds. One existing
Starlette/httpx deprecation warning remains. The temporary venv was removed.
This proves a fresh venv using the present interpreter/cache, not an all-platform
lock, independent interpreter bootstrap, wheel provenance, or success when the
cache is absent. `Setup.ps1 -Dev -Offline` targets the project `.venv`; its
wrapper itself was not the command used for this pristine-venv proof. Do not
delete the working `.venv` to simulate fresh setup.

Final isolated command: **12 tests passed, 167.710s** with one existing
Starlette/httpx deprecation warning. Tests use a new temporary data root AND
temporary local-app-data/backup root, no normal/demo files and no sockets.
They exercise real SQLite, shared strict manifest, actual Backend snapshot,
HTTP manager snapshot, schema inspection, auth invalidation, pre-restore
preservation, file replacement, manager recovery and application restart.
Wrong hash, manifest schema mismatch, future/drifted migration ledger, live
root lock contention and invalidator nested-lock refusal are covered.
All non-auth/non-operational database tables compare exactly after restore
and restart, including job/task/estimate/stock/settlement tables (some empty
in this fixture). This is not six complete business journeys or load evidence.

## Configuration — non-secret example

```powershell
$env:BENCHFLOW_HOST = '127.0.0.1'
$env:BENCHFLOW_PORT = '8000'
$env:BENCHFLOW_DEMO = '0'
Remove-Item Env:BENCHFLOW_DATA_ROOT -ErrorAction SilentlyContinue
```

Normal root: `%LOCALAPPDATA%\BenchFlow\data`; explicit `-Demo` uses separate
`%LOCALAPPDATA%\BenchFlow\demo`. Database: `benchflow.sqlite3`.
Backups: `%LOCALAPPDATA%\BenchFlow\backups\<UUID>\{manifest.json,benchflow.sqlite3}`.
Infra deliberately rejects custom data-root overrides, UNC and reparse paths.
Settings paths for tests must set an isolated `local_app_data_root` as well as
`data_root`; `Settings.for_test()` alone does not isolate the fixed backup root.
No passwords in environment, command arguments, files, logs or examples.

## Start / readiness / stop — bounded isolated live lifecycle tested

After approved dependency setup and first-manager bootstrap (or explicit
isolated fictional demo mode), use an interactive terminal:

```powershell
.\infra\Start.ps1       # foreground supervisor; keep this terminal open
.\infra\Health.ps1      # second terminal
.\infra\Stop.ps1        # second terminal; or Ctrl+C in supervisor
```

To repeat the demo lifecycle without using the normal or default demo data
roots, set a fresh temporary `LOCALAPPDATA` in each terminal before running
the scripts. The fixed-root rule remains in force: do not set
`BENCHFLOW_DATA_ROOT` to an arbitrary path.

```powershell
$env:LOCALAPPDATA = Join-Path $env:TEMP 'benchflow-demo-isolated'
if (Test-Path $env:LOCALAPPDATA) { throw 'Choose a new empty fixture path' }
.\infra\Start.ps1 -Demo
# In a second PowerShell terminal, use the exact same LOCALAPPDATA value:
$env:LOCALAPPDATA = Join-Path $env:TEMP 'benchflow-demo-isolated'
.\infra\Health.ps1
.\infra\Stop.ps1 -Demo
```

After stopping, confirm the recorded supervisor and child identities have
exited, the listener is gone, and `.operations.json` is absent before removing
only that explicitly isolated temporary fixture. Never recursively remove the
normal app-data directory or a default demo directory as a cleanup shortcut.

The child is exactly one Uvicorn worker at `http://127.0.0.1:8000`, no reload,
access logs or proxy-header trust. No alternative host or port fallback.
The launcher refuses missing app, occupied port and existing process records.
Readiness requires HTTP 200 and exactly `{"status":"ready","schema_version":N}`,
with positive integer N. Redirects/proxies are disabled. TCP listening alone
is not readiness. The prior in-process recovery test exercised Backend health
through a loopback TestClient and real lifespan; it proves readiness then
shutdown releases the root lock and sets app readiness false, but not Windows
signals. The live `Health.ps1` and TCP-listener evidence below is distinct.

The launcher holds `.operations.lock` and records child **and supervisor**
PID, OS creation time and image in `.operations.json`. Stop checks both
identities, requests supervisor shutdown, then waits. It never kills by name
or trusts PID alone. Supervisor uses its owned child handle, CTRL_BREAK then
bounded terminate fallback.

Observed live evidence (2026-10-23): `Start.ps1 -Demo` created data only beneath
a newly allocated temporary `LOCALAPPDATA`; readiness printed
`127.0.0.1:8000 workers=1`; `Health.ps1` returned
`{"status":"ready","schema_version":8}`. `Get-NetTCPConnection` showed one
listener bound to `127.0.0.1:8000`, owned by the single Uvicorn worker
descended from the recorded launcher child. `Stop.ps1 -Demo` printed `STOPPED`,
removed the operation record, released port 8000, and the recorded supervisor,
launcher child and identified worker all exited; in the second bounded check
all three had exited within 2.04 seconds from the start of the Stop command.
An occupied-port attempt returned exit 2 / `PORT_8000_OCCUPIED` and created no
process record.
The initial Start/Health/Stop lifecycle and the occupied-port case both used
temporary demo roots, not owner data. These are bounded local observations,
not crash-race, PID-reuse, repeated soak, or security acceptance.

## First manager / last-manager recovery

In an interactive console, while the application is stopped:

```powershell
.\infra\Operator.ps1 -Action bootstrap -LoginId fictional.manager -DisplayName 'Fictional Manager'
.\infra\Operator.ps1 -Action recover -AccountId '<existing-manager-UUID>' -VerificationMethod in_person
```

These wrap actual `backend.cli bootstrap-manager` / `recover-manager`
signatures, with launcher lock ONLY. Backend helpers acquire their own database
lock; do not wrap them in Infra's `offline()` database lock. The backend prompts
for verification and holder-entered password twice without echo. Phone
verification must use an already recorded contact, not a supplied new number.
OS SID/account/image/PID provenance is not independent identity verification.
Save account UUID privately; never print/store passwords or invent a new manager
to work around a missing existing account.
Host `recover_manager` was executed against an isolated existing manager,
followed by real HTTP login after restart. Interactive CLI password/verification
prompts and first-manager bootstrap were NOT executed in this package.

## Backup / offline restore — isolated current-schema journey tested

Do not run these on owner data during integration. Approved operator procedure
(wrappers not executed on normal/demo roots in this package):

```powershell
.\infra\Stop.ps1
.\infra\Operator.ps1 -Action backup
# Privately record the returned UUID; manager reviews backup age/consequences.
.\infra\Restore.ps1 -BackupId '<UUID>' -Confirm 'RESTORE BENCHFLOW <UUID>'
# STOP HERE: restore does not start the app.
.\infra\Operator.ps1 -Action recover -AccountId '<existing-manager-UUID>' -VerificationMethod in_person
# Only after successful verified recovery: explicit Start, Health, sign-in and business verification.
```

If already stopped, Stop may refuse `NO_OWNED_PROCESS_RECORD`; verify absence
of an app rather than treating that as successful shutdown. Restore requires
launcher + live-root database locks, canonical backup UUID, current-user-only
ACLs, no reparse paths, matching strict shared manifest UUID/size/hash/schema,
SQLite integrity and foreign-key checks. Exact manifest fields are
`format_version=1, backup_id, created_at` (UTC), `database_file="benchflow.sqlite3",
schema_version, size_bytes, sha256, integrity="ok"`. No `kind`, `app_version`,
`created_utc`, alternate filename or parallel Infra manifest.
Schema version is checked against the installed migration catalog (currently
8), not a hardcoded latest version. SHA-256 detects mismatch, not authenticity or encryption.
Backups remain sensitive plaintext; no confidentiality claim.

Restore copies into a separate same-volume `.restore-<UUID>\benchflow.sqlite3`
and constructs staged Settings. Backend's actual
`invalidate_restored_authentication(staged_settings, source_backup_id=...,
operator_provenance=...)` self-locks that separate root, migrates supported old
schema and invalidates all sessions/codes/passwords before live replacement.
Infra takes the candidate root lock for direct read-only schema/checksum
inspection, releases it before the self-locking invalidator, then reacquires
and reinspects before preserving/replacing the active database. Live offline
locks remain held throughout. `SchemaInspection` has no `.ready` field.
It preserves a pre-restore snapshot, checkpoints/closes both DBs and refuses
remaining WAL/SHM/journal files, then performs same-volume replacement.
Failed staging is retained privately for diagnosis. No auto-start.
Other accounts need verified reset/invitation after manager recovery; old
credentials must never work. Isolated evidence verifies both old manager and
customer cookies rejected, old passwords rejected, pending codes removed,
invitations revoked, every account reset, restore audit event persisted,
old recovery/activation codes rejected by real HTTP endpoints, recovered
manager login successful, pre-restore data preserved and post-snapshot marker
absent from restored active data. Restore does not auto-start the application.

New snapshots call `backend.backups.create_consistent_snapshot` while the
caller owns the live root lock, then validate the published bundle. Backend
retains failed `<UUID>.partial` bundles; never auto-purge them. Infra retains
failed `.restore-<UUID>` stages for review. Supported older positive-prefix
restore is implemented via Backend migration but NOT exercised end-to-end
here; preservation of an already-old active database fails closed because a
new pre-restore snapshot requires current schema. Diagnose/migrate a separate
isolated copy before any approved real-data operation; do not bypass inspection.

## Fictional demo reset / retention

Saved but not integration-tested:

```powershell
.\infra\Operator.ps1 -Action demo-reset -Demo -Confirm 'RESET FICTIONAL BENCHFLOW DEMO'
```

Requires fixed demo root, offline locks, pre-reset snapshot; renames the old
database instead of deleting it. Normal data is never reset. Do not manually
delete sidecars. Demo story seeding and nondestructive restart credential
rotation remain Backend integration dependencies; no working demo is claimed.
Backups have operator-managed 30-day retention: no automatic pruning. Never
remove the only verified copy or latest pre-restore copy. Failed stages and
preserved-reset DBs require explicit review, not age-based deletion.

## Diagnostics / crash recovery

* Missing backend app or schema hook: stop and return exact contract to Lead.
  Do not implement a parallel backend or weaken validation.
* Setup failure: check platform, cache and hash manifest, rerun offline setup.
  `pip check` failure is a blocker, not permission to update the lock.
* Occupied port: inspect `Get-NetTCPConnection -LocalPort 8000` and the exact
  owning PID with `Get-Process -Id <PID>`; never kill an unrelated application.
  The PowerShell wrapper reports a generic exit-2 error; on a refused command,
  the underlying module emits a safe closed code (the tested case was
  `PORT_8000_OCCUPIED`). Do not replace that with raw traces or expose
  process/database records.
* Stale record / supervisor timeout: do not delete state while any recorded
  process is alive. Compare PID, creation time and image for both roles; if
  identity differs, do not terminate it. Ask Lead/operator for private review.
  Only after proving both exited and database lock is available, preserve a
  copy of stale state privately and remove the stale **state file only**.
  Uvicorn's single worker is a descendant of the recorded launcher child and
  is not a separate field in `.operations.json`; the tested stop lifecycle
  observed that descendant exit, but worker-specific stale-process recovery
  has not been implemented. If a descendant remains, preserve exact PID,
  parent PID, creation time and image for Lead; never terminate by name.
* Failed integrity/checksum/auth invalidation: do not replace/start. Keep source,
  live data, pre-restore copy, failed stage and sidecars unchanged. Rerun only
  against a new isolated candidate after diagnosis; no destructive rollback.
* Diagnostics expose closed codes only. Raw child/access logs are suppressed;
  this limits diagnosis and is an explicit observability gap. Do not paste
  database contents, process records, paths with identities or credentials into
  public issues or telemetry.

## Hypothetical operating costs

`infra/COSTS-PLANNING.md` is a sourced, hypothetical AWS Lightsail option, not a
quote, capacity claim, deployment plan or measured bill. Its 2026-10-23 rates
were read from the official pricing page:
https://aws.amazon.com/lightsail/pricing/. It models Linux 1/2/4-GB bundles at
$7/$12/$24 per month plus snapshot storage at $0.05/GB-month, with explicit
traffic/user/storage assumptions and excluded costs. The local Windows,
loopback-only implementation must not be deployed as that future Linux design;
no account or resource was accessed. No AWS spending, free-tier reliance, paid
API, or cloud provisioning is authorized by this estimate.

## Remaining acceptance work

Interactive first-manager bootstrap and verification/password UI; older-prefix
staged migration/recovery; populated stock/settlement recovery; ACL
adversarial/reparse races; crash/port race/PID-reuse/CTRL_BREAK behavior; WAL
busy/sidecar and atomic replacement failure; demo rotation; approved
confidentiality/retention policy; dependency notices, native licenses and
advisories. No deployment/publication permission implied.
