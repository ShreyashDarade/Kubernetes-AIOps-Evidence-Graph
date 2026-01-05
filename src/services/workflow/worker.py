"""
Temporal Worker for Incident Workflows.
Runs the workflow and activities.
"""
import asyncio
import structlog
from temporalio.client import Client
from temporalio.worker import Worker

from src.config import settings
from src.services.workflow.incident_workflow import IncidentWorkflow
from src.services.workflow.activities import (
    collect_all_evidence,
    build_evidence_graph,
    generate_hypotheses,
    rank_hypotheses,
    generate_runbook,
    calculate_blast_radius,
    evaluate_remediation_policy,
    request_approval,
    execute_remediation,
    verify_remediation,
    create_ticket,
    close_incident,
)


logger = structlog.get_logger()


async def run_worker():
    """Start the Temporal worker."""
    logger.info(
        "Starting Temporal worker",
        address=settings.temporal_address,
        task_queue=settings.temporal_task_queue,
    )
    
    # Connect to Temporal
    client = await Client.connect(settings.temporal_address)
    
    # Create and run worker
    worker = Worker(
        client,
        task_queue=settings.temporal_task_queue,
        workflows=[IncidentWorkflow],
        activities=[
            collect_all_evidence,
            build_evidence_graph,
            generate_hypotheses,
            rank_hypotheses,
            generate_runbook,
            calculate_blast_radius,
            evaluate_remediation_policy,
            request_approval,
            execute_remediation,
            verify_remediation,
            create_ticket,
            close_incident,
        ],
    )
    
    logger.info("Worker started, listening for tasks")
    await worker.run()


def main():
    """Main entry point."""
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
