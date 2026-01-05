"""
Neo4j graph database connection and operations.
Used for storing and querying the Evidence Graph.
"""
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Optional
import structlog
from neo4j import AsyncGraphDatabase, AsyncDriver, AsyncSession
from neo4j.exceptions import ServiceUnavailable

from src.config import settings
from src.models.evidence import GraphEntity, GraphRelation


logger = structlog.get_logger()


class Neo4jConnection:
    """Neo4j database connection manager."""
    
    _driver: Optional[AsyncDriver] = None
    
    @classmethod
    async def get_driver(cls) -> AsyncDriver:
        """Get or create the Neo4j driver."""
        if cls._driver is None:
            cls._driver = AsyncGraphDatabase.driver(
                settings.neo4j_uri,
                auth=(settings.neo4j_user, settings.neo4j_password),
                max_connection_lifetime=3600,
                max_connection_pool_size=50,
            )
            logger.info("Neo4j driver initialized", uri=settings.neo4j_uri)
        return cls._driver
    
    @classmethod
    async def close(cls) -> None:
        """Close the Neo4j driver."""
        if cls._driver is not None:
            await cls._driver.close()
            cls._driver = None
            logger.info("Neo4j driver closed")
    
    @classmethod
    async def verify_connectivity(cls) -> bool:
        """Verify Neo4j connectivity."""
        try:
            driver = await cls.get_driver()
            await driver.verify_connectivity()
            return True
        except ServiceUnavailable as e:
            logger.error("Neo4j connectivity check failed", error=str(e))
            return False


@asynccontextmanager
async def get_neo4j_session() -> AsyncGenerator[AsyncSession, None]:
    """Get an async Neo4j session."""
    driver = await Neo4jConnection.get_driver()
    session = driver.session(database="neo4j")
    try:
        yield session
    finally:
        await session.close()


class GraphService:
    """Service for Evidence Graph operations."""
    
    @staticmethod
    async def create_entity(entity: GraphEntity) -> str:
        """Create a node in the graph."""
        async with get_neo4j_session() as session:
            # Build property string for Cypher
            props = entity.properties.copy()
            props["id"] = entity.id
            
            query = f"""
            MERGE (n:{entity.type} {{id: $id}})
            SET n += $properties
            RETURN n.id as id
            """
            
            result = await session.run(
                query, 
                id=entity.id, 
                properties=props
            )
            record = await result.single()
            
            logger.debug("Created graph entity", entity_id=entity.id, type=entity.type)
            return record["id"] if record else entity.id
    
    @staticmethod
    async def create_entities_batch(entities: list[GraphEntity]) -> int:
        """Create multiple entities in a batch."""
        async with get_neo4j_session() as session:
            count = 0
            for entity in entities:
                props = entity.properties.copy()
                props["id"] = entity.id
                
                query = f"""
                MERGE (n:{entity.type} {{id: $id}})
                SET n += $properties
                """
                
                await session.run(query, id=entity.id, properties=props)
                count += 1
            
            logger.info("Created entities batch", count=count)
            return count
    
    @staticmethod
    async def create_relation(relation: GraphRelation) -> bool:
        """Create a relationship between two entities."""
        async with get_neo4j_session() as session:
            query = f"""
            MATCH (source {{id: $source_id}})
            MATCH (target {{id: $target_id}})
            MERGE (source)-[r:{relation.relation_type}]->(target)
            SET r += $properties
            RETURN type(r) as rel_type
            """
            
            result = await session.run(
                query,
                source_id=relation.source_id,
                target_id=relation.target_id,
                properties=relation.properties,
            )
            record = await result.single()
            
            if record:
                logger.debug(
                    "Created graph relation",
                    source=relation.source_id,
                    target=relation.target_id,
                    type=relation.relation_type,
                )
                return True
            return False
    
    @staticmethod
    async def create_relations_batch(relations: list[GraphRelation]) -> int:
        """Create multiple relationships in a batch."""
        async with get_neo4j_session() as session:
            count = 0
            for rel in relations:
                query = f"""
                MATCH (source {{id: $source_id}})
                MATCH (target {{id: $target_id}})
                MERGE (source)-[r:{rel.relation_type}]->(target)
                SET r += $properties
                """
                
                await session.run(
                    query,
                    source_id=rel.source_id,
                    target_id=rel.target_id,
                    properties=rel.properties,
                )
                count += 1
            
            logger.info("Created relations batch", count=count)
            return count
    
    @staticmethod
    async def get_incident_graph(incident_id: str, depth: int = 3) -> dict[str, Any]:
        """Get the evidence graph for an incident."""
        async with get_neo4j_session() as session:
            query = """
            MATCH (i:Incident {id: $incident_id})
            CALL apoc.path.subgraphAll(i, {maxLevel: $depth}) YIELD nodes, relationships
            RETURN nodes, relationships
            """
            
            result = await session.run(query, incident_id=incident_id, depth=depth)
            record = await result.single()
            
            if not record:
                return {"nodes": [], "relationships": []}
            
            nodes = []
            for node in record["nodes"]:
                nodes.append({
                    "id": dict(node).get("id"),
                    "labels": list(node.labels),
                    "properties": dict(node),
                })
            
            relationships = []
            for rel in record["relationships"]:
                relationships.append({
                    "type": rel.type,
                    "source": dict(rel.start_node).get("id"),
                    "target": dict(rel.end_node).get("id"),
                    "properties": dict(rel),
                })
            
            return {"nodes": nodes, "relationships": relationships}
    
    @staticmethod
    async def find_related_changes(
        incident_id: str, 
        time_window_minutes: int = 30
    ) -> list[dict[str, Any]]:
        """Find deployment/config changes related to an incident."""
        async with get_neo4j_session() as session:
            query = """
            MATCH (i:Incident {id: $incident_id})-[:AFFECTS]->(s)
            MATCH (s)<-[:APPLIES_TO]-(c:ChangeEvent)
            WHERE c.changed_at >= datetime() - duration({minutes: $window})
            RETURN c
            ORDER BY c.changed_at DESC
            """
            
            result = await session.run(
                query, 
                incident_id=incident_id, 
                window=time_window_minutes
            )
            
            changes = []
            async for record in result:
                changes.append(dict(record["c"]))
            
            return changes
    
    @staticmethod
    async def find_affected_by_node(node_name: str) -> list[dict[str, Any]]:
        """Find all pods/services affected by a problematic node."""
        async with get_neo4j_session() as session:
            query = """
            MATCH (n:Node {name: $node_name})<-[:SCHEDULED_ON]-(p:Pod)
            MATCH (p)<-[:OWNS*]-(d:Deployment)
            OPTIONAL MATCH (d)<-[:SELECTS]-(s:Service)
            RETURN p, d, s
            """
            
            result = await session.run(query, node_name=node_name)
            
            affected = []
            async for record in result:
                affected.append({
                    "pod": dict(record["p"]) if record["p"] else None,
                    "deployment": dict(record["d"]) if record["d"] else None,
                    "service": dict(record["s"]) if record["s"] else None,
                })
            
            return affected
    
    @staticmethod
    async def get_service_dependencies(
        service_name: str, 
        namespace: str
    ) -> dict[str, Any]:
        """Get upstream and downstream dependencies of a service."""
        async with get_neo4j_session() as session:
            query = """
            MATCH (s:Service {name: $service_name, namespace: $namespace})
            OPTIONAL MATCH (s)-[:CALLS]->(downstream:Service)
            OPTIONAL MATCH (upstream:Service)-[:CALLS]->(s)
            RETURN s, collect(DISTINCT downstream) as downstream, collect(DISTINCT upstream) as upstream
            """
            
            result = await session.run(
                query, 
                service_name=service_name, 
                namespace=namespace
            )
            record = await result.single()
            
            return {
                "service": dict(record["s"]) if record and record["s"] else None,
                "downstream": [dict(d) for d in record["downstream"]] if record else [],
                "upstream": [dict(u) for u in record["upstream"]] if record else [],
            }
    
    @staticmethod
    async def cleanup_incident_graph(incident_id: str) -> int:
        """Remove all nodes and relationships for an incident."""
        async with get_neo4j_session() as session:
            query = """
            MATCH (i:Incident {id: $incident_id})
            CALL apoc.path.subgraphAll(i, {maxLevel: 10}) YIELD nodes
            DETACH DELETE nodes
            RETURN count(*) as deleted
            """
            
            result = await session.run(query, incident_id=incident_id)
            record = await result.single()
            
            deleted = record["deleted"] if record else 0
            logger.info("Cleaned up incident graph", incident_id=incident_id, deleted=deleted)
            return deleted
    
    @staticmethod
    async def init_constraints() -> None:
        """Initialize graph database constraints and indexes."""
        async with get_neo4j_session() as session:
            constraints = [
                "CREATE CONSTRAINT incident_id IF NOT EXISTS FOR (i:Incident) REQUIRE i.id IS UNIQUE",
                "CREATE CONSTRAINT pod_id IF NOT EXISTS FOR (p:Pod) REQUIRE p.id IS UNIQUE",
                "CREATE CONSTRAINT deployment_id IF NOT EXISTS FOR (d:Deployment) REQUIRE d.id IS UNIQUE",
                "CREATE CONSTRAINT service_id IF NOT EXISTS FOR (s:Service) REQUIRE s.id IS UNIQUE",
                "CREATE CONSTRAINT node_id IF NOT EXISTS FOR (n:Node) REQUIRE n.id IS UNIQUE",
                "CREATE CONSTRAINT change_id IF NOT EXISTS FOR (c:ChangeEvent) REQUIRE c.id IS UNIQUE",
                "CREATE INDEX incident_fingerprint IF NOT EXISTS FOR (i:Incident) ON (i.fingerprint)",
                "CREATE INDEX pod_namespace IF NOT EXISTS FOR (p:Pod) ON (p.namespace)",
            ]
            
            for constraint in constraints:
                try:
                    await session.run(constraint)
                except Exception as e:
                    # Constraint might already exist
                    logger.debug("Constraint creation skipped", error=str(e))
            
            logger.info("Neo4j constraints initialized")
