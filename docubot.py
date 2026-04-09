"""
Core DocuBot class responsible for:
- Loading documents from the docs/ folder
- Building a simple retrieval index (Phase 1)
- Retrieving relevant snippets (Phase 1)
- Supporting retrieval only answers
- Supporting RAG answers when paired with Gemini (Phase 2)
"""

import os
import glob

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

        # Split documents into paragraph-level chunks
        self.chunks = self.chunk_documents(self.documents)  # List of (filename, chunk_text)

        # Build a retrieval index over chunks
        self.index = self.build_index(self.chunks)

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
    # Chunking
    # -----------------------------------------------------------

    def chunk_documents(self, documents):
        """
        Splits each document into paragraph-level chunks (split on blank lines).
        Returns a list of (filename, chunk_text) tuples.
        """
        chunks = []
        for filename, text in documents:
            for para in text.split("\n\n"):
                para = para.strip()
                if para:
                    chunks.append((filename, para))
        return chunks

    # -----------------------------------------------------------
    # Index Construction (Phase 1)
    # -----------------------------------------------------------

    def build_index(self, chunks):
        """
        TODO (Phase 1):
        Build a tiny inverted index mapping lowercase words to the documents
        they appear in.

        Example structure:
        {
            "token": ["AUTH.md", "API_REFERENCE.md"],
            "database": ["DATABASE.md"]
        }

        Keep this simple: split on whitespace, lowercase tokens,
        ignore punctuation if needed.
        """
        index = {}
        for idx, (_, text) in enumerate(chunks):
            for word in text.lower().split():
                word = word.strip(".,!?;:\"'()[]{}")
                if word not in index:
                    index[word] = []
                if idx not in index[word]:
                    index[word].append(idx)
        return index

    # -----------------------------------------------------------
    # Scoring and Retrieval (Phase 1)
    # -----------------------------------------------------------

    def score_document(self, query, text):
        """
        TODO (Phase 1):
        Return a simple relevance score for how well the text matches the query.

        Suggested baseline:
        - Convert query into lowercase words
        - Count how many appear in the text
        - Return the count as the score
        """
        text_lower = text.lower()
        query_words = query.lower().split()
        return sum(1 for word in query_words if word in text_lower)

    def retrieve(self, query, top_k=3):
        """
        TODO (Phase 1):
        Use the index and scoring function to select top_k relevant document snippets.

        Return a list of (filename, text) sorted by score descending.
        """
        # Find candidate chunk indices via the index
        candidates = set()
        for word in query.lower().split():
            word = word.strip(".,!?;:\"'()[]{}")
            for idx in self.index.get(word, []):
                candidates.add(idx)

        # Score each candidate chunk; require at least half the query words to match
        query_words = query.lower().split()
        min_score = max(1, len(query_words) // 2)
        scored = [
            (self.score_document(query, self.chunks[idx][1]), idx)
            for idx in candidates
        ]
        scored = [(s, idx) for s, idx in scored if s >= min_score]
        scored.sort(key=lambda x: x[0], reverse=True)

        # Keep only the best-scoring chunk per file
        seen = set()
        results = []
        for _, idx in scored:
            filename, text = self.chunks[idx]
            if filename not in seen:
                seen.add(filename)
                results.append((filename, text))
            if len(results) == top_k:
                break
        return results

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
            formatted.append(f"[{filename}]\n{text}")
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
