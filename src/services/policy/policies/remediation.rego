package remediation

import rego.v1

# Default deny
default allow := false

# Environment gates
env_allows_action if {
    input.environment == "dev"
    input.action_type in allowed_actions_dev
}

env_allows_action if {
    input.environment == "staging"
    input.action_type in allowed_actions_staging
    not in_freeze_window
}

env_allows_action if {
    input.environment == "prod"
    input.action_type in allowed_actions_prod
    not in_freeze_window
}

# Action allowlists by environment
allowed_actions_dev := {
    "restart_pod",
    "delete_pod",
    "restart_deployment",
    "rollback_deployment",
    "scale_replicas",
    "cordon_node"
}

allowed_actions_staging := {
    "restart_pod",
    "delete_pod",
    "restart_deployment",
    "scale_replicas",
    "rollback_deployment"
}

allowed_actions_prod := {
    "restart_pod",
    "delete_pod",
    "restart_deployment",
    "scale_replicas"
}

# High risk actions - never auto-approve
high_risk_actions := {
    "drain_node",
    "delete_pvc",
    "update_resource_limits",
    "delete_namespace",
    "update_configmap",
    "uncordon_node"
}

# Freeze window detection
in_freeze_window if {
    # Late night freeze (10 PM - 6 AM)
    input.current_hour >= 22
}

in_freeze_window if {
    input.current_hour < 6
}

in_freeze_window if {
    # Weekend freeze for production
    input.environment == "prod"
    input.is_weekend == true
}

in_freeze_window if {
    # Explicit freeze window set
    input.freeze_active == true
}

# Blast radius acceptable
blast_radius_ok if {
    input.blast_radius_score < 50
    input.affected_replicas < 5
}

blast_radius_ok if {
    input.environment == "dev"
}

blast_radius_ok if {
    input.environment == "staging"
    input.blast_radius_score < 75
}

# Namespace restrictions
namespace_allowed if {
    not input.namespace in protected_namespaces
}

namespace_allowed if {
    input.environment == "dev"
}

protected_namespaces := {
    "kube-system",
    "kube-public",
    "kube-node-lease",
    "istio-system",
    "cert-manager",
    "monitoring"
}

# Main allow rule
allow if {
    env_allows_action
    blast_radius_ok
    namespace_allowed
    not input.action_type in high_risk_actions
}

# Require approval rules
requires_approval if {
    input.environment == "prod"
}

requires_approval if {
    input.environment == "staging"
    input.blast_radius_score >= 30
}

requires_approval if {
    input.action_type == "rollback_deployment"
}

requires_approval if {
    input.action_type == "cordon_node"
}

requires_approval if {
    input.affected_replicas >= 3
}

# Denial reasons
deny contains msg if {
    not env_allows_action
    input.action_type in high_risk_actions
    msg := sprintf("Action %s is high risk and not allowed", [input.action_type])
}

deny contains msg if {
    not env_allows_action
    in_freeze_window
    msg := "Action not allowed during freeze window"
}

deny contains msg if {
    not namespace_allowed
    msg := sprintf("Namespace %s is protected", [input.namespace])
}

deny contains msg if {
    not blast_radius_ok
    msg := sprintf("Blast radius score %v exceeds threshold", [input.blast_radius_score])
}
