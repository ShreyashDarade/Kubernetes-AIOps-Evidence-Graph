"""
Incident Simulator.
Creates test incidents by deploying faulty apps to Kubernetes.
"""
import click
import asyncio
import structlog
from datetime import datetime
from kubernetes import client, config


logger = structlog.get_logger()


CRASHLOOP_MANIFEST = """
apiVersion: apps/v1
kind: Deployment
metadata:
  name: crashloop-demo
  namespace: {namespace}
  labels:
    app: crashloop-demo
    simulator: aiops-test
spec:
  replicas: 1
  selector:
    matchLabels:
      app: crashloop-demo
  template:
    metadata:
      labels:
        app: crashloop-demo
    spec:
      containers:
      - name: crashloop
        image: busybox
        command: ["sh", "-c", "echo Starting && exit 1"]
        resources:
          limits:
            memory: "64Mi"
            cpu: "100m"
"""

OOM_MANIFEST = """
apiVersion: apps/v1
kind: Deployment
metadata:
  name: oom-demo
  namespace: {namespace}
  labels:
    app: oom-demo
    simulator: aiops-test
spec:
  replicas: 1
  selector:
    matchLabels:
      app: oom-demo
  template:
    metadata:
      labels:
        app: oom-demo
    spec:
      containers:
      - name: oom
        image: python:3.11-slim
        command: ["python", "-c", "import time; data = []; [data.append('x' * 10000000) or time.sleep(0.1) for _ in range(1000)]"]
        resources:
          limits:
            memory: "64Mi"
            cpu: "100m"
"""

IMAGE_PULL_MANIFEST = """
apiVersion: apps/v1
kind: Deployment
metadata:
  name: imagepull-demo
  namespace: {namespace}
  labels:
    app: imagepull-demo
    simulator: aiops-test
spec:
  replicas: 1
  selector:
    matchLabels:
      app: imagepull-demo
  template:
    metadata:
      labels:
        app: imagepull-demo
    spec:
      containers:
      - name: imagepull
        image: nonexistent-registry.io/fake/image:v99.99.99
        resources:
          limits:
            memory: "64Mi"
            cpu: "100m"
"""

SLOW_APP_MANIFEST = """
apiVersion: apps/v1
kind: Deployment
metadata:
  name: slowapp-demo
  namespace: {namespace}
  labels:
    app: slowapp-demo
    simulator: aiops-test
spec:
  replicas: 2
  selector:
    matchLabels:
      app: slowapp-demo
  template:
    metadata:
      labels:
        app: slowapp-demo
    spec:
      containers:
      - name: slowapp
        image: python:3.11-slim
        command:
        - python
        - -c
        - |
          from http.server import HTTPServer, BaseHTTPRequestHandler
          import time
          import random
          class Handler(BaseHTTPRequestHandler):
              def do_GET(self):
                  if random.random() < 0.3:
                      self.send_response(500)
                      self.end_headers()
                      self.wfile.write(b'Error')
                  else:
                      time.sleep(random.uniform(1, 5))
                      self.send_response(200)
                      self.end_headers()
                      self.wfile.write(b'OK')
          HTTPServer(('0.0.0.0', 8080), Handler).serve_forever()
        ports:
        - containerPort: 8080
        resources:
          limits:
            memory: "128Mi"
            cpu: "200m"
---
apiVersion: v1
kind: Service
metadata:
  name: slowapp-demo
  namespace: {namespace}
spec:
  selector:
    app: slowapp-demo
  ports:
  - port: 80
    targetPort: 8080
"""


class IncidentSimulator:
    """Creates test incidents in Kubernetes."""
    
    SCENARIOS = {
        "crashloop": CRASHLOOP_MANIFEST,
        "oom": OOM_MANIFEST,
        "imagepull": IMAGE_PULL_MANIFEST,
        "slowapp": SLOW_APP_MANIFEST,
    }
    
    def __init__(self, kubeconfig: str = None):
        if kubeconfig:
            config.load_kube_config(kubeconfig)
        else:
            try:
                config.load_incluster_config()
            except config.ConfigException:
                config.load_kube_config()
        
        self.apps_v1 = client.AppsV1Api()
        self.core_v1 = client.CoreV1Api()
    
    def create_scenario(self, scenario: str, namespace: str = "default") -> bool:
        """Create a test scenario."""
        if scenario not in self.SCENARIOS:
            logger.error(f"Unknown scenario: {scenario}")
            return False
        
        manifest = self.SCENARIOS[scenario].format(namespace=namespace)
        
        try:
            # Parse and create resources
            import yaml
            
            for doc in yaml.safe_load_all(manifest):
                if doc is None:
                    continue
                
                kind = doc.get("kind")
                
                if kind == "Deployment":
                    try:
                        self.apps_v1.delete_namespaced_deployment(
                            name=doc["metadata"]["name"],
                            namespace=namespace,
                        )
                    except client.ApiException:
                        pass
                    
                    self.apps_v1.create_namespaced_deployment(
                        namespace=namespace,
                        body=doc,
                    )
                    logger.info(f"Created deployment: {doc['metadata']['name']}")
                    
                elif kind == "Service":
                    try:
                        self.core_v1.delete_namespaced_service(
                            name=doc["metadata"]["name"],
                            namespace=namespace,
                        )
                    except client.ApiException:
                        pass
                    
                    self.core_v1.create_namespaced_service(
                        namespace=namespace,
                        body=doc,
                    )
                    logger.info(f"Created service: {doc['metadata']['name']}")
            
            return True
            
        except Exception as e:
            logger.error("Failed to create scenario", error=str(e))
            return False
    
    def cleanup(self, namespace: str = "default") -> None:
        """Clean up all simulator resources."""
        try:
            deployments = self.apps_v1.list_namespaced_deployment(
                namespace=namespace,
                label_selector="simulator=aiops-test",
            )
            
            for deploy in deployments.items:
                self.apps_v1.delete_namespaced_deployment(
                    name=deploy.metadata.name,
                    namespace=namespace,
                )
                logger.info(f"Deleted deployment: {deploy.metadata.name}")
            
            services = self.core_v1.list_namespaced_service(
                namespace=namespace,
                label_selector="simulator=aiops-test",
            )
            
            for svc in services.items:
                self.core_v1.delete_namespaced_service(
                    name=svc.metadata.name,
                    namespace=namespace,
                )
                logger.info(f"Deleted service: {svc.metadata.name}")
                
        except Exception as e:
            logger.error("Cleanup failed", error=str(e))
    
    def list_scenarios(self) -> list[str]:
        """List available scenarios."""
        return list(self.SCENARIOS.keys())


@click.group()
def cli():
    """AIOps Incident Simulator CLI."""
    pass


@cli.command()
@click.option("--scenario", "-s", required=True, help="Scenario to create (crashloop, oom, imagepull, slowapp)")
@click.option("--namespace", "-n", default="default", help="Kubernetes namespace")
@click.option("--kubeconfig", "-k", default=None, help="Path to kubeconfig")
def create(scenario: str, namespace: str, kubeconfig: str):
    """Create a test incident scenario."""
    simulator = IncidentSimulator(kubeconfig)
    
    if scenario == "all":
        for s in simulator.list_scenarios():
            simulator.create_scenario(s, namespace)
    else:
        simulator.create_scenario(scenario, namespace)
    
    click.echo(f"✅ Created scenario: {scenario} in namespace: {namespace}")
    click.echo("Watch for alerts in your monitoring system...")


@cli.command()
@click.option("--namespace", "-n", default="default", help="Kubernetes namespace")
@click.option("--kubeconfig", "-k", default=None, help="Path to kubeconfig")
def cleanup(namespace: str, kubeconfig: str):
    """Clean up all simulator resources."""
    simulator = IncidentSimulator(kubeconfig)
    simulator.cleanup(namespace)
    click.echo(f"✅ Cleaned up simulator resources in {namespace}")


@cli.command("list")
def list_scenarios():
    """List available test scenarios."""
    simulator = IncidentSimulator()
    click.echo("Available scenarios:")
    for s in simulator.list_scenarios():
        click.echo(f"  - {s}")


if __name__ == "__main__":
    cli()
