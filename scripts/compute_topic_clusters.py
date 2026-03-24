import asyncio
import logging
from app.db import connection_context
from app.services.topics import compute_topic_clusters, save_topic_clusters_to_cache
from app.services.entries import list_timeline_groups

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def run_compute():
    with connection_context() as connection:
        groups = list_timeline_groups(connection)
        for group in groups:
            logger.info(f"Computing topic clusters for group: {group.name} (ID: {group.id})")
            try:
                graph = await compute_topic_clusters(connection, group.id)
                if graph.nodes:
                    save_topic_clusters_to_cache(connection, group.id, graph)
                    logger.info(f"Successfully cached topic clusters for group {group.id} ({len(graph.nodes)} nodes)")
                else:
                    logger.warning(f"No clusters generated for group {group.id}")
            except Exception as e:
                logger.error(f"Failed to compute clusters for group {group.id}: {e}")

if __name__ == "__main__":
    asyncio.run(run_compute())