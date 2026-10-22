# Proof-of-concept dependency provenance and licences

This record covers the direct NG-POC-001 and Phase 3 security dependencies. Exact resolved
versions and artefact hashes are authoritative in `uv.lock`; this table documents why each
direct dependency is permitted and where its licence can be independently verified.

| Dependency | Declared version | Licence | Primary provenance |
|---|---:|---|---|
| CPython | `>=3.12,<3.13` | PSF-2.0 | <https://docs.python.org/3/license.html> |
| spaCy | `3.8.16` | MIT | <https://github.com/explosion/spaCy> |
| `en_core_web_sm` | `3.8.0` | MIT | <https://github.com/explosion/spacy-models/releases/tag/en_core_web_sm-3.8.0> |
| BM25S | `0.3.11` | MIT | <https://github.com/xhluca/bm25s> |
| ir-measures | `0.4.3` | MIT | <https://github.com/terrierteam/ir_measures> |
| NumPy | lock-resolved within `>=2.0,<3` | BSD-3-Clause | <https://github.com/numpy/numpy> |
| cryptography | lock-resolved within `>=46,<51` | Apache-2.0 OR BSD-3-Clause | <https://github.com/pyca/cryptography> |
| PyArrow (ESCI extra) | lock-resolved within `>=18,<22` | Apache-2.0 | <https://github.com/apache/arrow> |
| pytest (development) | lock-resolved within `>=8,<10` | MIT | <https://github.com/pytest-dev/pytest> |
| Ruff (development) | lock-resolved within `>=0.11,<1` | MIT | <https://github.com/astral-sh/ruff> |
| uv (tooling) | `0.12.18` in CI | Apache-2.0 OR MIT | <https://github.com/astral-sh/uv> |

## Pinned English model artefact

- URL: <https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl>
- SHA-256: `1932429db727d4bff3deed6b34cfc05df17794f4a52eeb26cf8928f7c1a0fb85`
- Compatibility declared upstream: spaCy `>=3.8.0,<3.9.0`
- Runtime scope: CPU English tagging and lemmatisation; parser and NER are not required for C4.

## Cryptography supply-chain boundary

NormGuard uses only the high-level `cryptography` APIs for DER SubjectPublicKeyInfo parsing
and ECDSA P-256/SHA-256 verification. It does not implement elliptic-curve arithmetic,
signature parsing or random-number generation. Verification is offline and accepts public key
material only; production private keys remain in an independently managed KMS or HSM.

The dependency is constrained to a reviewed major-version window and fully resolved in
`uv.lock`. Before production distribution, generate an SBOM, review the selected wheel and
transitive Rust/OpenSSL provenance, and run vulnerability and licence policy gates.

Transitive dependencies retain their own licences. Before any distribution or production release,
generate a complete software bill of materials from the locked environment and review all
transitive licence metadata. This direct-dependency record is not a substitute for legal advice.
