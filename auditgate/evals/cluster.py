"""Cluster eval failures to find systemic weaknesses instead of fixing cases one by one.

Twenty failures often share three root causes. Turn each failure into text, embed it,
group similar ones with k-means, and read each cluster's top terms:

    cluster 1 (7 failures): leaked, email, dot      -> obfuscated emails slip past the sanitizer
    cluster 2 (4 failures): scope_items, amount     -> bid parsing breaks on some layouts

Embeddings: TF-IDF by default (offline, deterministic, good enough for short error
messages). Pass ``embed=openai_embedder(...)`` to use a provider's embedding model -
including a local one such as Ollama's nomic-embed-text.

    uv run python -m auditgate.evals.runner --dataset all --report doc_report.json
    uv run python -m auditgate.evals.agent_runner --provider anthropic --report agent_report.json
    uv run python -m auditgate.evals.cluster doc_report.json agent_report.json -k 3
"""

from __future__ import annotations

import json
import math
import random
import re
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import typer

from auditgate.evals.metrics import cosine

Vector = list[float]
Embedder = Callable[[list[str]], list[Vector]]
STOPWORDS = {"the", "a", "an", "of", "to", "is", "in", "and", "or", "for", "got", "expected", "none", "with", "on"}


@dataclass
class Failure:
    case_id: str
    text: str


@dataclass
class Cluster:
    members: list[Failure]
    top_terms: list[str]


def failures_from_report(data: dict) -> list[Failure]:
    """Extract failures from a document-eval or agent-eval JSON report."""
    out: list[Failure] = []
    for case in data.get("cases", []):
        msgs = list(case.get("field_errors", []))
        msgs += [f"missing finding {c}" for c in case.get("missing_findings", [])]
        msgs += [f"leaked pii {v}" for v in case.get("leaked", [])]
        if case.get("status_expected") and case.get("status_expected") != case.get("status_actual"):
            msgs.append(f"status {case['status_actual']} instead of {case['status_expected']}")
        msgs += [a["failure"] for a in case.get("attempts", []) if a.get("failure")]
        out += [Failure(case["id"], m) for m in msgs]
    return out


def tokenize(text: str) -> list[str]:
    words = re.findall(r"[a-z_]+", text.lower().replace(".", " "))
    return [w for w in words if w not in STOPWORDS and len(w) > 1]


def tfidf(texts: Sequence[str]) -> list[Vector]:
    """L2-normalised TF-IDF vectors over the vocabulary of `texts`."""
    docs = [tokenize(t) for t in texts]
    vocab = sorted({w for d in docs for w in d})
    index = {w: i for i, w in enumerate(vocab)}
    df = Counter(w for d in docs for w in set(d))
    n = len(docs)
    vectors = []
    for d in docs:
        vec = [0.0] * len(vocab)
        for word, count in Counter(d).items():
            vec[index[word]] = count * (math.log((1 + n) / (1 + df[word])) + 1)
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        vectors.append([x / norm for x in vec])
    return vectors


def kmeans(vectors: Sequence[Vector], k: int, *, iterations: int = 25, seed: int = 0) -> list[int]:
    """Cosine k-means with k-means++ seeding. Deterministic for a given seed. Returns a label per vector."""
    if not vectors:
        return []
    k = max(1, min(k, len(vectors)))
    rng = random.Random(seed)
    centroids = [list(vectors[rng.randrange(len(vectors))])]
    while len(centroids) < k:  # k-means++: favour points far from existing centroids
        dist = [min(1 - cosine(v, c) for c in centroids) for v in vectors]
        total = sum(dist)
        if total == 0:
            break
        pick, acc = rng.random() * total, 0.0
        for v, d in zip(vectors, dist):
            acc += d
            if acc >= pick:
                centroids.append(list(v))
                break
    labels = [0] * len(vectors)
    for _ in range(iterations):
        new = [max(range(len(centroids)), key=lambda c: cosine(v, centroids[c])) for v in vectors]
        if new == labels and _ > 0:
            break
        labels = new
        for c in range(len(centroids)):
            members = [v for v, lab in zip(vectors, labels) if lab == c]
            if members:
                centroids[c] = [sum(col) / len(members) for col in zip(*members)]
    return labels


def cluster_failures(failures: list[Failure], k: int = 3, embed: Embedder | None = None,
                     top_n: int = 4) -> list[Cluster]:
    if not failures:
        return []
    texts = [f.text for f in failures]
    labels = kmeans(embed(texts) if embed else tfidf(texts), k)
    clusters = []
    for label in sorted(set(labels)):
        members = [f for f, lab in zip(failures, labels) if lab == label]
        terms = Counter(w for m in members for w in set(tokenize(m.text)))
        clusters.append(Cluster(members, [w for w, _ in terms.most_common(top_n)]))
    return sorted(clusters, key=lambda c: len(c.members), reverse=True)


def openai_embedder(model: str = "text-embedding-3-small", *, base_url: str | None = None,
                    api_key: str | None = None) -> Embedder:
    """Embeddings from OpenAI or any OpenAI-compatible server (e.g. Ollama with nomic-embed-text)."""
    from openai import OpenAI

    client = OpenAI(base_url=base_url, api_key=api_key)

    def embed(texts: list[str]) -> list[Vector]:
        return [d.embedding for d in client.embeddings.create(model=model, input=texts).data]

    return embed


def main(reports: list[Path], k: int = typer.Option(3, "-k", help="Number of clusters")) -> None:
    failures = [f for path in reports for f in failures_from_report(json.loads(path.read_text(encoding="utf-8")))]
    if not failures:
        typer.echo("No failures to cluster.")
        return
    for i, cluster in enumerate(cluster_failures(failures, k), start=1):
        typer.echo(f"\nCluster {i} ({len(cluster.members)} failures) - top terms: {', '.join(cluster.top_terms)}")
        for f in cluster.members[:6]:
            typer.echo(f"   {f.case_id}: {f.text}")


if __name__ == "__main__":
    typer.run(main)
