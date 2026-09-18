# Phase 1 Research Charter

## Decision to make

Determine whether NormGuard addresses a defensible, useful gap and whether a controlled proof of concept should be built.

## Problem statement

Text-processing libraries expose stemming, lemmatisation, tokenisation, and normalisation primitives. Search and RAG teams must still decide which transformations improve their task, which terms must be protected, and whether a pipeline change creates a retrieval regression.

NormGuard proposes a developer-facing quality layer that evaluates those decisions using task results rather than general rules of thumb.

## Primary research question

Can a task-aware, domain-aware normalisation quality gate improve lexical or hybrid retrieval while preventing harmful transformations of important terminology?

## Falsifiable hypotheses

- **H1 — task dependence:** no single normalisation strategy wins across all target retrieval tasks.
- **H2 — terminology safety:** protected-term controls reduce harmful domain-term transformations without materially reducing useful normalisation.
- **H3 — regression value:** a fixed evaluation set detects quality losses caused by pipeline changes before deployment.
- **H4 — bounded RAG value:** normalisation has measurable value for some lexical and hybrid RAG configurations, but is not assumed to improve dense retrieval.

## Initial domains

| Domain | Why it is useful | High-risk terminology |
|---|---|---|
| Cybersecurity | Dense technical vocabulary and identifiers | CVEs, products, commands, ports |
| E-commerce | Inflection, aliases, brands and model numbers | SKUs, brands, product variants |
| Healthcare or legal | Precision-sensitive professional language | drug names, clauses, citations |

## Phase 1 questions

1. Who experiences the problem and in which workflow?
2. Which open-source and commercial solutions already address it?
3. Which proposed capabilities are genuinely missing?
4. What is the smallest fair experiment that can disprove the idea?
5. Which datasets may be used legally and reproducibly?
6. Which metrics represent retrieval utility, meaning preservation, latency and operational cost?
7. What would make the team stop, narrow or reposition the project?

## Exit criteria

Phase 1 finishes with:

- a sourced landscape matrix;
- a literature review;
- three documented developer workflows;
- a frozen evaluation proposal;
- a research-gap decision;
- a go, narrow, reposition, or stop recommendation.

Application development begins only after that decision.
