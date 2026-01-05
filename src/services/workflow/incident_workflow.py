"""
Temporal Workflow for Incident Processing.
Orchestrates the full incident lifecycle from evidence collection to remediation.
"""
from datetime import timedelta
from typing import Any
from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from src.models import Incident


@workflow.defn
class IncidentWorkflow:
    """
    End-to-end incident resolution workflow.
    
    Steps:
    1. Parse and normalize alert
    2. Scope blast radius
    3. Collect evidence in parallel (K8s, logs, metrics, deploy diffs)
    4. Build evidence graph
    5. Generate hypotheses
    6. Rank hypotheses
    7. Generate runbook
    8. Evaluate remediation policy
    9. Execute remediation (if approved)
    10. Verify remediation
    11. Create ticket (if needed)
    12. Close incident
    """
    
    def __init__(self):
        self._status = "initialized"
        self._hypotheses = []
        self._evidence_count = 0
        self._remediation_result = None
    
    @workflow.query
    def status(self) -> str:
        """Query current workflow status."""
        return self._status
    
    @workflow.query
    def hypotheses(self) -> list:
        """Query generated hypotheses."""
        return self._hypotheses
    
    @workflow.query
    def evidence_count(self) -> int:
        """Query evidence count."""
        return self._evidence_count
    
    @workflow.run
    async def run(self, incident_data: dict) -> dict:
        """Execute the incident workflow."""
        
        # Default retry policy
        default_retry = RetryPolicy(
            initial_interval=timedelta(seconds=1),
            maximum_interval=timedelta(minutes=5),
            maximum_attempts=3,
            non_retryable_error_types=["ValueError", "TypeError"],
        )
        
        # Quick retry for fast operations
        quick_retry = RetryPolicy(
            initial_interval=timedelta(seconds=1),
            maximum_interval=timedelta(seconds=30),
            maximum_attempts=3,
        )
        
        result = {
            "incident_id": incident_data.get("id"),
            "status": "started",
            "steps_completed": [],
        }
        
        try:
            # Step 1: Update status
            self._status = "collecting_evidence"
            
            # Step 2: Collect evidence in parallel
            evidence_results = await workflow.execute_activity(
                "collect_all_evidence",
                incident_data,
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=default_retry,
            )
            
            self._evidence_count = evidence_results.get("total_evidence", 0)
            result["steps_completed"].append("evidence_collection")
            result["evidence_count"] = self._evidence_count
            
            # Step 3: Build evidence graph
            self._status = "building_graph"
            
            graph_result = await workflow.execute_activity(
                "build_evidence_graph",
                {
                    "incident": incident_data,
                    "evidence": evidence_results,
                },
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=default_retry,
            )
            
            result["steps_completed"].append("graph_building")
            result["graph_nodes"] = graph_result.get("node_count", 0)
            
            # Step 4: Generate hypotheses
            self._status = "analyzing"
            
            hypotheses = await workflow.execute_activity(
                "generate_hypotheses",
                {
                    "incident": incident_data,
                    "evidence": evidence_results,
                    "graph": graph_result,
                },
                start_to_close_timeout=timedelta(minutes=3),
                retry_policy=default_retry,
            )
            
            self._hypotheses = hypotheses
            result["steps_completed"].append("hypothesis_generation")
            result["hypotheses_count"] = len(hypotheses)
            
            # Step 5: Rank hypotheses
            ranked_hypotheses = await workflow.execute_activity(
                "rank_hypotheses",
                hypotheses,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=quick_retry,
            )
            
            result["steps_completed"].append("hypothesis_ranking")
            result["top_hypothesis"] = ranked_hypotheses[0] if ranked_hypotheses else None
            
            # Step 6: Generate runbook
            self._status = "generating_runbook"
            
            runbook = await workflow.execute_activity(
                "generate_runbook",
                {
                    "incident": incident_data,
                    "hypotheses": ranked_hypotheses,
                },
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=quick_retry,
            )
            
            result["steps_completed"].append("runbook_generation")
            result["runbook_id"] = runbook.get("id")
            
            # Step 7: Calculate blast radius
            blast_radius = await workflow.execute_activity(
                "calculate_blast_radius",
                incident_data,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=quick_retry,
            )
            
            result["blast_radius"] = blast_radius
            
            # Step 8: Evaluate remediation policy
            self._status = "evaluating_policy"
            
            policy_result = await workflow.execute_activity(
                "evaluate_remediation_policy",
                {
                    "incident": incident_data,
                    "hypotheses": ranked_hypotheses,
                    "blast_radius": blast_radius,
                },
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=quick_retry,
            )
            
            result["steps_completed"].append("policy_evaluation")
            result["remediation_allowed"] = policy_result.get("allowed", False)
            result["requires_approval"] = policy_result.get("requires_approval", True)
            
            # Step 9: Handle remediation
            if policy_result.get("allowed"):
                self._status = "remediating"
                
                if policy_result.get("requires_approval"):
                    # Request approval
                    approval_result = await workflow.execute_activity(
                        "request_approval",
                        {
                            "incident": incident_data,
                            "action": policy_result.get("proposed_action"),
                            "blast_radius": blast_radius,
                        },
                        start_to_close_timeout=timedelta(hours=4),  # Long timeout for human approval
                        retry_policy=quick_retry,
                    )
                    
                    result["approval_requested"] = True
                    result["approval_granted"] = approval_result.get("approved", False)
                    
                    if not approval_result.get("approved"):
                        self._status = "approval_denied"
                        result["steps_completed"].append("approval_denied")
                        # Skip to ticket creation
                        policy_result["allowed"] = False
                
                if policy_result.get("allowed"):
                    # Execute remediation
                    remediation_result = await workflow.execute_activity(
                        "execute_remediation",
                        {
                            "incident": incident_data,
                            "action": policy_result.get("proposed_action"),
                        },
                        start_to_close_timeout=timedelta(minutes=5),
                        retry_policy=default_retry,
                    )
                    
                    self._remediation_result = remediation_result
                    result["steps_completed"].append("remediation_executed")
                    result["remediation_success"] = remediation_result.get("success", False)
                    
                    # Step 10: Wait and verify
                    self._status = "verifying"
                    await workflow.sleep(timedelta(minutes=2))
                    
                    verification = await workflow.execute_activity(
                        "verify_remediation",
                        {
                            "incident": incident_data,
                            "action": policy_result.get("proposed_action"),
                        },
                        start_to_close_timeout=timedelta(minutes=2),
                        retry_policy=default_retry,
                    )
                    
                    result["steps_completed"].append("verification")
                    result["verification_success"] = verification.get("success", False)
                    result["metrics_improved"] = verification.get("metrics_improved", False)
            
            # Step 11: Create ticket if needed
            needs_ticket = (
                not policy_result.get("allowed") or
                not result.get("verification_success", True) or
                not result.get("remediation_success", True)
            )
            
            if needs_ticket:
                self._status = "creating_ticket"
                
                ticket_result = await workflow.execute_activity(
                    "create_ticket",
                    {
                        "incident": incident_data,
                        "hypotheses": ranked_hypotheses,
                        "runbook": runbook,
                        "remediation_attempted": result.get("remediation_success") is not None,
                    },
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=quick_retry,
                )
                
                result["steps_completed"].append("ticket_created")
                result["ticket_id"] = ticket_result.get("ticket_id")
            
            # Step 12: Close incident
            self._status = "closing"
            
            await workflow.execute_activity(
                "close_incident",
                {
                    "incident": incident_data,
                    "result": result,
                },
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=quick_retry,
            )
            
            result["steps_completed"].append("incident_closed")
            self._status = "completed"
            result["status"] = "completed"
            
        except Exception as e:
            self._status = f"failed: {str(e)}"
            result["status"] = "failed"
            result["error"] = str(e)
        
        return result
