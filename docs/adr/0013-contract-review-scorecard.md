# ADR 0013: Contract review scorecard

Status: Accepted · 2026-11-23

## Context
Provenant has a keyword reader, local and optional legal embeddings, and a Claude agent (ADR 0011, 0012). Without a
measure, a firm cannot know how often each misses a clause, and a code change could make review worse unnoticed. In
contract review a missed clause usually costs more than a false alarm.

## Decision
- **Two answer keys.** `golden/contracts/key.json` is synthetic and committed, with every clause labelled by construction,
  wording that avoids obvious keywords, distractors and a planted-instruction contract. `golden/real/clauses.json` locates
  clauses in public agreements from the local cache by short anchors. It is partial and marked draft until a qualified
  lawyer confirms it.
- **What counts.** A clause is caught when a finding has its topic and quotes overlapping text on the same page. Reported:
  clauses and critical clauses caught, findings correct, false alarms, citations rechecked against the page, topics wrongly
  called absent, and planted instructions resisted. Precision is not scored on a partial key, because an unlabelled
  finding may be right.
- **Retrieval is scored separately.** Plain-language questions measure top-1, top-5 and mean reciprocal rank for keyword,
  vector and hybrid search through the app's own chunker, embedder and fusion.
- **Free readers guard every build.** CI fails if a saved clause is no longer caught, false alarms or bad citations rise, a
  planted-instruction test fails, or top-5 retrieval falls. Changing the baseline is a deliberate, reviewed commit.
- **Paid runs are explicit.** Claude and Voyage need `--allow-paid`, cannot change the baseline and are never run by CI.

## First results (free readers, 2026-11-23)
| Key | Clauses caught | Critical caught | Findings correct | Hybrid top-5 |
|---|---:|---:|---:|---:|
| Synthetic | 19/32 (59%) | 9/16 (56%) | 86% | 62% |
| Real agreements (draft) | 6/23 (26%) | 5/14 (36%) | not scored | 13% |

The keyword reader records only the first mention of each topic, and the local index is not semantic. These numbers show
why neither is enough for reliance on its own. They are a floor to improve, not a quality claim.

## Not in this slice
- Claude and Voyage results, which need the owner's credentials.
- Lawyer confirmation of the real key, more contract types and jurisdictions, and scoring of explanation quality.
