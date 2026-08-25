"""
Architecture diagram for the aws-ecs ECS Fargate CI/CD lab.

Reflects the actual deployed system:
  - Two GitHub repos (aws-ecs-infra, aws-ecs-app), each with their own
    OIDC-authenticated GitHub Actions workflow.
  - CloudFormation nested stacks (network/ecr/ecs/pipeline) deployed via
    Git sync, packaged through a shared S3 bucket.
  - A 2-AZ VPC with public subnets (ALB) and private subnets (ECS Fargate
    tasks + VPC interface/gateway endpoints, no NAT Gateway).
  - EventBridge -> CodePipeline -> CodeDeploy blue/green onto ECS.

Kept only as a "before" reference - architecture_diagram_v2.py (light
theme, legend, generous spacing, IAM roles placed next to what they
protect) is the one actually used as the deliverable.

Run:
    pip install diagrams
    sudo apt install graphviz   # or brew install graphviz on macOS
    python3 architecture_diagram.py
Produces architecture_diagram.png in the same directory.
"""

from diagrams import Cluster, Diagram, Edge
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
    ElbApplicationLoadBalancer,
    Endpoint,
    RouteTable,
)
from diagrams.aws.security import IdentityAndAccessManagementIamRole as IAMRole
from diagrams.aws.storage import S3
from diagrams.aws.general import Users
from diagrams.onprem.vcs import Github
from diagrams.onprem.ci import GithubActions

GRAPH_ATTR = {
    "fontsize": "24",
    "fontname": "Helvetica-Bold",
    "bgcolor": "#0f1620",
    "pad": "0.6",
    "splines": "spline",
    "nodesep": "0.55",
    "ranksep": "0.85",
    "compound": "true",
}

NODE_ATTR = {
    "fontname": "Helvetica",
    "fontsize": "12",
    "fontcolor": "#e8edf4",
}

EDGE_ATTR = {
    "fontname": "Helvetica",
    "fontsize": "11",
}

# Edge "languages" used throughout, kept visually distinct on purpose:
#  - REQUEST  (white/solid)  : real user/application traffic
#  - CI_FLOW  (blue/dashed)  : code -> build -> deploy pipeline flow
#  - IAM_TRUST(orange/dotted): who is allowed to assume/act as whom
#  - GITSYNC  (green/dashed) : CloudFormation Git sync deploying infra
REQUEST = {"color": "#ffffff", "penwidth": "2"}
CI_FLOW = {"color": "#5aa7ff", "style": "dashed", "penwidth": "2"}
IAM_TRUST = {"color": "#ffb454", "style": "dotted", "penwidth": "1.6"}
GITSYNC = {"color": "#5CE65C", "style": "dashed", "penwidth": "2"}

CLUSTER_GITHUB = {"bgcolor": "#161b22", "pencolor": "#5aa7ff", "fontcolor": "#5aa7ff", "fontsize": "16", "style": "rounded"}
CLUSTER_AWS = {"bgcolor": "#12202e", "pencolor": "#ff9900", "fontcolor": "#ff9900", "fontsize": "18", "style": "rounded"}
CLUSTER_VPC = {"bgcolor": "#152a3d", "pencolor": "#59c9ff", "fontcolor": "#59c9ff", "fontsize": "15", "style": "rounded"}
CLUSTER_AZ = {"bgcolor": "#1b3549", "pencolor": "#8fd9ff", "fontcolor": "#8fd9ff", "fontsize": "13", "style": "rounded,dashed"}
CLUSTER_SUB = {"bgcolor": "#20415a", "pencolor": "#c8ecff", "fontcolor": "#c8ecff", "fontsize": "12", "style": "rounded"}
CLUSTER_CICD = {"bgcolor": "#1e1430", "pencolor": "#b892ff", "fontcolor": "#b892ff", "fontsize": "15", "style": "rounded"}
CLUSTER_IAC = {"bgcolor": "#142616", "pencolor": "#5CE65C", "fontcolor": "#5CE65C", "fontsize": "15", "style": "rounded"}


with Diagram(
    "aws-ecs: ECS Fargate Blue/Green CI/CD",
    filename="architecture_diagram",
    show=False,
    direction="TB",
    graph_attr=GRAPH_ATTR,
    node_attr=NODE_ATTR,
    edge_attr=EDGE_ATTR,
):
    developer = Users("Developer\n(arnold-mataba)")

    with Cluster("GitHub", graph_attr=CLUSTER_GITHUB):
        with Cluster("aws-ecs-infra repo", graph_attr=CLUSTER_SUB):
            infra_repo = Github("CloudFormation\ntemplates")
            infra_ci = GithubActions("package-templates.yml\n(OIDC)")

        with Cluster("aws-ecs-app repo", graph_attr=CLUSTER_SUB):
            app_repo = Github("FastAPI app +\nappspec/taskdef")
            app_ci = GithubActions("build-and-push.yml\n(OIDC, semver)")

    developer >> Edge(**CI_FLOW, label="git push") >> infra_repo
    developer >> Edge(**CI_FLOW, label="git push") >> app_repo
    infra_repo >> Edge(**CI_FLOW) >> infra_ci
    app_repo >> Edge(**CI_FLOW) >> app_ci

    with Cluster("AWS Account 107737161507 (us-east-1)", graph_attr=CLUSTER_AWS):
        oidc_role_pkg = IAMRole("github-actions-\ncfn-package-aws-ecs")
        oidc_role_ecr = IAMRole("github-actions-\necr-push-aws-ecs-app")

        infra_ci >> Edge(**IAM_TRUST, label="AssumeRoleWithWebIdentity") >> oidc_role_pkg
        app_ci >> Edge(**IAM_TRUST, label="AssumeRoleWithWebIdentity") >> oidc_role_ecr

        artifact_bucket = S3("arnold-bucket-dev\n(templates + pipeline artifacts)")
        oidc_role_pkg >> Edge(**CI_FLOW, label="cfn package\n(upload templates)") >> artifact_bucket

        with Cluster("Infrastructure as Code", graph_attr=CLUSTER_IAC):
            cfn_gitsync = Cloudformation("CloudFormation\nGit sync")
            cfn_nested = Cloudformation("Nested stack:\nnetwork / ecr / ecs / pipeline")

        infra_ci >> Edge(**GITSYNC, label="commits packaged\nroot template") >> cfn_gitsync
        cfn_gitsync >> Edge(**GITSYNC, label="deploys") >> cfn_nested
        artifact_bucket >> Edge(**GITSYNC, label="TemplateURL") >> cfn_gitsync

        ecr = ECR("aws-ecs-app\n(IMMUTABLE tags)")
        app_ci >> Edge(**CI_FLOW, label="docker push :semver") >> ecr

        with Cluster("VPC 10.0.0.0/16", graph_attr=CLUSTER_VPC):
            igw = InternetGateway("Internet\nGateway")
            alb = ElbApplicationLoadBalancer("Public ALB\n(:80)")
            igw >> Edge(**REQUEST) >> alb

            with Cluster("Availability Zone A", graph_attr=CLUSTER_AZ):
                pub_a = Endpoint("public-a\n10.0.0.0/24")
                with Cluster("private-a  10.0.10.0/24", graph_attr=CLUSTER_SUB):
                    task_a = Fargate("ECS task\n(app:8000)")
                    ep_a = Endpoint("VPC Endpoints\necr.api / ecr.dkr / logs")

            with Cluster("Availability Zone B", graph_attr=CLUSTER_AZ):
                pub_b = Endpoint("public-b\n10.0.1.0/24")
                with Cluster("private-b  10.0.11.0/24", graph_attr=CLUSTER_SUB):
                    task_b_endpoints = Endpoint("VPC Endpoints\necr.api / ecr.dkr / logs")

            alb >> Edge(**REQUEST) >> pub_a
            alb >> Edge(**REQUEST) >> pub_b
            alb >> Edge(**REQUEST, label="blue/green\ntarget groups") >> task_a

            private_rt = RouteTable("private route table\n(no NAT - S3 gateway\nendpoint only)")
            task_a >> Edge(color="#8fd9ff", style="dotted", label="pull image / ship logs") >> ep_a
            private_rt >> Edge(color="#8fd9ff", style="dotted") >> ep_a

        ecs_cluster = ECS("aws-ecs-cluster\n(Fargate service)")
        ecs_cluster >> Edge(**REQUEST) >> task_a

        task_exec_role = IAMRole("aws-ecs-app-\ntask-execution-role")
        task_exec_role >> Edge(**IAM_TRUST, label="pull from ECR,\nwrite logs") >> task_a

        with Cluster("Blue/Green Deployment Pipeline", graph_attr=CLUSTER_CICD):
            eventbridge = Eventbridge("EventBridge rule\n(ECR PUSH event)")
            pipeline = Codepipeline("CodePipeline\n(Source: GitHub)")
            codedeploy = Codedeploy("CodeDeploy\n(ECS blue/green)")

            eventbridge >> Edge(**CI_FLOW, label="StartPipelineExecution") >> pipeline
            pipeline >> Edge(**CI_FLOW, label="appspec + taskdef +\nimageDetail.json") >> codedeploy

        ecr >> Edge(**CI_FLOW, label="PUSH event") >> eventbridge
        app_repo >> Edge(**CI_FLOW, label="pulls appspec.yaml /\ntaskdef.json / imageDetail.json") >> pipeline
        codedeploy >> Edge(**CI_FLOW, label="register new task def,\nshift ALB traffic") >> ecs_cluster

        with Cluster("Observability & Scaling", graph_attr=CLUSTER_SUB):
            logs = CloudwatchLogs("/ecs/aws-ecs-app")
            alarm = CloudwatchAlarm("CPU > 50%\n(3x 1-min periods)")
            autoscaling = AutoScaling("Application Auto Scaling\nmin 1 / max 4")

            task_a >> Edge(color="#c8ecff") >> logs
            Cloudwatch("CPUUtilization") >> Edge(color="#c8ecff") >> alarm
            alarm >> Edge(color="#c8ecff", label="scale out/in") >> autoscaling
            autoscaling >> Edge(color="#c8ecff") >> ecs_cluster

print("Diagram rendered: architecture_diagram.png")
