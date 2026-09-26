"""Deterministic BM25 retrieval and paired evaluation for NG-POC-001."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any

import bm25s
import ir_measures

from .adapters import NormalisationAdapter

_ALLOWED_PARTITIONS = {"calibration", "development"}
_MEASURES = (ir_measures.nDCG @ 10, ir_measures.RR @ 10, ir_measures.P @ 10,
             ir_measures.Recall @ 10, ir_measures.Recall @ 100)


@dataclass(frozen=True, slots=True)
class RetrievalDocument:
    document_id: str
    text: str


@dataclass(frozen=True, slots=True)
class RetrievalQuery:
    query_id: str
    text: str
    partition: str


@dataclass(frozen=True, slots=True)
class RelevanceJudgement:
    query_id: str
    document_id: str
    relevance: int


@dataclass(frozen=True, slots=True)
class RankedHit:
    document_id: str
    rank: int
    score: float
    relevance: int


@dataclass(frozen=True, slots=True)
class QueryEvaluation:
    query_id: str
    source_query: str
    normalised_query: str
    hits: tuple[RankedHit, ...]
    metrics: dict[str, float]


@dataclass(frozen=True, slots=True)
class RetrievalEvaluation:
    configuration_id: str
    partition: str
    k1: float
    b: float
    queries: tuple[QueryEvaluation, ...]
    aggregate_metrics: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class PairedComparison:
    baseline_configuration_id: str
    candidate_configuration_id: str
    query_ids: tuple[str, ...]
    metric_deltas: dict[str, tuple[float, ...]]
    mean_deltas: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class BM25EvaluationHarness:
    """Run one validated partition with a fixed adapter and BM25 configuration."""

    def __init__(self, *, k1: float, b: float, top_k: int = 100) -> None:
        if k1 <= 0 or not 0 <= b <= 1 or top_k <= 0:
            raise ValueError("require k1 > 0, 0 <= b <= 1, and top_k > 0")
        self.k1 = k1
        self.b = b
        self.top_k = top_k

    def evaluate(
        self,
        *,
        adapter: NormalisationAdapter,
        documents: Sequence[RetrievalDocument],
        queries: Sequence[RetrievalQuery],
        qrels: Sequence[RelevanceJudgement],
        partition: str,
    ) -> RetrievalEvaluation:
        self._validate(documents, queries, qrels, partition)
        document_ids = tuple(document.document_id for document in documents)
        normalised_documents = [
            adapter.normalise(document.text).output_text for document in documents
        ]
        corpus_tokens = bm25s.tokenize(
            normalised_documents, stopwords=[], stemmer=None, show_progress=False
        )
        retriever = bm25s.BM25(method="lucene", k1=self.k1, b=self.b)
        retriever.index(corpus_tokens, show_progress=False)

        normalised_queries = [adapter.normalise(query.text).output_text for query in queries]
        query_tokens = bm25s.tokenize(
            normalised_queries, stopwords=[], stemmer=None, show_progress=False
        )
        result_ids, scores = retriever.retrieve(
            query_tokens,
            k=min(self.top_k, len(documents)),
            show_progress=False,
        )
        relevance = {(q.query_id, q.document_id): q.relevance for q in qrels}
        run: list[ir_measures.ScoredDoc] = []
        query_evaluations: list[QueryEvaluation] = []
        metric_rows = self._metric_rows(queries, document_ids, result_ids, scores, qrels)

        for query_index, query in enumerate(queries):
            hits: list[RankedHit] = []
            for rank, (document_index, score) in enumerate(
                zip(result_ids[query_index], scores[query_index], strict=True), start=1
            ):
                document_id = document_ids[int(document_index)]
                score_value = float(score)
                run.append(ir_measures.ScoredDoc(query.query_id, document_id, score_value))
                hits.append(
                    RankedHit(
                        document_id=document_id,
                        rank=rank,
                        score=score_value,
                        relevance=relevance.get((query.query_id, document_id), 0),
                    )
                )
            query_evaluations.append(
                QueryEvaluation(
                    query_id=query.query_id,
                    source_query=query.text,
                    normalised_query=normalised_queries[query_index],
                    hits=tuple(hits),
                    metrics=metric_rows[query.query_id],
                )
            )

        qrel_records = [
            ir_measures.Qrel(q.query_id, q.document_id, q.relevance) for q in qrels
        ]
        aggregate = {
            _metric_name(measure): float(value)
            for measure, value in ir_measures.calc_aggregate(_MEASURES, qrel_records, run).items()
        }
        return RetrievalEvaluation(
            configuration_id=adapter.metadata.configuration_id,
            partition=partition,
            k1=self.k1,
            b=self.b,
            queries=tuple(query_evaluations),
            aggregate_metrics=aggregate,
        )

    @staticmethod
    def _metric_rows(
        queries: Sequence[RetrievalQuery],
        document_ids: tuple[str, ...],
        result_ids: Any,
        scores: Any,
        qrels: Sequence[RelevanceJudgement],
    ) -> dict[str, dict[str, float]]:
        qrel_records = [
            ir_measures.Qrel(q.query_id, q.document_id, q.relevance) for q in qrels
        ]
        run = [
            ir_measures.ScoredDoc(query.query_id, document_ids[int(document_index)], float(score))
            for query_index, query in enumerate(queries)
            for document_index, score in zip(
                result_ids[query_index], scores[query_index], strict=True
            )
        ]
        rows = {query.query_id: {} for query in queries}
        for item in ir_measures.iter_calc(_MEASURES, qrel_records, run):
            rows[item.query_id][_metric_name(item.measure)] = float(item.value)
        return rows

    @staticmethod
    def _validate(
        documents: Sequence[RetrievalDocument],
        queries: Sequence[RetrievalQuery],
        qrels: Sequence[RelevanceJudgement],
        partition: str,
    ) -> None:
        if partition not in _ALLOWED_PARTITIONS:
            raise ValueError("only calibration and development partitions may be evaluated")
        if not documents or not queries or not qrels:
            raise ValueError("documents, queries and qrels must not be empty")
        document_ids = [item.document_id for item in documents]
        query_ids = [item.query_id for item in queries]
        if len(document_ids) != len(set(document_ids)):
            raise ValueError("duplicate document_id")
        if len(query_ids) != len(set(query_ids)):
            raise ValueError("duplicate query_id")
        if any(query.partition != partition for query in queries):
            raise ValueError("query partition mismatch or split leakage")
        known_documents = set(document_ids)
        known_queries = set(query_ids)
        seen_qrels: set[tuple[str, str]] = set()
        for judgement in qrels:
            key = (judgement.query_id, judgement.document_id)
            if judgement.query_id not in known_queries:
                raise ValueError("qrel references an unknown query")
            if judgement.document_id not in known_documents:
                raise ValueError("qrel references a missing document")
            if judgement.relevance < 0:
                raise ValueError("qrel relevance must be non-negative")
            if key in seen_qrels:
                raise ValueError("duplicate qrel")
            seen_qrels.add(key)


def compare_paired(
    baseline: RetrievalEvaluation,
    candidate: RetrievalEvaluation,
) -> PairedComparison:
    """Align per-query metrics and return candidate-minus-baseline deltas."""

    if baseline.partition != candidate.partition:
        raise ValueError("paired evaluations must use the same partition")
    baseline_rows = {row.query_id: row for row in baseline.queries}
    candidate_rows = {row.query_id: row for row in candidate.queries}
    if baseline_rows.keys() != candidate_rows.keys():
        raise ValueError("paired evaluations must contain identical query IDs")
    query_ids = tuple(sorted(baseline_rows))
    metric_names = set.intersection(
        *(set(baseline_rows[query_id].metrics) for query_id in query_ids),
        *(set(candidate_rows[query_id].metrics) for query_id in query_ids),
    )
    deltas = {
        metric: tuple(
            candidate_rows[query_id].metrics[metric]
            - baseline_rows[query_id].metrics[metric]
            for query_id in query_ids
        )
        for metric in sorted(metric_names)
    }
    means = {
        metric: sum(values) / len(values)
        for metric, values in deltas.items()
    }
    return PairedComparison(
        baseline_configuration_id=baseline.configuration_id,
        candidate_configuration_id=candidate.configuration_id,
        query_ids=query_ids,
        metric_deltas=deltas,
        mean_deltas=means,
    )


def _metric_name(measure: Any) -> str:
    name = str(measure)
    prefix, cutoff = name.split("@", maxsplit=1)
    labels = {"nDCG": "ndcg", "RR": "mrr", "P": "precision", "R": "recall"}
    return f"{labels[prefix]}_at_{cutoff}"
