# Safety Core adversarial and property testing

Issue #84 converts Phase 3 threat-model claims into deterministic executable tests.
All generated values and corpus entries are synthetic. The suite does not access ESCI,
customer data, external models or network services.

## Covered threats

| Threat | Executable control |
|---|---|
| NG-T05 | Policy-rule permutations retain deterministic overlap semantics |
| NG-T06 | Prompt-like text remains inert and cannot replace policy or P0 output |
| NG-T08 | Resource-limit failures are typed, atomic and return no partial result |
| NG-T09 | Generated Unicode preserves determinism and protected-span offsets |
| NG-T10 | Generated raw values are absent from default evidence |
| NG-T13 | Arbitrary policy bytes fail at the strict SDK document boundary |
| NG-T16 | Failures never fall back to an unverified or partial result |
| NG-T18 | Runtime and SDK errors exclude raw input and document bytes |

## Reproducibility

Hypothesis runs with `derandomize=True`, no example database and bounded example counts.
This makes CI generation repeatable. Any committed regression example must remain synthetic
and must not contain customer or production policy data.

Run the isolated suite with core and development dependencies only:

```bash
uv sync --locked --no-extra research --no-extra esci --dev
uv run --locked --no-extra research --no-extra esci --dev \
  python -m unittest tests.test_adversarial_properties -v
```

Property tests complement fixed contract tests; they do not prove correctness for all inputs.
Production incident findings and safely minimized synthetic reproductions should be added as
fixed regression cases.
