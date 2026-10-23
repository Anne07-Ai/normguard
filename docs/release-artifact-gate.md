# Release artifact gate

Issue #78 adds a non-publishing release gate for the NormGuard Python package.

## Purpose

The workflow builds and validates the exact wheel and source distribution that could
later be promoted. It does not create a GitHub release or publish to PyPI.

For every pull request and every push to `main`, CI:

1. restores the locked Python 3.12 environment;
2. builds the wheel and source distribution with `uv build --no-sources`;
3. validates the project schema, wheel contents and installed core metadata;
4. installs the wheel into a new isolated virtual environment;
5. smoke-tests the installed package and both CLI entry paths;
6. audits the installed dependency graph and writes CycloneDX JSON;
7. generates SHA-256 checksums and a wheel-content inventory;
8. uploads distributions and release evidence as separate CI artifacts.

Any failed step blocks the job. Partial or unverified distributions are not promoted.

## Trust boundary

The workflow has read-only repository permission. It has no PyPI token, signing key,
GitHub release permission, deployment environment, or OIDC publication permission.
Artifact upload is evidence retention, not publication.

A future publication workflow must be a separate change with:

- a protected GitHub environment;
- explicit human approval;
- immutable tag-to-commit verification;
- trusted publishing through short-lived OIDC credentials;
- artifact digest comparison against this gate;
- release notes and rollback ownership.

## Local verification

```bash
uv sync --locked --all-extras --dev
uv build --no-sources
uvx --from validate-pyproject==0.24.1 validate-pyproject pyproject.toml
uvx --from check-wheel-contents==0.6.3 check-wheel-contents dist/*.whl

uv venv --seed .release-venv
uv pip install --python .release-venv/bin/python dist/*.whl
.release-venv/bin/python -c "import normguard; print(normguard.__version__)"
.release-venv/bin/normguard --help
```

Generated `dist/`, virtual environments, audit reports and checksums remain build
artifacts and must not be committed.

## Current dependency boundary

The `0.1.0` default installation is the lightweight Safety Core and has one
third-party runtime dependency: `cryptography`. Research dependencies and the
hash-pinned spaCy model are available only through the `research` extra; PyArrow is
available only through the `esci` extra. CI builds one artifact and installs each
profile independently, including a clean core-only environment that rejects
undeclared research/dataset dependencies.

The completed ESCI calibration boundary remains unchanged. Development and official
test partitions are not accessed by this workflow.
