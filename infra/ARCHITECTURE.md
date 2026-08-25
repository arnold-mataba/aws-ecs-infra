# aws-ecs Architecture Walkthrough

This explains the system exactly as drawn in `infra/architecture_diagram_v2.png`, in the same
order top to bottom. The diagram uses five distinct edge styles (see its legend); this document
follows the same five "flows" so you can point at a line on the diagram while you talk through it.

**The five flows:**
- **Solid black** — real traffic: a user's request, or one AWS service directly driving another.
- **Blue dashed** — the CI/CD pipeline: code becomes an image, becomes a deployment.
- **Green dashed** — CloudFormation Git sync: infrastructure changes flowing from a repo into live AWS resources.
- **Orange dotted** — IAM trust: who is allowed to assume or act as which role.
- **Pink dotted** — observability: metrics and logs feeding back into scaling decisions.

---

## 1. Two repositories, two independent pipelines

Everything starts with a deliberate split: **`aws-ecs-infra`** holds only CloudFormation
templates; **`aws-ecs-app`** holds only the FastAPI application, its Dockerfile, and the
deployment artifacts (`appspec.yaml`, `taskdef.json`). Each repo has its own GitHub Actions
workflow, and each workflow authenticates to AWS independently via **OIDC** — there are no
long-lived AWS credentials stored in GitHub at all. A workflow requests a short-lived identity
token from GitHub, AWS verifies it against a registered OIDC provider, and a trust policy scoped
to that exact repository and branch decides whether to hand back temporary credentials.

## 2. Infrastructure delivery — CloudFormation Git sync

When something changes under `templates/`, the infra repo's workflow (`package-templates.yml`)
assumes a narrowly-scoped **packaging role** and runs `aws cloudformation package`, which uploads
the four nested-stack templates (network, ECR, ECS, pipeline) to a shared S3 bucket and rewrites a
root template's `TemplateURL` fields to point at them. That packaged root template gets committed
back into the repo. **CloudFormation Git sync** is watching that exact file — the moment it
changes, CloudFormation deploys it as a single nested stack. This is the only path infrastructure
ever takes into the account: nobody runs `aws cloudformation deploy` by hand.

## 3. Application delivery — build, tag, push

When app code changes, the app repo's workflow (`build-and-push.yml`) assumes a *different*,
equally narrow role — one that can only push to a single named ECR repository. It computes the
next version from conventional-commit prefixes in the git log (`feat:` → minor, `fix:` → patch,
`!`/`BREAKING CHANGE:` → major), builds the Docker image tagged with that exact version, and
pushes it. The ECR repository has **immutable tagging** turned on, so that version can never be
silently overwritten later — a rubric requirement, but also just good practice: whatever tag a
running task references is guaranteed to still mean the same image next year. The workflow then
writes `imageDetail.json` (the exact pushed image URI) and commits it alongside `appspec.yaml`/
`taskdef.json`, and tags the release commit `vX.Y.Z` in git.

## 4. The network the application actually runs in

The VPC is `10.0.0.0/16` across two Availability Zones, each with a public and a private subnet
(`10.0.0.0/24` / `10.0.10.0/24` in AZ A, `10.0.1.0/24` / `10.0.11.0/24` in AZ B). The public
subnets exist for exactly one thing: the Application Load Balancer, reachable on plain HTTP (no
ACM certificate/domain in scope for this lab). ECS tasks live only in the private subnets — they
have no public IP and no route to the internet at all. **There is no NAT Gateway.** Instead, three
VPC interface endpoints (`ecr.api`, `ecr.dkr`, `logs`) and one S3 gateway endpoint let tasks pull
their image and ship logs without ever leaving AWS's network. This was a deliberate cost and
security choice: a NAT Gateway would have been a recurring cost and an unnecessary internet
egress path for a workload that has no legitimate reason to call anything outside AWS.

Security groups chain hop by hop: the **ALB** accepts inbound 80 from anywhere; the **task**
security group only accepts traffic from the ALB's security group, on the container port; the
**VPC endpoint** security group only accepts traffic from the task's security group, on 443.
Nothing has a wide-open rule.

## 5. The deployment pipeline — EventBridge, CodePipeline, CodeDeploy

This is the part of the diagram with the most moving pieces, so it's worth walking slowly:

1. **ECR emits a `PUSH` event** the instant the app repo's workflow finishes pushing a new tag.
2. An **EventBridge rule**, filtered to exactly that repository and a successful push, triggers
   `codepipeline:StartPipelineExecution` — this is the literal "EventBridge detects a new image
   and triggers CodePipeline" requirement, built explicitly rather than relying on a console
   wizard's hidden default.
3. **CodePipeline** has a single source action pulling from the app repo's GitHub connection. It
   picks up `appspec.yaml`, `taskdef.json`, and `imageDetail.json` together — deliberately *not*
   using CodePipeline's built-in "ECR source" action, because that action always looks for a tag
   literally named `:latest`, which can never exist in an immutable-tag repository.
4. CodePipeline registers a **new ECS task definition revision**, substituting the real image URI
   from `imageDetail.json` into the placeholder inside `taskdef.json`.
5. It hands off to **CodeDeploy**, which runs the actual blue/green swap: it starts a new task set
   against the *green* target group, waits for it to pass ALB health checks, flips the ALB
   listener from blue to green, and — after a five-minute bake period — terminates the old blue
   task set. If anything fails partway through, CodeDeploy rolls back automatically.

## 6. Observability and autoscaling

The running task ships its logs to a CloudWatch Logs group and its CPU utilization to CloudWatch
Metrics. A target-tracking scaling policy watches that CPU metric against a 50% threshold — sustain
above it for three consecutive one-minute periods and Application Auto Scaling adds a task (up to a
maximum of 4); sustain below it and it removes one (down to a minimum of 1). This was verified
live during the build, not just configured: a sustained load test genuinely triggered a scale-out
from 1 to 2 tasks, and scale-in back to 1 once the load stopped.

---

**The one-sentence version, if you only have thirty seconds:** every push to either repository
flows through its own OIDC-authenticated pipeline — one deploys infrastructure via CloudFormation
Git sync, the other builds and immutably tags a container image — and a new image landing in ECR
is what actually triggers EventBridge to kick off a zero-downtime blue/green deployment onto an
autoscaling ECS Fargate service that has no direct exposure to the internet at all.
