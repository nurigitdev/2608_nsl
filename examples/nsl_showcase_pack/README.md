# NSL Business Controls Showcase Pack

This pack demonstrates independent, deterministic READ and VALIDATE Skills for
common corporate controls. It is a source and test showcase, not a production
system connector bundle. Endpoint configuration and credentials are excluded.

| Module | Status | Skills |
|---|---|---|
| Corporate Card Control | IMPLEMENTED | Monthly Summary, Monthly Policy Check |
| Employee Contract Expiry | PLANNED | Contract Expiry Check |
| Access Segregation | PLANNED | Access Segregation Check |
| Vendor Concentration | PLANNED | Vendor Concentration Check |
| Duplicate Invoice | PLANNED | Duplicate Invoice Check |

The machine-readable module inventory is `pack.json`. Each implemented module
contains NSL sources, canonical Tool Contracts, local profiles, bounded mock
fixtures, and positive and negative scenario suites.

The complete five-module pack can later be built and signed as an NSP. A partial
showcase is intentionally not represented as a production-ready signed package.
