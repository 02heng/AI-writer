"""Vector-based memory retrieval for semantic search.

This module provides vector storage and retrieval capabilities for memory entries,
enabling semantic search instead of simple time-based ordering.

Uses ChromaDB for local vector storage (no external server required).

Usage:
    from app.vector_memory import VectorMemoryStore

    store = VectorMemoryStore(book_root)
    store.add_entry("Alice found a glowing orb", {"chapter": 1, "type": "event"})
    results = store.retrieve_relevant("mysterious glowing object", top_k=5)
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Optional

from .core.logging import get_logger

logger = get_logger(__name__)

# Check if chromadb is available
try:
    import chromadb
    from chromadb.config import Settings
    HAS_CHROMADB = True
except ImportError:
    HAS_CHROMADB = False
    chromadb = None
    Settings = None


class VectorMemoryStore:
    """Vector-based memory storage using ChromaDB.

    This class provides semantic search capabilities for memory entries,
    allowing retrieval based on meaning rather than exact keywords.
    """

    def __init__(
        self,
        book_root: Path,
        *,
        collection_name: str = "book_memory",
    ) -> None:
        """Initialize the vector store.

        Args:
            book_root: Path to the book's root directory
            collection_name: Name of the ChromaDB collection
        """
        self.book_root = book_root
        self.collection_name = collection_name
        self._client = None
        self._collection = None

        if not HAS_CHROMADB:
            logger.warning(
                "chromadb is not installed. Vector memory search will fall back "
                "to keyword matching. Install with: pip install chromadb"
            )

    def _get_client(self):
        """Get or create the ChromaDB client."""
        if self._client is not None:
            return self._client

        if not HAS_CHROMADB:
            return None

        # Use persistent storage in the book's directory
        db_path = self.book_root / ".vector_db"
        db_path.mkdir(parents=True, exist_ok=True)

        self._client = chromadb.PersistentClient(
            path=str(db_path),
            settings=Settings(
                anonymized_telemetry=False,
                allow_reset=True,
            ),
        )
        return self._client

    def _get_collection(self):
        """Get or create the collection."""
        if self._collection is not None:
            return self._collection

        client = self._get_client()
        if client is None:
            return None

        self._collection = client.get_or_create_collection(
            name=self.collection_name,
            metadata={"hnsw:space": "cosine"},
        )
        return self._collection

    def _generate_id(self, text: str, metadata: dict[str, Any]) -> str:
        """Generate a unique ID for an entry."""
        content = f"{text}:{json.dumps(metadata, sort_keys=True)}"
        return hashlib.md5(content.encode()).hexdigest()[:16]

    def add_entry(
        self,
        text: str,
        metadata: Optional[dict[str, Any]] = None,
        entry_id: Optional[str] = None,
    ) -> str:
        """Add a memory entry to the vector store.

        Args:
            text: The text content to store
            metadata: Optional metadata (chapter, type, etc.)
            entry_id: Optional custom ID (will be generated if not provided)

        Returns:
            The entry ID
        """
        metadata = metadata or {}
        collection = self._get_collection()

        if collection is None:
            # Fallback: just return a generated ID
            return entry_id or self._generate_id(text, metadata)

        if entry_id is None:
            entry_id = self._generate_id(text, metadata)

        try:
            collection.add(
                documents=[text],
                metadatas=[metadata],
                ids=[entry_id],
            )
            logger.debug(
                f"Added vector entry",
                extra={"entry_id": entry_id, "text_length": len(text)},
            )
            return entry_id
        except Exception as e:
            logger.error(f"Failed to add vector entry: {e}")
            return entry_id

    def add_entries_batch(
        self,
        texts: list[str],
        metadatas: Optional[list[dict[str, Any]]] = None,
        ids: Optional[list[str]] = None,
    ) -> list[str]:
        """Add multiple entries at once.

        Args:
            texts: List of text contents
            metadatas: Optional list of metadata dicts
            ids: Optional list of custom IDs

        Returns:
            List of entry IDs
        """
        if not texts:
            return []

        collection = self._get_collection()
        if collection is None:
            return ids or [self._generate_id(t, {}) for t in texts]

        metadatas = metadatas or [{} for _ in texts]
        if ids is None:
            ids = [self._generate_id(t, m) for t, m in zip(texts, metadatas)]

        try:
            collection.add(
                documents=texts,
                metadatas=metadatas,
                ids=ids,
            )
            logger.info(f"Added {len(ids)} vector entries in batch")
            return ids
        except Exception as e:
            logger.error(f"Failed to add batch entries: {e}")
            return ids

    def retrieve_relevant(
        self,
        query: str,
        *,
        top_k: int = 5,
        where: Optional[dict[str, Any]] = None,
    ) -> list[dict[str, Any]]:
        """Retrieve entries relevant to the query.

        Args:
            query: The search query
            top_k: Maximum number of results
            where: Optional metadata filter (e.g., {"chapter": "5"})

        Returns:
            List of dictionaries with 'text', 'metadata', 'distance'
        """
        collection = self._get_collection()

        if collection is None:
            # Fallback: keyword matching
            return self._keyword_fallback(query, top_k)

        try:
            results = collection.query(
                query_texts=[query],
                n_results=top_k,
                where=where,
                include=["documents", "metadatas", "distances"],
            )

            entries = []
            if results and results.get("documents"):
                docs = results["documents"][0]
                metas = results.get("metadatas", [[]])[0]
                dists = results.get("distances", [[]])[0]

                for i, doc in enumerate(docs):
                    entries.append({
                        "text": doc,
                        "metadata": metas[i] if i < len(metas) else {},
                        "distance": dists[i] if i < len(dists) else 0.0,
                    })

            logger.debug(
                f"Retrieved {len(entries)} relevant entries",
                extra={"query_length": len(query)},
            )
            return entries

        except Exception as e:
            logger.error(f"Vector search failed: {e}")
            return self._keyword_fallback(query, top_k)

    def _keyword_fallback(
        self,
        query: str,
        top_k: int,
    ) -> list[dict[str, Any]]:
        """Fallback keyword matching when ChromaDB is unavailable.

        This reads from the SQLite memory store and does simple text matching.
        """
        from .memory_store import list_entries

        entries = list_entries(self.book_root, limit=100)
        results = []

        query_lower = query.lower()
        query_words = set(query_lower.split())

        for entry in entries:
            body = entry.get("body", "").lower()
            title = entry.get("title", "").lower()

            # Simple scoring based on word overlap
            body_words = set(body.split())
            title_words = set(title.split())

            overlap = len(query_words & body_words) + 2 * len(query_words & title_words)

            if overlap > 0:
                results.append({
                    "text": entry.get("body", ""),
                    "metadata": {
                        "title": entry.get("title"),
                        "room": entry.get("room"),
                        "chapter_label": entry.get("chapter_label"),
                    },
                    "distance": 1.0 / (overlap + 1),  # Higher overlap = lower distance
                })

        # Sort by distance (ascending) and return top_k
        results.sort(key=lambda x: x["distance"])
        return results[:top_k]

    def delete_entry(self, entry_id: str) -> bool:
        """Delete an entry by ID.

        Args:
            entry_id: The entry ID to delete

        Returns:
            True if deleted, False otherwise
        """
        collection = self._get_collection()
        if collection is None:
            return False

        try:
            collection.delete(ids=[entry_id])
            logger.debug(f"Deleted vector entry: {entry_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete vector entry: {e}")
            return False

    def clear_all(self) -> bool:
        """Clear all entries from the collection.

        Returns:
            True if successful, False otherwise
        """
        collection = self._get_collection()
        if collection is None:
            return False

        try:
            # Get all IDs and delete them
            all_ids = collection.get()["ids"]
            if all_ids:
                collection.delete(ids=all_ids)
            logger.info("Cleared all vector entries")
            return True
        except Exception as e:
            logger.error(f"Failed to clear collection: {e}")
            return False

    def count(self) -> int:
        """Get the number of entries in the collection.

        Returns:
            Number of entries
        """
        collection = self._get_collection()
        if collection is None:
            return 0

        try:
            return collection.count()
        except Exception:
            return 0


# =============================================================================
# Convenience Functions
# =============================================================================

def build_semantic_context(
    book_root: Path,
    query: str,
    *,
    top_k: int = 8,
    max_chars: int = 2000,
    filters: Optional[dict[str, Any]] = None,
) -> str:
    """Build context string from semantically relevant memories.

    Args:
        book_root: Path to the book's root directory
        query: The search query (usually scene description)
        top_k: Maximum number of results
        max_chars: Maximum total characters
        filters: Optional metadata filters

    Returns:
        Formatted context string for LLM injection
    """
    store = VectorMemoryStore(book_root)
    entries = store.retrieve_relevant(query, top_k=top_k, where=filters)

    if not entries:
        return ""

    parts: list[str] = ["【相关记忆（语义检索）】"]
    used = 0

    for entry in entries:
        text = entry["text"]
        meta = entry.get("metadata", {})

        block = f"\n- [{meta.get('room', '情节')}] {meta.get('title', '')}\n  {text}"

        if used + len(block) > max_chars:
            break

        parts.append(block)
        used += len(block)

    return "\n".join(parts)


def sync_memory_to_vector(book_root: Path, limit: int = 100) -> int:
    """Sync existing memory entries to vector store.

    Args:
        book_root: Path to the book's root directory
        limit: Maximum entries to sync

    Returns:
        Number of entries synced
    """
    from .memory_store import list_entries

    entries = list_entries(book_root, limit=limit)
    if not entries:
        return 0

    store = VectorMemoryStore(book_root)

    texts = []
    metadatas = []

    for entry in entries:
        texts.append(entry.get("body", ""))

        metadatas.append({
            "title": entry.get("title", ""),
            "room": entry.get("room", ""),
            "chapter_label": entry.get("chapter_label", ""),
            "created_at": entry.get("created_at"),
        })

    ids = store.add_entries_batch(texts, metadatas)
    return len(ids)
