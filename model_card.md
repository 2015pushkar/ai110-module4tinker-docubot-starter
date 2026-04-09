# DocuBot Model Card

---

## 1. System Overview

**What is DocuBot trying to do?**

DocuBot answers developer questions about a codebase by reading a set of project documentation files. It supports three modes that trade off grounding, readability, and LLM involvement — so you can compare how each approach handles the same question.

**What inputs does DocuBot take?**

- A natural language developer question (string)
- A `docs/` folder containing `.md` and `.txt` files (`AUTH.md`, `API_REFERENCE.md`, `DATABASE.md`, `SETUP.md`)
- An optional `GEMINI_API_KEY` environment variable to enable LLM-based modes

**What outputs does DocuBot produce?**

- Mode 1: A free-form LLM answer drawn from Gemini's general training (no doc grounding)
- Mode 2: Raw paragraph chunks from the docs that matched the query, labelled by filename
- Mode 3: A synthesized LLM answer constrained to the retrieved paragraphs, with source citations

---

## 2. Retrieval Design

**How does your retrieval system work?**

- **Indexing:** Each document is split into paragraphs (split on `\n\n`). Each paragraph is assigned a chunk index. An inverted index maps lowercase words (stripped of punctuation) to the list of chunk indices that contain them.
- **Scoring:** `score_document` counts how many query words appear anywhere in the chunk text (case-insensitive substring match). A chunk mentioning 3 of 5 query words scores 3.
- **Guardrail threshold:** Only chunks scoring at least `max(1, len(query_words) // 2)` are kept. This filters out weak matches where only one incidental word overlaps.
- **Deduplication:** After ranking, only the highest-scoring chunk per source file is kept, so the top-3 results always come from up to 3 different files.

**What tradeoffs did you make?**

| Decision | Tradeoff |
|----------|----------|
| Paragraph chunks instead of full documents | More precise matches, but answer can span two paragraphs in the same file and only one is returned |
| Substring match in scoring | Catches partial matches (`auth` inside `authentication`) but also matches unrelated occurrences |
| One chunk per file | Prevents flooding results with one document but can miss multi-section answers |
| `min_score = len(query_words) // 2` | Reduces false positives; may produce false negatives for short queries or unusual vocabulary |

---

## 3. Use of the LLM (Gemini)

**When does DocuBot call the LLM and when does it not?**

- **Naive LLM mode:** Calls Gemini with only the raw query. The `all_text` argument (full corpus) is passed in but intentionally ignored by the current prompt — Gemini answers purely from its training data with no access to the project docs.
- **Retrieval only mode:** No LLM call at all. Returns the matched paragraph chunks formatted with their source filenames.
- **RAG mode:** Calls `retrieve()` first. If chunks are found, passes them to Gemini with a strict prompt. If nothing is retrieved, returns "I do not know" without calling the LLM.

**What instructions do you give the LLM to keep it grounded?**

The RAG prompt in `llm_client.py:answer_from_snippets` tells the model to:
- Answer using **only** the information in the provided snippets
- Never invent functions, endpoints, or configuration values
- Reply with the exact phrase `"I do not know based on the docs I have."` if the snippets are insufficient
- Briefly mention which files the answer came from

---

## 4. Experiments and Comparisons

| Query | Naive LLM | Retrieval only | RAG | Notes |
|-------|-----------|----------------|-----|-------|
| Where is the auth token generated? | Harmful — names generic patterns, never mentions `generate_access_token` or `auth_utils.py` | Helpful — returns the exact Token Generation paragraph from `AUTH.md` | Helpful — synthesizes a clear sentence citing `AUTH.md` | Clearest example of Mode 1 hallucinating a different codebase |
| What environment variables are required for authentication? | Harmful — lists generic names like `API_KEY`, `SECRET`, not the actual variable names | Helpful — returns the Environment Variables paragraph with `AUTH_SECRET_KEY` and `TOKEN_LIFETIME_SECONDS` | Helpful — readable summary citing `AUTH.md` | Mode 1 wrong on exact names |
| How do I connect to the database? | Neutral — generic connection advice, correct for most apps but not specific to `DATABASE_URL` | Helpful — returns Connection Configuration paragraph from `DATABASE.md` | Helpful — precise answer about `DATABASE_URL` | |
| Which endpoint lists all users? | Harmful — may guess `/users` without the `/api` prefix, or cite a different method | Helpful — returns `GET /api/users` section from `API_REFERENCE.md` | Helpful — direct answer with correct path | |
| Is there any mention of payment processing in these docs? | Harmful — cannot know what is absent from the docs; may guess or add unsupported caveats | Helpful — "I do not know based on these docs." (guardrail fires) | Helpful — same refusal | Best example of Mode 1 failing on an out-of-scope question |
| How does a client refresh an access token? | Neutral — gives standard JWT refresh advice unrelated to `/api/refresh` | Helpful — returns Client Workflow paragraph from `AUTH.md` | Helpful — may surface both `AUTH.md` and `API_REFERENCE.md` paragraphs | |
| Which fields are stored in the users table? | Harmful — lists plausible generic fields (`id`, `name`, `created_at`) instead of actual schema | Helpful — returns exact table definition from `DATABASE.md` | Helpful — readable list with exact field names | Mode 1 sounds correct but the field names are wrong |
| What does the /api/projects/<project_id> route return? | Neutral — gives reasonable REST guess | Helpful — returns the specific endpoint paragraph from `API_REFERENCE.md` | Helpful — precise answer with JSON example | |

**What patterns did you notice?**

- **When naive LLM looks impressive but is untrustworthy:** On generic questions (`how does JWT work?`) Mode 1 gives polished, confident answers that happen to be reasonable for most apps — but for project-specific names and values it silently substitutes its own knowledge. A developer reading Mode 1 output has no way to know which parts are from the docs and which are invented.
- **When retrieval only is clearly better:** For factual lookups where the exact wording matters — field names, endpoint paths, environment variable names — Mode 2 returns the ground truth directly. The answer is always defensible because it is a direct quote.
- **When RAG is clearly better than both:** When the retrieved paragraph is dense or structured (a table, a numbered list), Mode 3 translates it into a readable sentence while keeping the grounding. It also refuses gracefully on out-of-scope questions, unlike Mode 1.

---

## 5. Failure Cases and Guardrails

**Failure case 1 — Vocabulary mismatch causes a false refusal**

- Question: `"How do I sign my tokens?"`
- What happened: The word `sign` appears in `AUTH.md` but the query also contains `my` and `tokens`. The chunk that mentions signing scores 1 (only one matching content word after stopwords) and is filtered by the `min_score = 2` threshold. DocuBot returns "I do not know" even though the answer is in the docs.
- What should happen: The system should return the Token Generation paragraph from `AUTH.md`.

**Failure case 2 — Wrong paragraph from the right file**

- Question: `"What happens if my token is missing?"`
- What happened: The Common Failure Cases paragraph in `AUTH.md` scores highest (mentions `token`, `missing`). The LLM receives a chunk about failure causes rather than the validation logic. The answer is coherent but describes a different aspect of authentication than the developer intended.
- What should happen: Retrieval should surface the Validating Requests paragraph which explains the `require_auth` decorator behavior.

**When should DocuBot say "I do not know based on the docs I have"?**

1. When no chunks pass the minimum score threshold — the query topic does not appear in any document (e.g., payment processing).
2. When retrieved chunks exist but do not contain enough evidence to answer confidently — the LLM should refuse rather than extrapolate.
3. When the query asks about behavior that is outside the scope of the loaded docs (e.g., deployment, scaling, billing).

**What guardrails did you implemented?**

- **Minimum score threshold** in `retrieve()`: chunks must match at least half the query words. Prevents returning weakly related paragraphs.
- **One chunk per file**: prevents one high-matching document from filling all top-k slots.
- **Empty-result short-circuit**: both `answer_retrieval_only` and `answer_rag` return an explicit refusal string when `retrieve()` returns nothing — no LLM call is made in the RAG case.
- **Prompt-level constraint** in `answer_from_snippets`: the LLM is instructed to answer only from snippets and to use the exact refusal phrase if uncertain.

---

## 6. Limitations and Future Improvements

**Current limitations**

1. **Bag-of-words scoring ignores meaning.** The scorer counts word matches, not semantic similarity. A query about "expiry" will not match chunks that use "expires_at" unless the word overlaps exactly.
2. **One chunk per file limits multi-section answers.** If a question spans two paragraphs in `AUTH.md` (e.g., token generation *and* the required env var), only the higher-scoring chunk is returned. The LLM receives an incomplete picture.
3. **Mode 1 ignores the provided corpus.** The `naive_answer_over_full_docs` function passes `all_text` but the current prompt does not include it. This is intentional for comparison purposes but means Mode 1 is purely a hallucination baseline, not a useful answer mode.
4. **No handling for multi-file answers.** Questions whose answers require combining information across files (e.g., "What do I need to set up authentication end-to-end?") cannot be fully answered because only three chunks (one per file) are retrieved.

**Future improvements**

1. **Semantic embeddings for retrieval.** Replace word-count scoring with cosine similarity over sentence embeddings. This would handle vocabulary mismatches and synonyms, fixing the false-refusal failure case.
2. **Overlapping or sliding-window chunks.** Instead of hard paragraph splits, use overlapping chunks so that answers that straddle two paragraphs are still captured.
3. **Include `all_text` in the Mode 1 prompt.** This would make Mode 1 a genuine "no-retrieval, full-context" baseline and surface a different class of tradeoffs (context window limits, hallucination with long context).

---

## 7. Responsible Use

**Where could this system cause real world harm if used carelessly?**

- **Mode 1 (Naive LLM)** can produce wrong-but-plausible answers about security-sensitive topics. A developer who trusts Mode 1's output for authentication configuration could set incorrect environment variables or misunderstand how token validation works — leading to auth bugs or security gaps.
- **Mode 3 (RAG)** can still hallucinate if the retrieved chunk is ambiguous and the LLM extrapolates. The prompt instructs refusal, but the model may not always comply.
- **All modes** will give stale answers if the docs are not kept up to date. DocuBot has no awareness of code changes that have not been reflected in the documentation.

**What instructions would you give real developers who want to use DocuBot safely?**

- Always verify security-critical answers (auth setup, token configuration, database credentials) directly against the source code, not just the docs.
- Treat Mode 1 output as a brainstorming aid, not a factual reference. It has no access to your actual project.
- For Mode 3, check which files are cited. If the source file does not match the topic of your question, the answer may be based on a poor retrieval match.
- Keep the `docs/` folder synchronized with the codebase. DocuBot is only as accurate as its documentation.

---
