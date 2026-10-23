# Installation profiles

NormGuard separates the deployable Safety Core from its research and dataset tooling.

## Core

```bash
pip install normguard
```

The default package contains offline policy verification, the bounded deterministic runtime,
the stable SDK facade, and the CLI. Its only third-party runtime dependency is
`cryptography`. Importing `normguard` does not import spaCy, retrieval, evaluation, or ESCI
modules.

## Research

```bash
pip install 'normguard[research]'
```

This profile adds the frozen proof-of-concept stack: spaCy and its English model, BM25,
IR evaluation, and NumPy. Existing research imports from the package root are resolved lazily
for compatibility. Direct research-module imports are also supported.

## ESCI

```bash
pip install 'normguard[esci]'
```

This profile adds PyArrow for the ESCI data preparation tools. It does not add the research
stack. Install both extras explicitly when both capabilities are required:

```bash
pip install 'normguard[research,esci]'
```

The official ESCI test partition remains sealed and is not read or transformed by any install
profile or CI check.

## Compatibility boundary

The supported deployment surface is `normguard.sdk`, the package-level Safety Core exports,
and the `normguard` CLI. Research exports remain available when the `research` extra is
installed, but research modules are not loaded as a side effect of importing the core package.
