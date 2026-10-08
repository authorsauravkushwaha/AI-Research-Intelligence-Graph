"""Claim extraction & conflict detection.

Two implementations, deliberately:
  * `resolve_locally` — pure Python, always available (used offline and in tests)
  * `resolve_via_ruby` — calls the Ruby worker (`polyglot/ruby/claim_resolver.rb`)
    when `NEXUS_CLAIM_URL` points at a running copy of it.

Both use the SAME additive, auditable rule set, so the reasoning a user sees in
the UI ("polarity differs", "effect direction is opposite") is identical
whichever engine ran. Nothing here asserts a contradiction as fact: it produces
*potential contradiction* candidates with the sentence-level evidence attached.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import httpx

from backend.config import REPO_ROOT, get_settings

log = logging.getLogger("nexus.claims")

#: Negation is a *construction*, not a keyword: "the method fails" is a plain
#: statement, while "the method fails to generalise" negates generalisation.
NEGATION_PATTERNS = [
    r"\bnot\b", r"\bno\b", r"\bnever\b", r"\bcannot\b", r"\bcan't\b",
    r"\b(don|doesn|didn|isn|aren|wasn|weren)'t\b", r"\bwithout\b", r"\bunable\b",
    r"\bnor\b", r"\bneither\b", r"\bfails? to\b", r"\bfailed to\b",
    r"\black(s|ing)?\b", r"\binsufficient\b", r"\bno significant\b",
    r"\bdoes not\b", r"\bdo not\b",
]
NEGATION_RE = re.compile("|".join(NEGATION_PATTERNS), re.IGNORECASE)

HEDGES_WEAK = {
    "may", "might", "could", "possibly", "perhaps", "potentially", "suggests", "suggest",
    "indicate", "indicates", "preliminary", "tentatively", "unclear", "inconclusive", "some",
    "partially", "modest", "slight", "marginally", "occasionally", "weaker",
}

HEDGES_STRONG = {
    "demonstrates", "demonstrate", "proves", "prove", "establishes", "established", "shows",
    "show", "confirms", "confirm", "significant", "significantly", "consistently", "robust",
    "substantially", "clearly", "always", "outperforms", "surpasses", "exceeds",
}

#: Opposing semantic axes. Tokens are normalised (plural/gerund stripped) before
#: matching, so "improves" and "improve" compare equal.
DIRECTION_PAIRS: list[tuple[str, str]] = [
    ("increase", "decrease"), ("improve", "degrade"), ("improve", "reduce"),
    ("improve", "harm"), ("increase", "reduce"), ("higher", "lower"),
    ("better", "worse"), ("positive", "negative"), ("outperform", "underperform"),
    # "fail" is deliberately NOT a direction pole: "fail to generalise" is a negation
    # of generalising, not the opposite pole of "improve", and counting it as both
    # double-scored those pairs. It stays in NEGATION_PATTERNS.
    ("help", "harm"),
    ("faster", "slower"), ("more", "less"), ("enable", "prevent"),
    ("support", "undermine"), ("gain", "loss"), ("strong", "weak"),
    ("robust", "fragile"), ("effective", "ineffective"), ("benefit", "harm"),
    ("necessary", "unnecessary"), ("reduce", "worsen"),
]

STOPWORDS = {
    "the", "a", "an", "of", "in", "on", "for", "with", "to", "and", "or", "is", "are", "was",
    "were", "be", "been", "being", "that", "this", "these", "those", "it", "its", "as", "at",
    "by", "from", "into", "over", "under", "we", "our", "their", "they", "them", "can", "could",
    "may", "might", "not", "no", "do", "does", "did", "has", "have", "had", "will", "would", "should",
}


@dataclass(slots=True)
class ParsedClaim:
    id: str
    text: str
    subject: str
    predicate: str
    direction: str | None
    negated: bool
    strength: str
    tokens: list[str]
    paper_id: str | None = None
    source: str = "corpus"

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "subject": self.subject,
            "predicate": self.predicate,
            "direction": self.direction,
            "negated": self.negated,
            "strength": self.strength,
            "paper_id": self.paper_id,
            "source": self.source,
        }


@dataclass(slots=True)
class Conflict:
    claim_a: str
    claim_b: str
    score: float
    kind: str
    reasons: list[str] = field(default_factory=list)
    paper_a: str | None = None
    paper_b: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "claim_a": self.claim_a,
            "claim_b": self.claim_b,
            "paper_a": self.paper_a,
            "paper_b": self.paper_b,
            "score": self.score,
            "kind": self.kind,
            "reasons": self.reasons,
        }


def tokenize(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9][a-z0-9\-_.]{1,}", text.lower()) if t not in STOPWORDS]


def _content(tokens: Iterable[str]) -> list[str]:
    return [t for t in tokens if len(t) > 3]


def _direction(tokens: list[str]) -> str | None:
    """Find the effect-direction word on the sentence's semantic axis.

    Both the raw and the normalised forms are matched, and the pair members are
    normalised too, so inflections cannot hide an opposing direction: without this,
    "degrades" normalises to "degrad" and stops matching the "improve/degrade" axis
    — which silently dropped a real directional signal from the score. The Ruby
    reference implementation (polyglot/ruby/claim_resolver.rb) compares raw and
    inflected forms and must agree with this function.
    """
    # Match raw, normalised and de-pluralised forms on both sides, so an inflected
    # verb ("degrades") cannot hide the axis it belongs to. The Ruby reference
    # implementation performs the same three-way comparison.
    tok_set = {normalise(t) for t in tokens} | set(tokens) | {t.rstrip("s") for t in tokens if len(t) > 4}
    for a, b in DIRECTION_PAIRS:
        if a in tok_set or normalise(a) in tok_set:
            return a
        if b in tok_set or normalise(b) in tok_set:
            return b
    return None


def _strength(tokens: list[str]) -> str:
    strong = sum(1 for t in tokens if t in HEDGES_STRONG)
    weak = sum(1 for t in tokens if t in HEDGES_WEAK)
    if strong and not weak:
        return "strong"
    if weak and not strong:
        return "weak"
    return "moderate"


def normalise(token: str) -> str:
    """Very light stemming so 'improves'/'improve' and 'scales'/'scale' match.

    Deliberately conservative: it only strips suffixes that are unambiguous for
    the claim vocabulary used here, and every normalised token stays visible in
    the evidence the UI shows.
    """
    for suffix in ("ies",):
        if token.endswith(suffix) and len(token) > 4:
            return token[: -len(suffix)] + "y"
    for suffix in ("ing", "ed", "es", "s"):
        if token.endswith(suffix) and len(token) - len(suffix) >= 4:
            return token[: -len(suffix)]
    return token


def parse_claim(claim_id: str, text: str, paper_id: str | None = None, source: str = "corpus") -> ParsedClaim:
    tokens = tokenize(text)
    content = _content(tokens)
    return ParsedClaim(
        id=claim_id,
        text=text,
        subject=" ".join(content[:3]),
        predicate=" ".join(content[3:6]),
        direction=_direction(tokens),
        negated=bool(NEGATION_RE.search(text)),
        strength=_strength(tokens),
        tokens=tokens,
        paper_id=paper_id,
        source=source,
    )


def _opposing(direction_a: str | None, direction_b: str | None) -> bool:
    if not direction_a or not direction_b:
        return False
    return any(
        (direction_a in pair and direction_b in pair and direction_a != direction_b)
        for pair in DIRECTION_PAIRS
    )


def _shared_content(a: ParsedClaim, b: ParsedClaim) -> tuple[set[str], float]:
    set_a = {normalise(t) for t in _content(a.tokens)}
    set_b = {normalise(t) for t in _content(b.tokens)}
    shared = set_a & set_b
    overlap = len(shared) / min(len(set_a), len(set_b)) if set_a and set_b else 0.0
    return shared, overlap


def opposition_gate(a: ParsedClaim, b: ParsedClaim) -> tuple[bool, str | None, str]:
    """Decide whether two claims are even *candidates* for contradiction.

    This gate is deliberately conservative, because a false "these papers
    disagree" flag is worse than a missed one. A pair qualifies when:

      A) polarity opposition with at least one shared concept token, or
      B) opposing effect direction with >=2 shared concept tokens and
         >=20% lexical overlap (they must be talking about the same thing), or
      C) the two claims state the same proposition (identical subject+predicate).

    Returns (candidate, kind, explanation).
    """
    if a.paper_id and a.paper_id == b.paper_id:
        return False, None, "same paper — not a contradiction candidate"
    shared, overlap = _shared_content(a, b)
    same_proposition = bool(a.subject) and a.subject == b.subject and a.predicate == b.predicate

    if a.negated != b.negated and (same_proposition or (len(shared) >= 2 and overlap >= 0.2)):
        return True, "polarity", (
            f"polarity opposition with {len(shared)} shared concept token(s) ({overlap:.0%} overlap)"
        )
    if _opposing(a.direction, b.direction) and len(shared) >= 2 and overlap >= 0.2:
        return True, "directional", (
            f"opposing direction on the same axis with {len(shared)} shared tokens ({overlap:.0%} overlap)"
        )
    if same_proposition and a.negated != b.negated:
        return True, "polarity", "same proposition stated with and without negation"
    return False, None, "no explicit opposition signal"


def score_pair(a: ParsedClaim, b: ParsedClaim) -> Conflict:
    """Additive, explainable conflict score (0..1).

    Weights are fixed and published in the UI:
        +0.45  polarity opposition (one claim negated, the other not)
        +0.35  opposing effect direction on the same axis
        +0.20  same subject/predicate (they really are about the same thing)
        +0.10  both claims use assertive language
        +0.15 * lexical overlap  (supporting signal only)
        -0.20  same polarity AND same direction  (corroboration, not conflict)
    """
    reasons: list[str] = []
    score = 0.0
    opposition, kind, _why = opposition_gate(a, b)
    if not opposition:
        return Conflict(
            claim_a=a.id, claim_b=b.id, score=0.0, kind="none", reasons=[],
            paper_a=a.paper_id, paper_b=b.paper_id,
        )

    if a.negated != b.negated:
        score += 0.45
        reasons.append(f"Polarity differs: one claim is negated ({a.id if a.negated else b.id}).")
    if _opposing(a.direction, b.direction):
        score += 0.35
        reasons.append(f"Effect direction is opposite: '{a.direction}' vs '{b.direction}'.")
    if a.subject and a.subject == b.subject and a.predicate == b.predicate:
        score += 0.20
        reasons.append(f"Both claims address the same subject/predicate ({a.subject} / {a.predicate}).")
    if a.strength == "strong" and b.strength == "strong":
        score += 0.10
        reasons.append("Both claims use assertive language, so the disagreement is material.")
    if not a.negated and not b.negated and a.direction and a.direction == b.direction:
        score -= 0.20
        reasons.append("Same polarity and same direction — treated as corroboration, not conflict.")

    shared, overlap = _shared_content(a, b)
    if overlap > 0:
        score += 0.15 * overlap
        reasons.append(f"{len(shared)} shared concept tokens ({overlap:.0%} lexical overlap).")

    return Conflict(
        claim_a=a.id,
        claim_b=b.id,
        score=round(max(0.0, min(1.0, score)), 3),
        kind=kind or "contextual",
        reasons=reasons,
        paper_a=a.paper_id,
        paper_b=b.paper_id,
    )


def group_key(claim: ParsedClaim) -> str:
    return f"{claim.subject}|{claim.predicate}"


def resolve_locally(records: list[dict[str, Any]], min_score: float = 0.35) -> dict[str, Any]:
    """Parse every claim, then score all within-group pairs."""
    parsed = [
        parse_claim(r.get("id", f"claim:{i}"), r["text"], r.get("paper"), r.get("source", "corpus"))
        for i, r in enumerate(records)
        if r.get("text")
    ]
    groups: dict[str, list[ParsedClaim]] = {}
    for claim in parsed:
        groups.setdefault(group_key(claim), []).append(claim)

    conflicts: list[Conflict] = []
    for group in groups.values():
        if len(group) < 2:
            continue
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                conflict = score_pair(a, b)
                if conflict.score >= min_score:
                    conflicts.append(conflict)

    # claims that never landed in a multi-claim group still deserve a pass:
    # compare them against the global pool on direction/polarity alone.
    singles = [c for c in parsed if len(groups[group_key(c)]) == 1]
    for i, a in enumerate(singles):
        for b in singles[i + 1:]:
            if a.paper_id and a.paper_id == b.paper_id:
                continue
            if not opposition_gate(a, b)[0]:
                continue
            conflict = score_pair(a, b)
            if conflict.score >= max(min_score, 0.45):
                conflicts.append(conflict)

    conflicts.sort(key=lambda c: -c.score)
    return {
        "engine": "nexus-claim-resolver-python",
        "claims": [c.to_json() for c in parsed],
        "conflicts": [c.to_json() for c in conflicts],
    }


def resolve_via_ruby(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Call the Ruby worker when configured; return None on any failure."""
    settings = get_settings()
    url = settings.embeddings.claim_service
    if not url:
        return None
    try:
        with httpx.Client(timeout=20.0) as client:
            resp = client.post(url.rstrip("/") + "/resolve", json={"claims": records})
            resp.raise_for_status()
            payload = resp.json()
        payload.setdefault("engine", "nexus-claim-resolver-ruby")
        return payload
    except Exception as exc:  # noqa: BLE001 - any failure must degrade gracefully
        log.info("ruby claim worker unavailable (%s) — using local resolver", exc)
        return None


def resolve_via_subprocess(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Offline path: run the Ruby script directly if a ruby interpreter exists."""
    script = REPO_ROOT / "polyglot" / "ruby" / "claim_resolver.rb"
    if not script.exists():
        return None
    ruby = "ruby"
    try:
        proc = subprocess.run(
            [ruby, str(script)],
            input=json.dumps({"claims": records}),
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(script.parent),
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None


def resolve_claims(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Best available engine: Ruby service -> Ruby subprocess -> Python."""
    for resolver in (resolve_via_ruby, resolve_via_subprocess):
        payload = resolver(records)
        if payload and payload.get("conflicts") is not None:
            return payload
    return resolve_locally(records)


def claims_from_abstract(abstract: str, paper_id: str, limit: int = 3) -> list[dict[str, Any]]:
    """Sentence-level claim extraction used for user-uploaded / live-ingested papers."""
    sentences = re.split(r"(?<=[.!?])\s+", abstract or "")
    out: list[dict[str, Any]] = []
    for sentence in sentences:
        s = sentence.strip()
        if not (45 <= len(s) <= 400):
            continue
        tokens = tokenize(s)
        if _strength(tokens) == "moderate" and not any(t in NEGATIONS for t in tokens) and not _direction(tokens):
            continue
        out.append(
            {
                "id": f"claim:{paper_id}:{len(out) + 1}",
                "paper": paper_id,
                "text": s,
                "stance": "reports",
                "source": "abstract-extraction",
            }
        )
        if len(out) >= limit:
            break
    return out
