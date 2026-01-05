"""
Slack Client for ChatOps approvals.
"""
from datetime import datetime
from typing import Any, Optional
import structlog

from src.config import settings


logger = structlog.get_logger()


class SlackClient:
    """Slack integration for approval workflows."""
    
    def __init__(self):
        self.bot_token = settings.slack_bot_token
        self.channel = settings.slack_approval_channel
    
    async def request_approval(
        self,
        incident: dict,
        action: str,
        blast_radius: dict,
    ) -> dict[str, Any]:
        """Request approval via Slack."""
        if not self.bot_token or not self.channel:
            logger.warning("Slack not configured, auto-denying")
            return {"approved": False, "reason": "Slack not configured"}
        
        try:
            from slack_sdk.web.async_client import AsyncWebClient
            
            client = AsyncWebClient(token=self.bot_token)
            
            blocks = self._build_approval_blocks(incident, action, blast_radius)
            
            response = await client.chat_postMessage(
                channel=self.channel,
                text=f"🚨 Approval needed: {action} for {incident.get('title')}",
                blocks=blocks,
            )
            
            message_ts = response.get("ts")
            
            # In production, we'd wait for an interactive response
            # For now, return pending
            return {
                "approved": False,
                "pending": True,
                "message_ts": message_ts,
                "channel": self.channel,
            }
            
        except ImportError:
            logger.warning("slack_sdk not installed")
            return {"approved": False, "reason": "slack_sdk not installed"}
        except Exception as e:
            logger.error("Slack approval request failed", error=str(e))
            return {"approved": False, "reason": str(e)}
    
    def _build_approval_blocks(
        self,
        incident: dict,
        action: str,
        blast_radius: dict,
    ) -> list:
        """Build Slack block kit message."""
        return [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": "🚨 Remediation Approval Required",
                }
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Incident:*\n{incident.get('title', 'Unknown')}"},
                    {"type": "mrkdwn", "text": f"*Severity:*\n{incident.get('severity', 'Unknown')}"},
                    {"type": "mrkdwn", "text": f"*Namespace:*\n{incident.get('namespace', 'Unknown')}"},
                    {"type": "mrkdwn", "text": f"*Action:*\n{action}"},
                ]
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Blast Radius:*\n{blast_radius.get('score', 0):.1f}"},
                    {"type": "mrkdwn", "text": f"*Affected Pods:*\n{blast_radius.get('affected_pods', 0)}"},
                ]
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "✅ Approve"},
                        "style": "primary",
                        "action_id": "approve_action",
                        "value": incident.get("id", ""),
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "❌ Reject"},
                        "style": "danger",
                        "action_id": "reject_action",
                        "value": incident.get("id", ""),
                    },
                ]
            },
        ]


class JiraClient:
    """Jira integration for ticket creation."""
    
    def __init__(self):
        self.jira_url = settings.jira_url
        self.user = settings.jira_user
        self.token = settings.jira_api_token
        self.project = settings.jira_project_key
    
    def create_incident_ticket(
        self,
        incident: dict,
        hypotheses: list,
        runbook: dict,
    ) -> dict[str, Any]:
        """Create a Jira ticket for the incident."""
        if not self.jira_url or not self.token:
            logger.info("Jira not configured")
            return {"ticket_id": None}
        
        try:
            from jira import JIRA
            
            jira = JIRA(
                server=self.jira_url,
                basic_auth=(self.user, self.token),
            )
            
            description = self._build_description(incident, hypotheses, runbook)
            
            issue = jira.create_issue(
                project=self.project,
                summary=f"[Incident] {incident.get('title', 'Unknown Incident')}",
                description=description,
                issuetype={"name": "Bug"},
                priority={"name": self._map_severity(incident.get("severity"))},
            )
            
            logger.info("Created Jira ticket", key=issue.key)
            
            return {
                "ticket_id": issue.key,
                "ticket_url": f"{self.jira_url}/browse/{issue.key}",
            }
            
        except ImportError:
            logger.warning("jira package not installed")
            return {"ticket_id": None}
        except Exception as e:
            logger.error("Jira ticket creation failed", error=str(e))
            return {"ticket_id": None, "error": str(e)}
    
    def _build_description(
        self,
        incident: dict,
        hypotheses: list,
        runbook: dict,
    ) -> str:
        """Build Jira ticket description."""
        top_hypothesis = hypotheses[0] if hypotheses else {}
        
        return f"""
h2. Incident Details
* *Cluster:* {incident.get('cluster', 'Unknown')}
* *Namespace:* {incident.get('namespace', 'Unknown')}
* *Service:* {incident.get('service', 'N/A')}
* *Started:* {incident.get('started_at', 'Unknown')}

h2. Root Cause Analysis
*Top Hypothesis:* {top_hypothesis.get('title', 'Unknown')}
*Confidence:* {top_hypothesis.get('confidence', 0):.0%}

{top_hypothesis.get('description', 'No description available')}

h2. Recommended Actions
{chr(10).join(['* ' + a for a in top_hypothesis.get('recommended_actions', ['Investigate manually'])])}

h2. Evidence
See runbook for investigation commands and dashboard links.
"""
    
    def _map_severity(self, severity: str) -> str:
        """Map incident severity to Jira priority."""
        mapping = {
            "critical": "Highest",
            "high": "High",
            "medium": "Medium",
            "low": "Low",
            "info": "Lowest",
        }
        return mapping.get(severity, "Medium")
