# Hypothetical AWS option — planning estimate, not a quote or measured bill

2026-10-23, USD, US East (N. Virginia), no free tiers or credits assumed.
Official rates retrieved read-only on this date:
https://aws.amazon.com/lightsail/pricing/

Linux public-IPv4 general-purpose bundles list $7/month (1 GB RAM, 40 GB SSD,
2 TB transfer), $12 (2 GB, 60 GB, 3 TB), $24 (4 GB, 80 GB, 4 TB).
Instance/disk snapshot storage lists $0.05/GB-month.

| Assumed workload, not capacity proof | Active users/month | Requests/month | Live DB | Traffic/month | Retained snapshot GB-month | Formula / monthly base |
|---|---:|---:|---:|---:|---:|---:|
| Small, one shop | 10 | 30,000 | 1 GB | 5 GB | 5 | $7 + 5 × $0.05 = **$7.25** |
| Medium, busier shop | 50 | 150,000 | 5 GB | 25 GB | 25 | $12 + 25 × $0.05 = **$13.25** |
| Larger, optimistic single shop | 200 | 600,000 | 10 GB | 100 GB | 50 | $24 + 50 × $0.05 = **$26.50** |

Request counts assume roughly 3,000 requests per active user/month and are
rough planning inputs, not observations. Traffic includes static/UI/API traffic
and is assumed below bundle allowances; recalculate if ingress+egress crosses
the allowance. Snapshot quantities are retained billed data, not live DB alone.
Snapshots are not a substitute for consistent SQLite backups/restore testing.

This is a FUTURE architecture, not the shipped local implementation: one Linux
VM, one app process/SQLite, HTTPS reverse proxy, private encrypted tested backups.
Linux launch/ACL/operator provenance, remote origin/HTTPS session security,
firewall, backup export and monitoring require separately approved engineering.
Current Windows/loopback-only code must NOT be deployed as-is. No AWS resource
or account was accessed/provisioned and no capacity/load test has been run.

Excluded: tax, domain, engineering/support, off-instance backup transfer and
redundancy, monitoring/log storage, overages, restore drills, availability costs,
email/SMS/payment vendors (none integrated), and security operations. AI/API
inference workload is zero in this architecture; no paid API assumed. One-time
engineering is unpriced and separate. Existing subscriptions/model consumption
are separate and unmeasured. Local validation used no additional service purchase.

Single-instance failure and SQLite write contention dominate risk. Larger
scenarios may need PostgreSQL and app changes before concurrency is acceptable;
do not horizontally replicate SQLite or treat a bigger VM as proof of scale.
Backup growth, traffic and support can dominate the base bill. Root COSTS.md
belongs to Lead; this scoped draft is for review, not publication.
