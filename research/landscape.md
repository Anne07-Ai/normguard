# Landscape Review

Status: **first primary-source verification pass — 18 September 2026**

## Scope and interpretation

This document separates three categories that are often grouped together:

1. **morphological processors** — stemmers and lemmatisers;
2. **character/token normalisers** — Unicode, case, accent and replacement processing;
3. **search analysis systems** — index/query analyzers that use normalisation components.

A capability marked “not documented” means it was not found in the cited product documentation reviewed during this pass. It does not prove that no extension or external workflow can provide it.

## Verified capability matrix

| Solution | Category | Relevant capability | Customisation / protection | Task-level retrieval evaluation | Automatic pipeline recommendation |
|---|---|---|---|---|---|
| NLTK | Morphological library | Porter, Snowball, Lancaster, language-specific stemmers and WordNet lemmatisation | Algorithm- and component-dependent | Not documented | Not documented |
| spaCy | NLP pipeline | Lookup, rule/POS-based and trainable lemmatisation | Configurable pipeline, lookup tables and language factories | Not documented | Not documented |
| Stanza | NLP pipeline | POS-aware `LemmaProcessor`; seq2seq, dictionary and edit-classifier options | Custom key-value lemma dictionary and training options | Not documented | Not documented |
| Snowball | Stemming algorithms | Language-specific stemming algorithms and generated implementations | Algorithm definitions are extensible through Snowball | Not documented | Not documented |
| Hugging Face Tokenizers | Character/token normalisation | Unicode forms, lowercase, accent stripping, regex replacement and ordered sequences | Composable normalizer sequences | Not documented | Not documented |
| Elasticsearch | Search analysis | Algorithmic/dictionary stemming at index and search time | Override rules, keyword markers, conditionals and stem exclusions | Analyzer testing exists; dedicated cross-pipeline recommendation is not documented | Not documented |
| OpenSearch | Search analysis | Language-configurable stemmer token filter | Analyzer/filter configuration | Dedicated recommendation is not documented | Not documented |
| Apache Solr | Search analysis | Language-specific analyzers, stemmers, protected terms and overrides | Filter-chain configuration and protected word files | Dedicated cross-pipeline recommendation is not documented | Not documented |
| Proposed NormGuard scope | Quality layer | Adapters over candidate pipelines | Domain-term protection and declared safety policy | Matched lexical, dense, hybrid and RAG evaluation | Evidence-based recommendation, Pareto set or inconclusive result |

## Evidence register

| Source | Verified observation | Limitation | Accessed |
|---|---|---|---|
| [NLTK stem package](https://www.nltk.org/api/nltk.stem.html) | Documents a common stemmer interface, Porter variants, Snowball language stemmers, other language-specific stemmers and WordNet lemmatisation. It explicitly notes irregular words, morphology, POS and sense ambiguity as difficulties. | API documentation does not describe retrieval experiments or automatic method selection. | 2026-09-18 |
| [spaCy Lemmatizer API](https://spacy.io/api/lemmatizer) | Lemmas may be assigned through lookup or POS-dependent rules; spaCy also links a trainable EditTreeLemmatizer. The component is configurable and language-specific. | Rule and POS-lookup modes depend on earlier POS assignment; the page evaluates the component, not downstream retrieval choices. | 2026-09-18 |
| [Stanza lemmatisation](https://stanfordnlp.github.io/stanza/lemma.html) | Uses token text and universal POS; supports statistical, dictionary-only, ensemble and edit-classifier behaviours, plus pretagged input and custom dictionary entries. | It is a lemmatisation processor rather than a cross-engine retrieval quality gate. | 2026-09-18 |
| [Snowball algorithms](https://snowballstem.org/algorithms/) | Publishes stemming algorithms for multiple languages. | Algorithm availability does not answer which method is best for an application. | 2026-09-18 |
| [Hugging Face Tokenizers normalizers](https://huggingface.co/docs/tokenizers/api/normalizers) | Provides Unicode NFC/NFD/NFKC/NFKD, lowercasing, accent stripping, replacement and composable sequences while supporting offset alignment. | This normalizer API is not a lemmatisation evaluator or retrieval recommender. | 2026-09-18 |
| [Elasticsearch stemming guide](https://www.elastic.co/docs/manage-data/data-store/text-analysis/stemming) | Distinguishes algorithmic and dictionary stemming; recommends consistent index/search analysis; documents speed/memory trade-offs and controls for harmful conflation. | Configuration guidance is substantial, but this page does not compare candidate pipelines against developer-supplied relevance judgements. | 2026-09-18 |
| [OpenSearch stemmer filter](https://docs.opensearch.org/latest/analyzers/token-filters/stemmer/) | Provides a configurable language stemmer as part of an analyzer chain. | Documentation describes configuration rather than automatic task-aware selection. | 2026-09-18 |
| [Apache Solr language analysis](https://solr.apache.org/guide/solr/latest/indexing-guide/language-analysis.html) | Provides language-specific analysis chains and stemming-related filters, including protected-term mechanisms for relevant analyzers. | The guide does not establish a cross-engine normalisation regression gate. | 2026-09-18 |

## Important overlap with the proposed product

Elasticsearch already addresses part of the proposed safety story. It supports stemmer overrides, keyword markers, conditionals and stem exclusions. Consequently, **protected terms alone are not a differentiator**.

spaCy and Stanza already offer configurable and context-sensitive lemmatisation. Consequently, **POS-aware lemmatisation is not a differentiator**.

Hugging Face Tokenizers already offers composable character normalisation. Consequently, **pipeline composition alone is not a differentiator**.

## Provisional gap

The defensible gap to investigate is narrower:

> A developer-facing, engine-neutral quality workflow that runs matched retrieval experiments, measures terminology damage and operational cost, and returns a CI-compatible recommendation, Pareto set, or inconclusive decision with inspectable evidence.

This remains a hypothesis. It must be tested against relevance-testing platforms, commercial search products, research frameworks and open-source projects before any novelty claim.

## Closest research overlap

[A Task-Oriented Evaluation Framework for Text Normalization in Modern NLP Pipelines](https://arxiv.org/abs/2511.20409) proposes measures for stemming utility, downstream performance change and semantic damage. NormGuard must not present task-oriented evaluation alone as novel.

The combination still requiring validation is:

1. adapters across multiple normalisation engines;
2. developer-supplied retrieval tasks and relevance labels;
3. terminology-safety analysis;
4. separate lexical, dense, hybrid and RAG comparisons;
5. uncertainty-aware decisions rather than a forced winner;
6. CI-compatible baseline and regression evidence.

## Remaining Phase 1 landscape work

- Expand the map to at least 15 credible tools, products or papers.
- Review search relevance testing products and commercial platforms.
- Search GitHub and package registries for close open-source implementations.
- Record licences, maintenance signals, language coverage and integration cost.
- Verify whether current RAG evaluation frameworks can express the proposed normalisation comparison.
- Produce a dated novelty assessment without claiming exhaustive patent clearance.
