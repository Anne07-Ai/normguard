# Release artifact gate

Issue #78 adds a non-publishing release gate for the NormGuard Python package.

## Purpose

The workflow builds and validates the exact wheel and source distribution that could
later be promoted. It does not create a GitHub release or publish to PyPI.

For every pull request and every push to `main`, CI:

1. restores the locked Python 3.12 environment;
2. builds the wheel and source distribution with `uv build --no-sources`;
3. validates package metadata with strict Twine checks;
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
uvx --from twine==6.2.0 twine check --strict dist/*

uv venv --seed .release-venv
uv pip install --python .release-venv/bin/python dist/*.whl
.release-venv/bin/python -c "import normguard; print(normguard.__version__)"
.release-venv/bin/normguard --help
```

Generated `dist/`, virtual environments, audit reports and checksums remain build
artifacts and must not be committed.

## Current dependency boundary

The `0.1.0` distribution still includes the research dependencies and the
hash-pinned spaCy model reference used by the existing repository. This gate
validates that declared package as-is. Separating a lightweight Safety Core extra
from the research toolkit changes the public installation contract and requires a
separate compatibility issue and release decision.

The completed ESCI calibration boundary remains unchanged. Development and official
test partitions are not accessed by this workflow.
