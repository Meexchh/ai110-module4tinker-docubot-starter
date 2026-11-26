"""
Core DocuBot class responsible for:
- Loading documents from the docs/ folder
- Building a simple retrieval index (Phase 1)
- Retrieving relevant snippets (Phase 1)
- Supporting retrieval only answers
- Supporting RAG answers when paired with Gemini (Phase 2)
"""

import os
import re
import glob

STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "to", "of", "in", "on",
    "for", "and", "or", "it", "its", "this", "that", "how", "do", "does", "i",
    "where", "which", "what", "who", "when", "my", "me", "we", "you", "your",
    "can", "with", "as", "at", "by", "from", "there", "about", "any", "these",
    "there", "mention", "all",
}

# Guardrail: the best snippet must match at least this many distinct query
# words (or every query word, if the query has fewer meaningful words).
MIN_SCORE = 2


def tokenize(text):
    """Lowercase, split into alphanumeric words, drop stopwords.

    Words are reduced to their first 4 letters (crude stemming) so that
    "token"/"tokens" and "list"/"lists" match each other.
    """
    words = re.findall(r"[a-z0-9]+", text.lower())
    return [w[:4] for w in words if w not in STOPWORDS and len(w) > 1]


class DocuBot:
    def __init__(self, docs_folder="docs", llm_client=None):
        """
        docs_folder: directory containing project documentation files
        llm_client: optional Gemini client for LLM based answers
        """
        self.docs_folder = docs_folder
        self.llm_client = llm_client

        # Load documents into memory
        self.documents = self.load_documents()  # List of (filename, text)

        # Build a retrieval index (implemented in Phase 1)
        self.chunks = []  # List of (filename, paragraph text), set by build_index
        self.index = self.build_index(self.documents)

    # -----------------------------------------------------------
    # Document Loading
    # -----------------------------------------------------------

    def load_documents(self):
        """
        Loads all .md and .txt files inside docs_folder.
        Returns a list of tuples: (filename, text)
        """
        docs = []
        pattern = os.path.join(self.docs_folder, "*.*")
        for path in glob.glob(pattern):
            if path.endswith(".md") or path.endswith(".txt"):
                with open(path, "r", encoding="utf8") as f:
                    text = f.read()
                filename = os.path.basename(path)
                docs.append((filename, text))
        return docs

    # -----------------------------------------------------------
    # Index Construction (Phase 1)
    # -----------------------------------------------------------

    def build_chunks(self, documents):
        """Split each document into paragraph-sized chunks (blank-line separated).

        A lone heading line is kept attached to the paragraph below it so a
        snippet still shows what section it came from.
        """
        chunks = []
        for filename, text in documents:
            pending_heading = ""
            for para in re.split(r"\n\s*\n", text):
                para = para.strip()
                if not para:
                    continue
                if para.startswith("#") and "\n" not in para:
                    pending_heading = para + "\n"
                    continue
                # Tables, bullet lists and code blocks belong to the paragraph above.
                if chunks and chunks[-1][0] == filename and re.match(
                    r"(\||- |\* |\d+\. |```)", para
                ):
                    prev_name, prev_text = chunks[-1]
                    chunks[-1] = (prev_name, prev_text + "\n\n" + para)
                    continue
                chunks.append((filename, pending_heading + para))
                pending_heading = ""
        return chunks

    def build_index(self, documents):
        """
        Build an inverted index mapping each word to the set of chunk ids
        (positions in self.chunks) that contain it.
        """
        self.chunks = self.build_chunks(documents)
        index = {}
        for chunk_id, (_filename, text) in enumerate(self.chunks):
            for word in set(tokenize(text)):
                index.setdefault(word, set()).add(chunk_id)
        return index

    # -----------------------------------------------------------
    # Scoring and Retrieval (Phase 1)
    # -----------------------------------------------------------

    def score_document(self, query, text):
        """
        Relevance score = number of distinct meaningful query words found in the
        text, plus a tie-breaking bonus (below 1) for how dense the query words
        are in the snippet, so short focused snippets beat long ones.
        """
        text_words = tokenize(text)
        distinct = sum(1 for word in set(tokenize(query)) if word in set(text_words))
        occurrences = sum(text_words.count(word) for word in set(tokenize(query)))
        density = occurrences / len(text_words) if text_words else 0
        return distinct + 0.9 * density

    def retrieve(self, query, top_k=3):
        """
        Return up to top_k (filename, snippet) tuples, best first.

        Guardrail: returns [] when the best snippet matches too few query
        words, so callers answer "I do not know" instead of guessing.
        """
        query_words = set(tokenize(query))
        if not query_words:
            return []

        # Only score chunks that share at least one word with the query.
        candidate_ids = set()
        for word in query_words:
            candidate_ids |= self.index.get(word, set())

        scored = []
        for chunk_id in candidate_ids:
            filename, text = self.chunks[chunk_id]
            scored.append((self.score_document(query, text), filename, text))
        scored.sort(key=lambda item: item[0], reverse=True)

        # The integer part of a score is the number of distinct matching words.
        needed = min(MIN_SCORE, len(query_words))
        if not scored or int(scored[0][0]) < needed:
            return []

        return [(fn, text) for score, fn, text in scored if int(score) >= needed][:top_k]

    # -----------------------------------------------------------
    # Answering Modes
    # -----------------------------------------------------------

    def answer_retrieval_only(self, query, top_k=3):
        """
        Phase 1 retrieval only mode.
        Returns raw snippets and filenames with no LLM involved.
        """
        snippets = self.retrieve(query, top_k=top_k)

        if not snippets:
            return "I do not know based on these docs."

        formatted = []
        for filename, text in snippets:
            formatted.append(f"[{filename}]\n{text}\n")

        return "\n---\n".join(formatted)

    def answer_rag(self, query, top_k=3):
        """
        Phase 2 RAG mode.
        Uses student retrieval to select snippets, then asks Gemini
        to generate an answer using only those snippets.
        """
        if self.llm_client is None:
            raise RuntimeError(
                "RAG mode requires an LLM client. Provide a GeminiClient instance."
            )

        snippets = self.retrieve(query, top_k=top_k)

        if not snippets:
            return "I do not know based on these docs."

        return self.llm_client.answer_from_snippets(query, snippets)

    # -----------------------------------------------------------
    # Bonus Helper: concatenated docs for naive generation mode
    # -----------------------------------------------------------

    def full_corpus_text(self):
        """
        Returns all documents concatenated into a single string.
        This is used in Phase 0 for naive 'generation only' baselines.
        """
        return "\n\n".join(text for _, text in self.documents)