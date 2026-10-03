"""Development-only parameter selection and paired statistical analysis."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True, slots=True)
class PairedStatistics:
    query_count: int
    sample_count: int
    seed: int
    bit_generator: str
    mean_delta: float
    ci95_lower: float
    ci95_upper: float
    randomisation_p_value: float


def paired_statistics(
    deltas: tuple[float, ...], *, seed: int, sample_count: int = 10_000
) -> PairedStatistics:
    """Return deterministic paired bootstrap and sign-flip evidence."""

    if not deltas:
        raise ValueError("paired deltas must not be empty")
    if seed < 0 or sample_count <= 0:
        raise ValueError("seed must be non-negative and sample_count must be positive")
    values = np.asarray(deltas, dtype=np.float64)
    if not np.isfinite(values).all():
        raise ValueError("paired deltas must be finite")
    generator = np.random.default_rng(seed)
    bootstrap_means = np.empty(sample_count, dtype=np.float64)
    randomised_means = np.empty(sample_count, dtype=np.float64)
    batch_size = 1_000
    for start in range(0, sample_count, batch_size):
        stop = min(start + batch_size, sample_count)
        size = stop - start
        indexes = generator.integers(0, len(values), size=(size, len(values)))
        bootstrap_means[start:stop] = values[indexes].mean(axis=1)
        signs = generator.choice((-1.0, 1.0), size=(size, len(values)))
        randomised_means[start:stop] = (values * signs).mean(axis=1)
    mean_delta = float(values.mean())
    lower, upper = np.quantile(bootstrap_means, (0.025, 0.975))
    p_value = (np.count_nonzero(np.abs(randomised_means) >= abs(mean_delta)) + 1) / (
        sample_count + 1
    )
    return PairedStatistics(
        query_count=len(values),
        sample_count=sample_count,
        seed=seed,
        bit_generator=generator.bit_generator.__class__.__name__,
        mean_delta=mean_delta,
        ci95_lower=float(lower),
        ci95_upper=float(upper),
        randomisation_p_value=float(p_value),
    )


def select_bm25_parameters(
    development_scores: dict[tuple[float, float], float],
) -> tuple[float, float]:
    """Select best C0 development score; ties prefer smaller k1 then b."""

    if not development_scores:
        raise ValueError("development scores must not be empty")
    allowed_k1 = {0.6, 0.9, 1.2, 1.5}
    allowed_b = {0.25, 0.5, 0.75}
    for (k1, b), score in development_scores.items():
        if k1 not in allowed_k1 or b not in allowed_b:
            raise ValueError("BM25 parameters are outside the frozen grid")
        if not np.isfinite(score):
            raise ValueError("development scores must be finite")
    return min(
        development_scores,
        key=lambda pair: (-development_scores[pair], pair[0], pair[1]),
    )
