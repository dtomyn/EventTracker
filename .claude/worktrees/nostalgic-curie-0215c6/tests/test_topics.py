from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from app.db import init_db, connection_context
from app.models import Entry
from app.schemas import EntryPayload
from app.services.entries import save_entry, sync_entry_tags
from app.services.topics import (
    build_topic_graph,
    build_tag_graph,
    compute_topic_clusters,
    TopicGraphNode,
)
import uuid

class TestTopicClustering(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_db_path = os.environ.get("EVENTTRACKER_DB_PATH")
        os.environ["EVENTTRACKER_DB_PATH"] = str(
            Path(self.temp_dir.name) / "EventTracker-test.db"
        )
        init_db()

    def tearDown(self) -> None:
        if self.previous_db_path is None:
            os.environ.pop("EVENTTRACKER_DB_PATH", None)
        else:
            os.environ["EVENTTRACKER_DB_PATH"] = self.previous_db_path
        self.temp_dir.cleanup()

    def _add_entry(self, title: str, text: str, group_id: int = 1) -> None:
        with connection_context() as connection:
            save_entry(
                connection,
                EntryPayload(
                    event_year=2026,
                    event_month=3,
                    event_day=24,
                    group_id=group_id,
                    title=title,
                    source_url=f"https://example.com/topic/{uuid.uuid4()}",
                    generated_text="",
                    final_text=text,
                    tags=[],
                    links=[]
                ),
            )

    def test_build_topic_graph_with_0_entries(self) -> None:
        with connection_context() as connection:
            graph = build_topic_graph(connection, 1)
        self.assertEqual(len(graph.nodes), 0)
        self.assertEqual(len(graph.edges), 0)

    def test_build_topic_graph_with_1_entry(self) -> None:
        self._add_entry("Test entry", "Some body text", 1)
        with connection_context() as connection:
            graph = build_topic_graph(connection, 1)
        self.assertTrue(len(graph.nodes) in (0, 1))

    @patch("app.services.topics.is_sqlite_vec_enabled", return_value=False)
    def test_build_topic_graph_embeddings_disabled(self, mock_is_enabled) -> None:
        self._add_entry("Test entry 1", "Body", 1)
        self._add_entry("Test entry 2", "Another body", 1)
        with connection_context() as connection:
            graph = build_topic_graph(connection, 1)
        self.assertEqual(len(graph.nodes), 0)

    def test_build_tag_graph_no_tags(self) -> None:
        self._add_entry("Entry without tags", "No tags here", 1)
        with connection_context() as connection:
            graph = build_tag_graph(connection, 1)
        self.assertEqual(len(graph.nodes), 0)
        self.assertEqual(len(graph.edges), 0)

    def test_build_tag_graph_with_tags(self) -> None:
        with connection_context() as connection:
            entry_id = save_entry(
                connection,
                EntryPayload(
                    event_year=2026,
                    event_month=3,
                    event_day=1,
                    group_id=1,
                    title="Tagged entry",
                    source_url=f"https://example.com/{uuid.uuid4()}",
                    generated_text="",
                    final_text="Content",
                    tags=["AI Safety", "Open Source"],
                    links=[],
                ),
            )
        with connection_context() as connection:
            graph = build_tag_graph(connection, 1)
        tag_labels = {n.label for n in graph.nodes}
        self.assertIn("AI Safety", tag_labels)
        self.assertIn("Open Source", tag_labels)

    def test_build_tag_graph_edge_for_cooccurrence(self) -> None:
        """Two entries sharing a tag should produce an entry count, and entries
        sharing two tags produce a co-occurrence edge between those tags."""
        with connection_context() as connection:
            save_entry(
                connection,
                EntryPayload(
                    event_year=2026, event_month=3, event_day=1, group_id=1,
                    title="Entry A", source_url=f"https://example.com/{uuid.uuid4()}",
                    generated_text="", final_text="A",
                    tags=["Alpha", "Beta"], links=[],
                ),
            )
            save_entry(
                connection,
                EntryPayload(
                    event_year=2026, event_month=3, event_day=2, group_id=1,
                    title="Entry B", source_url=f"https://example.com/{uuid.uuid4()}",
                    generated_text="", final_text="B",
                    tags=["Alpha", "Gamma"], links=[],
                ),
            )
        with connection_context() as connection:
            graph = build_tag_graph(connection, 1)
        tag_node = next((n for n in graph.nodes if n.label == "Alpha"), None)
        self.assertIsNotNone(tag_node)
        self.assertEqual(tag_node.size, 2)  # type: ignore[union-attr]
        # Alpha co-occurs with Beta (entry A) and Gamma (entry B) → 2 edges
        edge_pairs = {(e.source, e.target) for e in graph.edges}
        self.assertTrue(
            ("Alpha", "Beta") in edge_pairs or ("Beta", "Alpha") in edge_pairs
        )

    @patch("app.services.topics.load_entry_tag_generator")
    async def test_compute_topic_clusters_generates_tags(self, mock_load) -> None:
        self._add_entry("AI Research Paper", "Details about AI safety research", 1)

        mock_gen = AsyncMock()
        mock_gen.generate_tags.return_value = ["AI Safety", "Research"]
        mock_load.return_value = mock_gen

        with connection_context() as connection:
            graph = await compute_topic_clusters(connection, 1)

        # The generator should have been called once for the one entry
        mock_gen.generate_tags.assert_called_once()
        # The graph should have tag nodes from the generated tags
        tag_labels = {n.label for n in graph.nodes}
        self.assertTrue(len(tag_labels) > 0)

    @patch("app.services.topics.load_entry_tag_generator")
    async def test_compute_topic_clusters_generator_unavailable(self, mock_load) -> None:
        """When the tag generator cannot be loaded, fall back to existing tags."""
        with connection_context() as connection:
            save_entry(
                connection,
                EntryPayload(
                    event_year=2026, event_month=1, event_day=1, group_id=1,
                    title="An entry", source_url=f"https://example.com/{uuid.uuid4()}",
                    generated_text="", final_text="Text",
                    tags=["Existing Tag"], links=[],
                ),
            )
        mock_load.side_effect = ValueError("Provider not configured")

        with connection_context() as connection:
            graph = await compute_topic_clusters(connection, 1)

        # Should return existing tag-based graph
        tag_labels = {n.label for n in graph.nodes}
        self.assertIn("Existing Tag", tag_labels)

    @patch("app.services.topics.load_entry_tag_generator")
    async def test_compute_topic_clusters_ai_failure_skips_entry(self, mock_load) -> None:
        """When tag generation raises for an entry, that entry is skipped gracefully."""
        self._add_entry("Entry 1", "Body 1", 1)

        mock_gen = AsyncMock()
        mock_gen.generate_tags.side_effect = Exception("API rate limit")
        mock_load.return_value = mock_gen

        with connection_context() as connection:
            # Should not raise
            graph = await compute_topic_clusters(connection, 1)
