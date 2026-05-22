from __future__ import annotations

import asyncio
import collections
from collections import deque
import datetime
import json
import logging
import re
import sqlite3
from dataclasses import asdict, dataclass, field
from contextlib import AsyncExitStack
from typing import Mapping, Protocol

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam
from openai.types.chat import ChatCompletionSystemMessageParam
from openai.types.chat import ChatCompletionUserMessageParam

from app.db import is_sqlite_vec_enabled
try:
    import sqlite_vec
except ImportError:
    sqlite_vec = None

from app.models import Entry
from app.services.entries import list_timeline_entries, merge_entry_tags
from app.services import copilot_runtime
from app.services.ai_generate import (
    DEFAULT_AI_PROVIDER,
    SUPPORTED_AI_PROVIDERS,
    OpenAISettings,
    CopilotSettings,
    load_openai_settings,
    load_copilot_settings,
    load_ai_provider,
)

logger = logging.getLogger(__name__)

CLUSTER_DISTANCE_THRESHOLD = 0.30


@dataclass(slots=True)
class TopicGraphNode:
    id: str
    label: str
    entry_ids: list[int]
    size: int


@dataclass(slots=True)
class TopicGraphEdge:
    source: str
    target: str
    weight: float


@dataclass(slots=True)
class TopicGraph:
    nodes: list[TopicGraphNode] = field(default_factory=list)
    edges: list[TopicGraphEdge] = field(default_factory=list)


def build_topic_graph(connection: sqlite3.Connection, group_id: int) -> TopicGraph:
    if not is_sqlite_vec_enabled(connection) or sqlite_vec is None:
        return TopicGraph()

    try:
        rows = connection.execute(
            """
            SELECT
                a.rowid as id1,
                b.rowid as id2,
                vec_distance_cosine(a.embedding, b.embedding) AS distance
            FROM entry_embeddings a
            JOIN entry_embeddings b ON a.rowid < b.rowid
            JOIN entries ea ON a.rowid = ea.id
            JOIN entries eb ON b.rowid = eb.id
            WHERE ea.group_id = ? AND eb.group_id = ?
            """,
            (group_id, group_id),
        ).fetchall()
    except sqlite3.OperationalError:
        # Table might not exist if embeddings haven't been indexed
        return TopicGraph()

    pair_distances: dict[tuple[int, int], float] = {}
    for r in rows:
        pair_distances[(r["id1"], r["id2"])] = float(r["distance"])
        pair_distances[(r["id2"], r["id1"])] = float(r["distance"])

    entries = list_timeline_entries(connection, group_id=group_id)
    entry_dict = {e.id: e for e in entries}

    if not entry_dict:
        return TopicGraph()

    adj: dict[int, list[int]] = {e_id: [] for e_id in entry_dict}
    for (id1, id2), dist in pair_distances.items():
        if id1 < id2 and dist <= CLUSTER_DISTANCE_THRESHOLD:
            adj[id1].append(id2)
            adj[id2].append(id1)

    visited = set()
    clusters: list[list[int]] = []

    for node in adj:
        if node not in visited:
            comp = []
            queue = deque([node])
            visited.add(node)
            while queue:
                curr = queue.popleft()
                comp.append(curr)
                for neighbor in adj[curr]:
                    if neighbor not in visited:
                        visited.add(neighbor)
                        queue.append(neighbor)
            clusters.append(comp)

    graph = TopicGraph()
    cluster_mapping = {}

    for i, comp in enumerate(clusters):
        cid = f"cluster_{i}"
        node_label = f"Topic {i+1} ({len(comp)} items)"
        graph.nodes.append(
            TopicGraphNode(
                id=cid,
                label=node_label,
                entry_ids=comp,
                size=len(comp)
            )
        )
        for e_id in comp:
            cluster_mapping[e_id] = cid

    cluster_pairs_sum: collections.defaultdict[tuple[str, str], float] = collections.defaultdict(float)
    cluster_pairs_count: collections.defaultdict[tuple[str, str], int] = collections.defaultdict(int)

    for (id1, id2), dist in pair_distances.items():
        if id1 < id2:
            c1 = cluster_mapping[id1]
            c2 = cluster_mapping[id2]
            if c1 != c2:
                if c1 > c2:
                    c1, c2 = c2, c1
                cluster_pairs_sum[(c1, c2)] += dist
                cluster_pairs_count[(c1, c2)] += 1

    for (c1, c2), total_dist in cluster_pairs_sum.items():
        count = cluster_pairs_count[(c1, c2)]
        avg_dist = total_dist / count
        if avg_dist < 0.45:
            weight = max(0.01, 1.0 - avg_dist)
            graph.edges.append(
                TopicGraphEdge(
                    source=c1,
                    target=c2,
                    weight=round(weight, 3)
                )
            )

    return graph


class TopicLabelGenerator(Protocol):
    async def generate_label(self, entries: list[Entry]) -> str: ...


def get_topic_clusters_from_cache(connection: sqlite3.Connection, group_id: int) -> TopicGraph:
    row = connection.execute("SELECT graph_json FROM topic_cluster_cache WHERE group_id = ?", (group_id,)).fetchone()
    if not row:
        return TopicGraph()
        
    data = json.loads(row["graph_json"])
    
    nodes = [TopicGraphNode(**n) for n in data.get("nodes", [])]
    edges = [TopicGraphEdge(**e) for e in data.get("edges", [])]
    return TopicGraph(nodes=nodes, edges=edges)

def save_topic_clusters_to_cache(connection: sqlite3.Connection, group_id: int, graph: TopicGraph):
    data = asdict(graph)
    graph_json = json.dumps(data)
    now = datetime.datetime.now(datetime.UTC).isoformat()
    
    connection.execute(
        """
        INSERT INTO topic_cluster_cache (group_id, graph_json, updated_utc)
        VALUES (?, ?, ?)
        ON CONFLICT(group_id) DO UPDATE SET
            graph_json=excluded.graph_json,
            updated_utc=excluded.updated_utc
        """,
        (group_id, graph_json, now)
    )
    connection.commit()


def _build_label_messages(entries: list[Entry]) -> list[ChatCompletionMessageParam]:
    text = "\n".join(f"- {e.title}" for e in entries[:20])
    system_msg: ChatCompletionSystemMessageParam = {
        "role": "system",
        "content": "You are a timeline topic classifier. Given a list of event titles, provide a single concise topic name (1 to 3 words max). Reply ONLY with the topic name without quotes.",
    }
    user_msg: ChatCompletionUserMessageParam = {
        "role": "user",
        "content": text,
    }
    return [system_msg, user_msg]


class OpenAITopicLabelGenerator:
    def __init__(self, settings: OpenAISettings):
        self._settings = settings
        self._client = AsyncOpenAI(
            api_key=settings.api_key,
            base_url=settings.base_url or None,
        )

    async def generate_label(self, entries: list[Entry]) -> str:
        if not entries:
            return "Mixed Topics"
        try:
            response = await self._client.chat.completions.create(
                model=self._settings.model_id,
                messages=_build_label_messages(entries),
            )
        except Exception as e:
            logger.warning(f"OpenAI label generation failed: {e}")
            return "Mixed Topics"
        msg = response.choices[0].message.content if response.choices else None
        return (msg or "Topic").strip().strip('"\'')


class CopilotTopicLabelGenerator:
    def __init__(self, settings: CopilotSettings):
        self._settings = settings

    async def generate_label(self, entries: list[Entry]) -> str:
        if not entries:
            return "Mixed Topics"

        try:
            client = copilot_runtime.instantiate_copilot_client(
                self._settings,
                configuration_error_type=Exception,
                missing_sdk_message=copilot_runtime.COPILOT_SDK_REQUIRED_MESSAGE,
                invalid_settings_message=copilot_runtime.COPILOT_CLIENT_SETTINGS_MESSAGE,
            )
            async with AsyncExitStack() as exit_stack:
                active_client = await copilot_runtime.prepare_copilot_client(exit_stack, client)
                session = await copilot_runtime.create_copilot_session(
                    active_client,
                    model_id=self._settings.model_id,
                    system_message="You are a timeline topic classifier. Given a list of event titles, provide a single concise topic name (1 to 3 words max). Reply ONLY with the topic name.'",
                )
                active_session = await copilot_runtime.prepare_copilot_resource(exit_stack, session)
                text = "\n".join(f"- {e.title}" for e in entries[:20])
                response = await copilot_runtime.send_copilot_prompt(active_session, text, timeout=60.0)
                msg = copilot_runtime.extract_copilot_message_content(response)
                return msg.strip().strip('"\'')
        except Exception as e:
            logger.warning(f"Copilot failed to generate label: {e}")
            return "Topic"
        except asyncio.CancelledError as e:
            logger.warning(f"Copilot label generation cancelled or timed out: {e}")
            return "Topic"


def load_topic_label_generator() -> TopicLabelGenerator:
    provider = load_ai_provider()
    if provider == "openai":
        return OpenAITopicLabelGenerator(load_openai_settings())
    elif provider == "copilot":
        return CopilotTopicLabelGenerator(load_copilot_settings())
    raise ValueError(f"Unsupported AI provider: {provider}")


# ---------------------------------------------------------------------------
# Multi-tag generation: AI assigns multiple topic tags per entry
# ---------------------------------------------------------------------------


def _build_tags_messages(entry: Entry) -> list[ChatCompletionMessageParam]:
    text_excerpt = (entry.final_text or "")[:600]
    system_msg: ChatCompletionSystemMessageParam = {
        "role": "system",
        "content": (
            "You are a timeline topic classifier. "
            "Given an event title and summary, return 2 to 5 concise topic tags (1 to 3 words each). "
            'Reply ONLY with a JSON array of strings, e.g. ["AI Safety", "Open Source", "Policy"]. '
            "Do not include any explanation or extra text."
        ),
    }
    user_msg: ChatCompletionUserMessageParam = {
        "role": "user",
        "content": f"Title: {entry.title}\n\nSummary: {text_excerpt}",
    }
    return [system_msg, user_msg]


def _parse_tags_response(text: str) -> list[str]:
    """Extract a list of tag strings from an AI JSON-array response."""
    match = re.search(r"\[.*?\]", text, re.DOTALL)
    if match:
        try:
            tags = json.loads(match.group())
            if isinstance(tags, list):
                return [
                    str(t).strip()
                    for t in tags
                    if isinstance(t, str) and str(t).strip()
                ]
        except json.JSONDecodeError:
            pass
    return []


class EntryTagGenerator(Protocol):
    async def generate_tags(self, entry: Entry) -> list[str]: ...


class OpenAIEntryTagGenerator:
    def __init__(self, settings: OpenAISettings):
        self._settings = settings
        self._client = AsyncOpenAI(
            api_key=settings.api_key,
            base_url=settings.base_url or None,
        )

    async def generate_tags(self, entry: Entry) -> list[str]:
        try:
            response = await self._client.chat.completions.create(
                model=self._settings.model_id,
                messages=_build_tags_messages(entry),
            )
        except Exception as e:
            logger.warning(f"OpenAI tag generation failed for entry {entry.id}: {e}")
            return []
        content = response.choices[0].message.content if response.choices else None
        return _parse_tags_response(content or "")


class CopilotEntryTagGenerator:
    def __init__(self, settings: CopilotSettings):
        self._settings = settings

    async def generate_tags(self, entry: Entry) -> list[str]:
        try:
            client = copilot_runtime.instantiate_copilot_client(
                self._settings,
                configuration_error_type=Exception,
                missing_sdk_message=copilot_runtime.COPILOT_SDK_REQUIRED_MESSAGE,
                invalid_settings_message=copilot_runtime.COPILOT_CLIENT_SETTINGS_MESSAGE,
            )
            async with AsyncExitStack() as exit_stack:
                active_client = await copilot_runtime.prepare_copilot_client(exit_stack, client)
                session = await copilot_runtime.create_copilot_session(
                    active_client,
                    model_id=self._settings.model_id,
                    system_message=(
                        "You are a timeline topic classifier. "
                        "Given an event title and summary, return 2 to 5 concise topic tags (1 to 3 words each). "
                        'Reply ONLY with a JSON array of strings, e.g. ["AI Safety", "Open Source", "Policy"]. '
                        "Do not include any explanation or extra text."
                    ),
                )
                active_session = await copilot_runtime.prepare_copilot_resource(exit_stack, session)
                text_excerpt = (entry.final_text or "")[:600]
                prompt = f"Title: {entry.title}\n\nSummary: {text_excerpt}"
                response = await copilot_runtime.send_copilot_prompt(active_session, prompt, timeout=60.0)
                msg = copilot_runtime.extract_copilot_message_content(response)
                return _parse_tags_response(msg or "")
        except asyncio.CancelledError as e:
            logger.warning(f"Copilot tag generation cancelled for entry {entry.id}: {e}")
            return []
        except Exception as e:
            logger.warning(f"Copilot tag generation failed for entry {entry.id}: {e}")
            return []


def load_entry_tag_generator() -> EntryTagGenerator:
    provider = load_ai_provider()
    if provider == "openai":
        return OpenAIEntryTagGenerator(load_openai_settings())
    elif provider == "copilot":
        return CopilotEntryTagGenerator(load_copilot_settings())
    raise ValueError(f"Unsupported AI provider: {provider}")


def build_tag_graph(connection: sqlite3.Connection, group_id: int) -> TopicGraph:
    """Build a topic graph from existing entry tags for a group.

    Each unique tag becomes a node.  Edges connect tags that co-occur on the
    same entry; edge weight is the Jaccard-like co-occurrence ratio.
    """
    rows = connection.execute(
        """
        SELECT et.entry_id, t.name AS tag_name
        FROM entry_tags et
        JOIN tags t ON t.id = et.tag_id
        JOIN entries e ON e.id = et.entry_id
        WHERE e.group_id = ?
        """,
        (group_id,),
    ).fetchall()

    if not rows:
        return TopicGraph()

    tag_entries: dict[str, list[int]] = {}
    entry_tags_map: dict[int, list[str]] = {}
    for row in rows:
        tag_name: str = row["tag_name"]
        entry_id: int = row["entry_id"]
        tag_entries.setdefault(tag_name, []).append(entry_id)
        entry_tags_map.setdefault(entry_id, []).append(tag_name)

    nodes = [
        TopicGraphNode(id=tag, label=tag, entry_ids=ids, size=len(ids))
        for tag, ids in tag_entries.items()
    ]

    co_occurrence: dict[tuple[str, str], int] = {}
    for tags in entry_tags_map.values():
        unique_tags = list(dict.fromkeys(tags))
        for i, t1 in enumerate(unique_tags):
            for t2 in unique_tags[i + 1 :]:
                key = (min(t1, t2), max(t1, t2))
                co_occurrence[key] = co_occurrence.get(key, 0) + 1

    edges: list[TopicGraphEdge] = []
    for (t1, t2), count in co_occurrence.items():
        max_size = min(len(tag_entries[t1]), len(tag_entries[t2]))
        weight = count / max_size if max_size > 0 else 0.0
        if weight >= 0.1 or count >= 2:
            edges.append(TopicGraphEdge(source=t1, target=t2, weight=round(weight, 3)))

    return TopicGraph(nodes=nodes, edges=edges)


async def compute_topic_clusters(connection: sqlite3.Connection, group_id: int) -> TopicGraph:
    """For each entry in the group, use AI to generate multiple topic tags and
    merge them into the entry (without removing existing tags).  Then build and
    return a tag-based topic graph.
    """
    entries = list_timeline_entries(connection, group_id=group_id)
    if not entries:
        logger.info(f"No entries found for group {group_id}.")
        return TopicGraph()

    try:
        generator = load_entry_tag_generator()
        logger.info(f"Using entry tag generator: {generator.__class__.__name__}")
    except Exception as e:
        logger.error(f"Entry tag generator unavailable: {e}")
        return build_tag_graph(connection, group_id)

    semaphore = asyncio.Semaphore(5)

    async def _generate_tags_for_entry(
        idx: int, entry: object,
    ) -> tuple[object, list[str]] | None:
        logger.info(f"Generating tags for entry {idx}/{len(entries)}: {entry.title[:60]}")
        try:
            async with semaphore:
                new_tags = await generator.generate_tags(entry)
            return (entry, new_tags) if new_tags else None
        except Exception as e:
            logger.warning(f"Failed to generate tags for entry {entry.id}: {e}")
            return None

    results = await asyncio.gather(
        *[_generate_tags_for_entry(i, e) for i, e in enumerate(entries, start=1)]
    )

    for result in results:
        if result is not None:
            entry, new_tags = result
            merge_entry_tags(connection, entry.id, new_tags)
            logger.info(f"Merged tags for entry {entry.id}: {new_tags}")
    connection.commit()

    logger.info(f"Building tag graph for group {group_id}...")
    graph = build_tag_graph(connection, group_id)
    logger.info(f"Tag graph has {len(graph.nodes)} nodes and {len(graph.edges)} edges.")
    return graph

