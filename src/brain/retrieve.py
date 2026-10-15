"""Hybrid retrieval — the R in RAG (doc/BRAIN_DESIGN.md §5).

1. Spot exact terms in the question (cmdlets, CVE IDs, error codes, event IDs, versions, flags).
2. Meaning search: embed the question -> nearest chunks by cosine similarity (pgvector HNSW).
3. Keyword search: full-text match on the question's meaningful words; chunks containing ALL
   exact terms are ranked first.
4. Merge both ranked lists with Reciprocal Rank Fusion (RRF, k=60).
5. Keep only active documents the account may see (row-level security does the access part).
6. Diversify: at most 2 passages per document, within a token budget for the answer prompt.
7. Evidence check: say "not enough evidence" instead of handing the model weak passages.
"""

from __future__ import annotations

import re
import time
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from pgvector import Vector
from psycopg.rows import dict_row

from src.brain.chunking import estimate_tokens
from src.brain.db import connect
from src.brain.embed import embed_query
from src.config import Config

Mode = Literal["hybrid", "vector", "keyword"]

CANDIDATES = 30            # per search method, before merging
RRF_K = 60                 # standard RRF constant: dampens the weight of the very top ranks
# Fusion weights — measured on data/brain_eval (2026-10-15, 40 in-scope questions):
#   vector only MRR 0.88 · equal-weight RRF 0.81 · broad keyword 0.1–0.2: 0.90 · broad 0 + exact 1: 0.93
# Broad keyword matches ("dns OR server OR …") pulled correct meaning matches down, so they
# only stay in the candidate pool (fallback). Passages containing every exact identifier
# (cmdlet, CVE, error code) get a full extra vote: identifier questions went from MRR 0.79 to 1.00.
VECTOR_WEIGHT = 1.0
KEYWORD_WEIGHT = 0.0
EXACT_WEIGHT = 1.0
MAX_PER_DOCUMENT = 2
MAX_PASSAGES = 8
TOKEN_BUDGET = 3000        # passages must fit the 8K context with question + answer
# Evidence threshold on the best meaning match. Eval 2026-10-15: answerable questions ≥ 0.526,
# unrelated questions ≤ 0.451 → midpoint. Re-tune whenever sources or the embedding model change.
MIN_SIMILARITY = 0.49
HNSW_EF_SEARCH = 100

# Identifiers specific enough that a document containing them is evidence on its own
_STRONG_EXACT = [
    re.compile(r"\bCVE-\d{4}-\d{4,7}\b", re.IGNORECASE),
    re.compile(r"\b0x[0-9a-f]{4,8}\b", re.IGNORECASE),                      # error codes
    re.compile(r"\b(?:Get|Set|Add|Remove|New|Enable|Disable|Test|Clear|Start|Stop|Restart|"
               r"Register|Unregister|Install|Uninstall|Import|Export|Invoke|Show|Resolve|Update|"
               r"Reset|Sync|Move|Rename|Suspend|Resume|Grant|Revoke)-[A-Za-z]+\b"),  # PowerShell cmdlets
    re.compile(r"\bKB\d{6,8}\b", re.IGNORECASE),
    re.compile(r"(?<![\w-])--?[a-z][a-z0-9-]{2,}\b"),                         # command-line flags
]
# Exact tokens that help ranking but are too common to prove relevance (years, ports, versions)
_WEAK_EXACT = [
    re.compile(r"\b\d+\.\d+\.\d+(?:\.\d+)?\b"),
    re.compile(r"\b\d{3,5}\b"),
]

# Words that carry no search meaning (English + common Hinglish)
_STOPWORDS = frozenset("""
a an the and or but if then of to in on at by for from with without about into over under is are was were
be been being do does did doing have has had i me my we our you your he she it its they them their this that
these those what which who whom whose when where why how can could should would will shall may might must
not no yes please tell show give explain want need get got make use using used via vs etc also just only
kya kyu kyun kaise kese kaisa kaun kab kahan kidhar kitna kitne hai hain ho hota hoti hote hoga hogi honge
tha thi the ka ki ke ko se me mein main pe par aur ya bhi to toh na nahi nahin jab tab liye wala wali wale
ek koi kuch sab apna apne apni aap hum mujhe mera meri mere ye yeh wo woh is us karna karo karein kare kar
karte karta karti raha rahe rahi sakte sakta sakti chahiye batao bataye bataiye
""".split())

_WORD = re.compile(r"[\w][\w.-]*")


@dataclass
class Passage:
    chunk_id: int
    document_id: int
    title: str
    url: str | None
    heading_path: str | None
    content: str
    token_count: int
    updated_at: datetime | None
    source: str
    publisher: str | None
    licence: str | None
    score: float                       # fused RRF score
    similarity: float | None = None    # cosine similarity, when found by meaning search
    vector_rank: int | None = None
    keyword_rank: int | None = None


@dataclass
class SearchResult:
    query: str
    mode: Mode
    passages: list[Passage]
    enough_evidence: bool
    best_similarity: float | None
    exact_terms: list[str]
    exact_match: bool                  # a passage contains every strong exact term
    timings_ms: dict[str, float] = field(default_factory=dict)


def _find(patterns: list[re.Pattern], text: str) -> list[str]:
    found: dict[str, None] = {}
    for pattern in patterns:
        for match in pattern.finditer(text):
            found.setdefault(match.group(0).lstrip("-"))
    return list(found)


def detect_exact_terms(question: str) -> tuple[list[str], list[str]]:
    """(strong identifiers, weak exact tokens) found in the question."""
    strong = _find(_STRONG_EXACT, question)
    weak = [t for t in _find(_WEAK_EXACT, question) if all(t not in s for s in strong)]
    return strong, weak


def keyword_query(question: str) -> str:
    """websearch_to_tsquery text: meaningful words joined with OR (leading '-' stripped: it means NOT)."""
    words: dict[str, None] = {}
    for word in _WORD.findall(question.lower()):
        word = word.strip(".-")
        if len(word) >= 2 and word not in _STOPWORDS:
            words.setdefault(word)
    return " OR ".join(words)


def rrf_merge(
    *ranked_lists: Iterable[int], k: int = RRF_K, weights: Iterable[float] | None = None
) -> list[tuple[int, float]]:
    """Weighted Reciprocal Rank Fusion: score = sum of weight / (k + rank) over the lists."""
    lists = list(ranked_lists)
    weight_list = list(weights) if weights is not None else [1.0] * len(lists)
    scores: dict[int, float] = defaultdict(float)
    for ranked, weight in zip(lists, weight_list):
        for rank, item in enumerate(ranked, 1):
            scores[item] += weight / (k + rank)
    return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)


def _collection_filter(collections: list[str] | None) -> tuple[str, list]:
    if not collections:
        return "", []
    return " AND c.collection_id IN (SELECT id FROM brain.collections WHERE slug = ANY(%s))", [collections]


def _vector_search(conn, vector: Vector, collections: list[str] | None) -> list[tuple[int, float]]:
    where, params = _collection_filter(collections)
    conn.execute(f"SET LOCAL hnsw.ef_search = {HNSW_EF_SEARCH}")
    conn.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
    # Inner query can use the HNSW index (one table); the join then drops superseded/removed docs.
    rows = conn.execute(
        f"""SELECT n.id, n.similarity FROM (
                SELECT c.id, c.document_id, 1 - (c.embedding <=> %s) AS similarity
                FROM brain.chunks c WHERE true{where}
                ORDER BY c.embedding <=> %s LIMIT %s
            ) n JOIN brain.documents d ON d.id = n.document_id AND d.status = 'active'
            ORDER BY n.similarity DESC LIMIT %s""",
        [vector, *params, vector, CANDIDATES * 3, CANDIDATES],
    ).fetchall()
    return [(cid, float(sim)) for cid, sim in rows]


def _keyword_search(conn, query_text: str, collections: list[str] | None) -> list[int]:
    if not query_text:
        return []
    where, params = _collection_filter(collections)
    rows = conn.execute(
        f"""SELECT c.id FROM brain.chunks c
            JOIN brain.documents d ON d.id = c.document_id AND d.status = 'active',
                 websearch_to_tsquery('simple', %s) q
            WHERE c.tsv @@ q{where}
            ORDER BY ts_rank_cd(c.tsv, q, 1) DESC, c.id LIMIT %s""",
        [query_text, *params, CANDIDATES],
    ).fetchall()
    return [cid for (cid,) in rows]


def _details(conn, chunk_ids: list[int]) -> dict[int, dict]:
    if not chunk_ids:
        return {}
    cur = conn.cursor(row_factory=dict_row)
    rows = cur.execute(
        """SELECT c.id AS chunk_id, c.document_id, d.title, d.url, c.heading_path, c.content,
                  c.token_count, d.updated_at, s.slug AS source, s.publisher, s.licence
           FROM brain.chunks c
           JOIN brain.documents d ON d.id = c.document_id
           JOIN brain.sources s ON s.id = d.source_id
           WHERE c.id = ANY(%s)""",
        (chunk_ids,),
    ).fetchall()
    return {row["chunk_id"]: row for row in rows}


def _select(fused: list[tuple[int, float]], details: dict[int, dict], limit: int, budget: int,
            similarity: dict[int, float], vec_rank: dict[int, int], kw_rank: dict[int, int]) -> list[Passage]:
    """Best-first, max MAX_PER_DOCUMENT per document, within the token budget."""
    chosen: list[Passage] = []
    per_doc: dict[int, int] = defaultdict(int)
    used = 0
    for chunk_id, score in fused:
        row = details.get(chunk_id)
        if row is None or per_doc[row["document_id"]] >= MAX_PER_DOCUMENT:
            continue
        tokens = row["token_count"] or estimate_tokens(row["content"])
        if chosen and used + tokens > budget:
            continue  # a smaller passage further down may still fit
        chosen.append(Passage(
            **row, score=score, similarity=similarity.get(chunk_id),
            vector_rank=vec_rank.get(chunk_id), keyword_rank=kw_rank.get(chunk_id),
        ))
        per_doc[row["document_id"]] += 1
        used += tokens
        if len(chosen) >= limit:
            break
    return chosen


def search(
    question: str,
    *,
    config: Config | None = None,
    account_id: str | None = None,
    collections: list[str] | None = None,
    mode: Mode = "hybrid",
    limit: int = MAX_PASSAGES,
    token_budget: int = TOKEN_BUDGET,
    embed: Callable[[str], list[float]] | None = None,
) -> SearchResult:
    """Find the passages that best answer `question`, visible to `account_id`."""
    config = config or Config.from_env()
    question = question.strip()
    timings: dict[str, float] = {}
    start = time.perf_counter()
    strong, weak = detect_exact_terms(question)

    vector_hits: list[tuple[int, float]] = []
    keyword_hits: list[int] = []
    exact_hits: list[int] = []
    broad: list[int] = []
    query_vector = None
    if mode in ("hybrid", "vector"):
        t = time.perf_counter()
        query_vector = Vector((embed or (lambda q: embed_query(q, config)))(question))
        timings["embed_ms"] = round((time.perf_counter() - t) * 1000, 1)

    with connect(config, account_id=account_id) as conn:
        if query_vector is not None:
            t = time.perf_counter()
            vector_hits = _vector_search(conn, query_vector, collections)
            timings["vector_ms"] = round((time.perf_counter() - t) * 1000, 1)
        if mode in ("hybrid", "keyword"):
            t = time.perf_counter()
            if strong:
                # passages containing every strong identifier go first (websearch: space = AND)
                exact_hits = _keyword_search(conn, " ".join(f'"{term}"' for term in strong), collections)
            broad = _keyword_search(conn, keyword_query(question), collections)
            first = set(exact_hits)
            keyword_hits = exact_hits + [cid for cid in broad if cid not in first]
            timings["keyword_ms"] = round((time.perf_counter() - t) * 1000, 1)

        vec_ids = [cid for cid, _ in vector_hits]
        if mode == "keyword":
            fused = rrf_merge(keyword_hits[:CANDIDATES])
        else:
            fused = rrf_merge(
                vec_ids, exact_hits, broad,
                weights=(VECTOR_WEIGHT, EXACT_WEIGHT, KEYWORD_WEIGHT),
            )
        details = _details(conn, [cid for cid, _ in fused])

    similarity = dict(vector_hits)
    passages = _select(
        fused, details, limit, token_budget, similarity,
        {cid: i for i, cid in enumerate(vec_ids, 1)},
        {cid: i for i, cid in enumerate(keyword_hits, 1)},
    )
    best = max(similarity.values()) if similarity else None
    exact_match = bool(exact_hits)
    if mode == "keyword":
        enough = bool(passages)
    else:
        enough = bool(passages) and ((best is not None and best >= MIN_SIMILARITY) or exact_match)
    timings["total_ms"] = round((time.perf_counter() - start) * 1000, 1)
    return SearchResult(
        query=question, mode=mode, passages=passages, enough_evidence=enough,
        best_similarity=round(best, 4) if best is not None else None,
        exact_terms=strong + weak, exact_match=exact_match, timings_ms=timings,
    )
