# Security and operating boundary

BenchFlow is a local Windows application, bound to 127.0.0.1:8000. The host and origin are fixed; do not place it behind a tunnel, expose it to a LAN, or deploy it as-is.

Account roles and job/customer ownership are checked on the server. Customer projections exclude internal notes and other customers' records. Sessions use opaque cookies and CSRF protection; writes use closed schemas, explicit expected revisions and idempotency IDs. Passwords are Argon2 hashes. Offline operator recovery and backup restore are explicit host actions, not web-accessible administrator shortcuts.

The database and backups contain sensitive business information and are not encrypted at rest by this application. Windows ACLs and local filesystem rules are enforced by operations tooling. Protect the host and backup media; do not use real customer data until the operator has established confidentiality, retention, access and recovery policies.

Demo mode is separate fictional data. Its one-time credentials are deliberately revealed only on the host-local demo page and rotate at demo startup. Its simulated payments and collections never claim real transactions or transfer. Normal-mode manual payment, contact and custody records do not send messages or process money.

The local checks cover identity, CSRF/Host/Origin, ownership/assignment isolation, revocation, immutable records, command validation, migration integrity and recovery. These are targeted engineering checks, not a penetration test, current vulnerability-database clearance, compliance certification or internet-deployment approval. The remote CI workflow has been prepared but not executed.

For a suspected security defect, preserve a minimal fictional reproduction and discuss a private reporting channel with the repository owner. Do not publish credentials, customer records or a live exploit against someone else's installation.
