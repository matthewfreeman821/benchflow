# Operating cost assumptions

Reviewed 2026-10-24. The supported application runs locally on an existing Windows computer. It uses no paid AI API, messaging vendor, supplier integration or payment processor. No additional cloud service was purchased for local validation. Electricity, hardware, Windows licensing, backup media and staff time are not priced here.

## Hypothetical future hosted architecture

This is an architectural comparison, not a deployment plan or a capacity guarantee. AWS Lightsail's published Linux/public-IPv4 bundles list 1 GB at $7/month, 2 GB at $12/month and 4 GB at $24/month; snapshot storage is listed at $0.05 per GB-month. Source checked 2026-10-24: https://aws.amazon.com/lightsail/pricing/ . Recheck rates, region and tax before purchasing.

Illustrative base totals with 5, 25 and 50 retained snapshot GB-month respectively are $7.25, $13.25 and $26.50 per month. Those arithmetic examples do not demonstrate how many users a machine can support. Engineering, domain, HTTPS, off-instance backup copies, monitoring, restore drills, support, transfer overages and redundancy are excluded.

The current application requires Windows host operations and loopback access. Linux operation, remote authentication/HTTPS, secure remote backup and monitoring require additional engineering and testing. SQLite write contention and a single host remain bottlenecks. More simultaneous shops may require a different storage/deployment design; no load-test capacity is claimed.
