# DocuBot Model Card

> Note: I was unable to obtain a working Gemini API key, so Modes 1 and 3 (the LLM modes) were not run. Only retrieval-only results were tested directly, and everything below about Modes 1 and 3 is based on reading the code, not on observed outputs.

---

## 1. System Overview

**What is DocuBot trying to do?**
DocuBot answers developer questions about a project's local documentation. It compares three approaches: sending the LLM everything, retrieving snippets with my own code, and retrieving first and then asking the LLM to answer from those snippets only. The goal is answers that are grounded in the docs and that refuse when the docs don't say.

**What inputs does DocuBot take?**
A user question, the `.md` and `.txt` files in `docs/`, and the `GEMINI_API_KEY` environment variable (loaded from `.env`) for the LLM modes.

**What outputs does DocuBot produce?**
Mode 1: a free-form LLM answer. Mode 2: raw snippets labeled with their filenames. Mode 3: a short LLM answer that cites the files it used. Modes 2 and 3 return "I do not know based on these docs." when evidence is weak.

---

## 2. Retrieval Design

**How does your retrieval system work?**

- **Index:** each doc is split into paragraph chunks (blank-line separated). A lone heading is attached to the paragraph below it, and tables, bullet lists and code blocks are attached to the paragraph above them. `build_index` creates an inverted index from each word to the set of chunks containing it. Words are lowercased, stopwords are dropped, and words are compared by their first 4 letters (crude stemming, so "token"/"tokens" and "list"/"lists" match).
- **Scoring:** `score_document` counts the distinct query words found in a chunk, plus a small density bonus (below 1) so short, focused chunks win ties.
- **Top snippets:** `retrieve` scores only chunks that share a word with the query, sorts by score, and returns the top 3 that pass the guardrail.

**What tradeoffs did you make?**
I chose simplicity and readability over accuracy: word overlap is easy to explain line by line but is lexical, not semantic. Paragraph chunks are much more precise than whole files but can split an answer across chunks. First-4-letters stemming is crude and can merge unrelated words (for example "data" and "database").

---

## 3. Use of the LLM (Gemini)

**When does DocuBot call the LLM and when does it not?**

- Naive LLM mode: always calls Gemini. In the starter code, `naive_answer_over_full_docs` ignores the docs text and sends only the question, so the model answers from general knowledge.
- Retrieval only mode: never calls the LLM.
- RAG mode: calls Gemini only if retrieval returned snippets. If the guardrail returns nothing, DocuBot refuses without calling the model.

**What instructions do you give the LLM to keep it grounded?**
The RAG prompt tells the model to use only the provided snippets, not to invent functions, endpoints or config values, to reply exactly "I do not know based on the docs I have." when the snippets are not enough, and to mention which files it relied on.

---

## 4. Experiments and Comparisons

Retrieval-only results I verified (top snippet per query). Naive LLM and RAG columns are "Not run" because I had no working API key.

| Query | Naive LLM | Retrieval only | RAG | Notes |
|------|-----------|----------------|-----|-------|
| Where is the auth token generated? | Not run | Helpful: returns the Token Generation paragraph naming `generate_access_token` in `auth_utils.py` | Not run | |
| How do I connect to the database? | Not run | Helpful: returns the Connection Configuration paragraph (`DATABASE_URL`) | Not run | Overview paragraphs also returned, adding noise |
| Which endpoint lists all users? | Not run | Helpful: `GET /api/users` ranked first | Not run | Lower results are weakly related |
| How does a client refresh an access token? | Not run | Partly helpful: Client Workflow ranked first; the `/api/refresh` section is not in the top 3 | Not run | Shows a ranking weakness |
| Is there any mention of payment processing? | Not run | Correctly refuses | Not run | Naive mode is the one to watch for a confident guess |

**What patterns did you notice?**

- When does naive LLM look impressive but untrustworthy? Not observed. From the code, `naive_answer_over_full_docs` sends only the question and ignores the docs text, so I would expect fluent answers drawn from general knowledge rather than from these docs.
- When is retrieval only clearly better? It never invents anything and always shows its source file, so it is trustworthy for questions the docs answer and refuses cleanly for ones they don't. The cost is that it returns raw paragraphs the reader must interpret.
- When is RAG clearly better than both? Not observed. I would expect it to help when the answer is spread over several snippets, since the model can summarize them, but I did not test this.

---

## 5. Failure Cases and Guardrails

**Describe at least two concrete failure cases you observed.**

> **Failure case 1:** Question: "How does a client refresh an access token?" Retrieval returned the Client Workflow paragraph and two JSON response blocks, but not the `POST /api/refresh` section. The answer is partially present but incomplete. It should have ranked the refresh endpoint section first. The cause is that the section matches few query words ("client" and "access" are not in it).

> **Failure case 2:** Question: "Which endpoint lists all users?" The correct paragraph ranks first, but unrelated chunks (for example one about a user "listed as admin") also pass the 2-word threshold and are returned. Extra snippets can confuse a reader, and in RAG mode they give the LLM irrelevant context.

**When should DocuBot say "I do not know based on the docs I have"?**

- The question is about something the docs never cover (payment processing, deploying to AWS).
- The question has no meaningful words after stopword removal, or the best snippet matches fewer than 2 of its meaningful words.

**What guardrails did you implement?**
`retrieve` returns an empty list when the best chunk matches fewer than 2 distinct query words (or all of them, for very short queries). Both `answer_retrieval_only` and `answer_rag` turn an empty result into a refusal, so Gemini is never called without evidence. The RAG prompt adds a second layer: an explicit refusal instruction. Retrieval is also capped at the top 3 snippets.

---

## 6. Limitations and Future Improvements

**Current limitations**

1. Matching is lexical, so synonyms ("login" vs "authenticate") are missed.
2. The 2-word refusal threshold is a heuristic that can refuse valid short questions or accept an unrelated snippet that shares two common words.
3. An answer split across several chunks may only be partly retrieved.
4. Only `.md` and `.txt` files are read, and docs that are outdated or wrong are returned as if they were true.

**Future improvements**

1. Weight rare words more heavily (TF-IDF) so that distinctive terms like "refresh" matter more than common ones.
2. Use embeddings for semantic matching instead of word overlap.
3. Add a small evaluation set with expected files so retrieval changes can be measured instead of eyeballed.

---

## 7. Responsible Use

**Where could this system cause real world harm if used carelessly?**
A confident but wrong answer (for example a wrong endpoint, auth step or config value) could send a developer down the wrong path or lead to insecure setup. Missing or outdated docs produce wrong answers that look authoritative, and over-trusting the LLM's fluent wording hides that the snippet may be the wrong one.

**What instructions would you give real developers who want to use DocuBot safely?**

- Check the cited file before acting on any answer, especially for security or production changes.
- Treat "I do not know" as a signal to read the docs or ask a teammate, not as a bug to work around.
- Keep the docs current; DocuBot can only be as accurate as what it indexes.