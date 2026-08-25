"""
Architecture diagram (v2) for the aws-ecs ECS Fargate CI/CD lab.

Same real system as architecture_diagram.py, restyled for clarity:
  - Light theme, AWS-style category coloring (orange=account boundary,
    blue=network, green=IaC, purple=CI/CD, pink=observability).
  - IAM roles placed next to what they actually protect, not floating.
  - No default AWS icon whose baked-in artwork is misleading here (e.g.
    the stock RouteTable icon shows a fictitious 172.16.x.x example that
    has nothing to do with our 10.0.0.0/16 VPC) - facts are conveyed as
    plain edge/node labels instead.
  - A legend cluster (top-left) spells out what each edge color/style means.

Run:
    python3 -m venv .venv && source .venv/bin/activate
    pip install diagrams
    sudo apt install graphviz   # or brew install graphviz on macOS
    python3 architecture_diagram_v2.py
Produces architecture_diagram_v2.png in the same directory.
"""

from diagrams import Cluster, Diagram, Edge
from diagrams.generic.blank import Blank
from diagrams.aws.compute import ECS, ECR, Fargate
from diagrams.aws.integration import Eventbridge
from diagrams.aws.devtools import Codepipeline, Codedeploy
from diagrams.aws.management import (
    Cloudformation,
    Cloudwatch,
    CloudwatchAlarm,
    CloudwatchLogs,
    AutoScaling,
)
from diagrams.aws.network import (
    InternetGateway,
    PublicSubnet,
    PrivateSubnet,
    ElbApplicationLoadBalancer,
    Endpoint,
)
from diagrams.aws.security import IdentityAndAccessManagementIamRole as IAMRole
from diagrams.aws.storage import S3
from diagrams.aws.general import Users
from diagrams.onprem.vcs import Github
from diagrams.onprem.ci import GithubActions

GRAPH_ATTR = {
    "fontsize": "26",
    "fontname": "Helvetica-Bold",
    "fontcolor": "#1b1f24",
    "bgcolor": "#f7f9fb",
    "pad": "0.8",
    "splines": "spline",
    "nodesep": "0.85",
    "ranksep": "1.05",
    "concentrate": "false",
}

NODE_ATTR = {
    "fontname": "Helvetica",
    "fontsize": "12",
    "fontcolor": "#1b1f24",
}

EDGE_ATTR = {
    "fontname": "Helvetica",
    "fontsize": "10",
    "fontcolor": "#33393f",
}

# Distinct "languages" of edges, kept visually separate on purpose:
REQUEST = {"color": "#1b1f24", "penwidth": "2.2"}                       # real user/app traffic
CI_FLOW = {"color": "#0969da", "style": "dashed", "penwidth": "1.8"}    # build -> push -> deploy
IAM_TRUST = {"color": "#9a6700", "style": "dotted", "penwidth": "1.6"}  # who may assume/act as whom
GITSYNC = {"color": "#1a7f37", "style": "dashed", "penwidth": "1.8"}    # CloudFormation Git sync
OBSERVE = {"color": "#bf3989", "style": "dotted", "penwidth": "1.6"}    # metrics/logs/scaling

CLUSTER_AWS = {"bgcolor": "#fff8f0", "pencolor": "#ff9900", "fontcolor": "#c76b00", "fontsize": "17", "style": "rounded", "penwidth": "2.2", "labeljust": "l", "margin": "32"}
CLUSTER_GITHUB = {"bgcolor": "#f6f8fa", "pencolor": "#24292f", "fontcolor": "#24292f", "fontsize": "15", "style": "rounded", "penwidth": "1.6", "margin": "26"}
CLUSTER_REPO = {"bgcolor": "#ffffff", "pencolor": "#8c959f", "fontcolor": "#24292f", "fontsize": "12", "style": "rounded", "margin": "22"}
CLUSTER_IAC = {"bgcolor": "#eafbea", "pencolor": "#1a7f37", "fontcolor": "#1a7f37", "fontsize": "14", "style": "rounded", "penwidth": "1.8", "margin": "26"}
CLUSTER_CICD = {"bgcolor": "#f5f0ff", "pencolor": "#8250df", "fontcolor": "#8250df", "fontsize": "14", "style": "rounded", "penwidth": "1.8", "margin": "26"}
CLUSTER_VPC = {"bgcolor": "#eef6ff", "pencolor": "#0969da", "fontcolor": "#0969da", "fontsize": "15", "style": "rounded", "penwidth": "2", "margin": "28"}
CLUSTER_AZ = {"bgcolor": "#ffffff", "pencolor": "#54aeff", "fontcolor": "#0969da", "fontsize": "12", "style": "rounded,dashed", "penwidth": "1.4", "margin": "22"}
CLUSTER_PUB = {"bgcolor": "#eafbea", "pencolor": "#1a7f37", "fontcolor": "#1a7f37", "fontsize": "11", "style": "rounded", "margin": "18"}
CLUSTER_PRIV = {"bgcolor": "#fff0f0", "pencolor": "#cf222e", "fontcolor": "#cf222e", "fontsize": "11", "style": "rounded", "margin": "18"}
CLUSTER_OBS = {"bgcolor": "#fff0f6", "pencolor": "#bf3989", "fontcolor": "#bf3989", "fontsize": "14", "style": "rounded", "penwidth": "1.8", "margin": "26"}
CLUSTER_LEGEND = {"bgcolor": "#ffffff", "pencolor": "#8c959f", "fontcolor": "#1b1f24", "fontsize": "14", "style": "rounded", "penwidth": "1.4", "margin": "24"}


with Diagram(
    "aws-ecs -- ECS Fargate Blue Green CI CD v2",
    filename="architecture_diagram_v2",
    show=False,
    direction="TB",
    graph_attr=GRAPH_ATTR,
    node_attr=NODE_ATTR,
    edge_attr=EDGE_ATTR,
):
    with Cluster("Legend: edge meaning", graph_attr=CLUSTER_LEGEND):
        l1a, l1b = Blank(""), Blank("")
        l2a, l2b = Blank(""), Blank("")
        l3a, l3b = Blank(""), Blank("")
        l4a, l4b = Blank(""), Blank("")
        l5a, l5b = Blank(""), Blank("")
        l1a >> Edge(**REQUEST, label="real user / app traffic") >> l1b
        l2a >> Edge(**CI_FLOW, label="build -> push -> deploy pipeline flow") >> l2b
        l3a >> Edge(**GITSYNC, label="CloudFormation Git sync") >> l3b
        l4a >> Edge(**IAM_TRUST, label="IAM trust (who may assume/act as whom)") >> l4b
        l5a >> Edge(**OBSERVE, label="metrics / logs / autoscaling") >> l5b

    developer = Users("Developer\n(arnold-mataba)")

    with Cluster("GitHub", graph_attr=CLUSTER_GITHUB):
        with Cluster("aws-ecs-infra", graph_attr=CLUSTER_REPO):
            infra_repo = Github("CloudFormation\ntemplates")
            infra_ci = GithubActions("package-\ntemplates.yml")

        with Cluster("aws-ecs-app", graph_attr=CLUSTER_REPO):
            app_repo = Github("FastAPI app,\nappspec/taskdef")
            app_ci = GithubActions("build-and-\npush.yml")

    developer >> Edge(**REQUEST, label="git push") >> infra_repo
    developer >> Edge(**REQUEST, label="git push") >> app_repo
    infra_repo >> Edge(**CI_FLOW) >> infra_ci
    app_repo >> Edge(**CI_FLOW) >> app_ci

    with Cluster("AWS Account 107737161507 (us-east-1)", graph_attr=CLUSTER_AWS):

        artifact_bucket = S3("arnold-bucket-dev\n(shared: templates +\npipeline artifacts)")

        with Cluster("Infrastructure as Code", graph_attr=CLUSTER_IAC):
            pkg_role = IAMRole("cfn-package\nrole (OIDC)")
            cfn_gitsync = Cloudformation("Git sync")
            cfn_nested = Cloudformation("Nested stack\nnetwork / ecr / ecs / pipeline")
            pkg_role >> Edge(color="#1a7f37") >> cfn_gitsync
            cfn_gitsync >> Edge(**GITSYNC, label="deploys") >> cfn_nested

        infra_ci >> Edge(**IAM_TRUST, label="AssumeRoleWithWebIdentity") >> pkg_role
        infra_ci >> Edge(**GITSYNC, label="cfn package") >> artifact_bucket
        artifact_bucket >> Edge(**GITSYNC, label="TemplateURL") >> cfn_gitsync

        with Cluster("ECR", graph_attr=CLUSTER_REPO):
            ecr_role = IAMRole("ecr-push\nrole (OIDC)")
            ecr = ECR("aws-ecs-app\n(IMMUTABLE tags)")
            ecr_role >> Edge(color="#0969da") >> ecr

        app_ci >> Edge(**IAM_TRUST, label="AssumeRoleWithWebIdentity") >> ecr_role
        app_ci >> Edge(**CI_FLOW, label="docker push :semver") >> ecr

        with Cluster("Blue/Green Deployment Pipeline", graph_attr=CLUSTER_CICD):
            eventbridge = Eventbridge("EventBridge rule\n(ECR PUSH event)")
            pipeline = Codepipeline("CodePipeline\n(Source: GitHub)")
            codedeploy = Codedeploy("CodeDeploy\n(ECS blue/green)")
            eventbridge >> Edge(**CI_FLOW, label="StartPipelineExecution") >> pipeline
            pipeline >> Edge(**CI_FLOW, label="appspec + taskdef\n+ imageDetail.json") >> codedeploy

        ecr >> Edge(**CI_FLOW, label="PUSH event") >> eventbridge
        app_repo >> Edge(**CI_FLOW, label="source pull") >> pipeline

        with Cluster("VPC  10.0.0.0/16", graph_attr=CLUSTER_VPC):
            igw = InternetGateway("Internet\nGateway")
            alb = ElbApplicationLoadBalancer("Public ALB\nHTTP :80")
            igw >> Edge(**REQUEST) >> alb

            with Cluster("AZ A", graph_attr=CLUSTER_AZ):
                with Cluster("public-a  10.0.0.0/24", graph_attr=CLUSTER_PUB):
                    pub_a = PublicSubnet("ALB ENI")
                with Cluster("private-a  10.0.10.0/24", graph_attr=CLUSTER_PRIV):
                    task_a = Fargate("ECS task\napp :8000")
                    ep_a = Endpoint("VPC Endpoints\necr.api / ecr.dkr / logs")

            with Cluster("AZ B", graph_attr=CLUSTER_AZ):
                with Cluster("public-b  10.0.1.0/24", graph_attr=CLUSTER_PUB):
                    pub_b = PublicSubnet("ALB ENI")
                with Cluster("private-b  10.0.11.0/24", graph_attr=CLUSTER_PRIV):
                    ep_b = Endpoint("VPC Endpoints\necr.api / ecr.dkr / logs")

            alb >> Edge(**REQUEST) >> pub_a
            alb >> Edge(**REQUEST) >> pub_b
            alb >> Edge(**REQUEST, label="blue/green target groups") >> task_a
            task_a >> Edge(color="#0969da", style="dotted", label="pull image, ship logs\n(no NAT - endpoints only)") >> ep_a
            task_exec_role = IAMRole("task-execution-\nrole")
            task_exec_role >> Edge(**IAM_TRUST, label="pull ECR, write logs") >> task_a

        ecs_cluster = ECS("aws-ecs-cluster\n(Fargate service)")
        ecs_cluster >> Edge(**REQUEST) >> task_a
        codedeploy >> Edge(**CI_FLOW, label="register task def,\nshift ALB traffic") >> ecs_cluster

        with Cluster("Observability & Scaling", graph_attr=CLUSTER_OBS):
            logs = CloudwatchLogs("/ecs/aws-ecs-app")
            cw_metric = Cloudwatch("CPUUtilization")
            alarm = CloudwatchAlarm("CPU > 50%\n(3x 1-min periods)")
            autoscaling = AutoScaling("Application Auto Scaling\nmin 1 / max 4")
            cw_metric >> Edge(**OBSERVE) >> alarm
            alarm >> Edge(**OBSERVE, label="scale out/in") >> autoscaling

        task_a >> Edge(**OBSERVE) >> logs
        task_a >> Edge(**OBSERVE) >> cw_metric
        autoscaling >> Edge(**OBSERVE) >> ecs_cluster

print("Diagram rendered: architecture_diagram_v2.png")
