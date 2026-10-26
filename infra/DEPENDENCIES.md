# Source-candidate review — 2026-10-24

The local source candidate includes original source and fictional screenshots, not dependency wheels or native runtimes. The exact supplied license texts for the 28 pinned packages are preserved in [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md). Original code uses the approved MIT license. Fresh offline installation and compatibility checks have passed for the local Windows x64 / CPython 3.13 cache.

This review covers the source-only candidate. It does not clear redistribution of the native wheels, a Python runtime or a binary installer. The older inventory below deliberately retains its native build-chain and advisory gaps for that separate distribution decision. There is no current vulnerability-database clearance or zero-risk claim. Downloaded wheels remain excluded by the source ignore rules.

---

# Dependency inventory checkpoint — NOT license/advisory clearance

2026-10-23. Sources: actual local wheel `.dist-info/METADATA`, packaged
license/notice filenames and native member filenames. SHA-256 of all 28 wheel
files matched a requirement manifest entry. Versions below are direct and
transitive pinned closure, not all-platform support. Source distribution and
build-chain provenance were not verified. No dependency upload/publication.

Metadata-reported licenses (not independently adjudicated):

| Packages and versions | Metadata license |
|---|---|
| annotated-doc 0.0.5; annotated-types 0.8.0; anyio 4.15.1 | MIT |
| argon2-cffi 25.1.0; argon2-cffi-bindings 26.1.0 | MIT |
| cffi 2.1.1 | MIT-0 |
| certifi 2026.10.19 | MPL-2.0 |
| click 8.5.0; fastapi 0.161.1 | BSD-3-Clause; MIT respectively |
| colorama 0.4.6 | BSD classifier only; exact terms unreviewed |
| h11 0.16.0 | MIT |
| httpcore 1.0.9; httpx 0.28.1; idna 3.20 | BSD-3-Clause |
| iniconfig 2.3.1; pluggy 1.7.0 | MIT |
| opentelemetry-api 1.45.1 | Apache-2.0 |
| packaging 26.3 | Apache-2.0 OR BSD-2-Clause |
| pycparser 3.12 | BSD-3-Clause |
| pydantic 2.14.1; pydantic_core 2.50.1 | MIT |
| Pygments 2.21.0 | BSD-2-Clause |
| pytest 8.4.2 | MIT |
| starlette 1.9.0; uvicorn 0.54.0 | BSD-3-Clause |
| typing_extensions 4.16.0 | PSF-2.0 |
| typing-inspection 0.4.4 | MIT |
| tzdata 2026.5 | Apache-2.0 (underlying timezone data terms need review) |

All wheels contained at least one license/notice-named member. Those texts
have NOT all been reviewed, their adequacy is not established, and notices are
not consolidated for redistribution. Wheel metadata licenses alone do not
prove acceptable free-use rights. Certifi's CA bundle and MPL obligations,
dual-license choices, Python ecosystem notices and generated files need review.

Native wheel members observed:

* argon2-cffi-bindings: `_argon2_cffi_bindings/_ffi.pyd`
* cffi: `_cffi_backend.cp313-win_amd64.pyd`
* pydantic_core: `pydantic_core/_pydantic_core.cp313-win_amd64.pyd`

Native subcomponent gaps: Argon2 reference code, possible vendored libffi,
Rust crate closure and compiler/runtime linkage, build provenance and notices
were not extracted/verified. CPython 3.13.9, bundled SQLite 3.50.4, bootstrap pip,
OpenSSL, Windows/VC runtime, PowerShell and OS tools are outside the app wheel
lock and also need source/version/license/runtime inventory. No claim of
advisory clearance: no vulnerability database scan was run. No source build
was executed. Keep wheels local/private pending full rights/provenance review.

Lead dependencies: commission full notice/native/SBOM and advisory review;
decide project license separately. Do not publish a “zero legal risk” claim.
