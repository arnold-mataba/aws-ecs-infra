# End-to-end deep dive: deploy flow, autoscaling, and failure handling

This traces one continuous story through every resource in this project, in exact
chronological order, naming the real config values at each step. Companion to
`ARCHITECTURE.md` (which explains *what* each piece is) — this explains *what actually
happens, in order*, when a change ships, when load spikes, and when something fails.

## Part 1 — A code change, from `git push` to serving live traffic

1. **Developer commits and pushes** to `aws-ecs-app`'s `main` branch, e.g. `git commit -m
   "feat: ..."` — the commit prefix matters, it's read later.
2. **GitHub fires `build-and-push.yml`** (`on: push: branches: [main]`, excluding pushes
   that only touch `imageDetail.json` — avoids a self-trigger loop).
3. **Version is computed** from every commit since the last `v*` tag; highest-severity
   prefix among them wins (`feat:` → minor, `fix:` → patch, `!`/`BREAKING CHANGE:` → major).
4. **OIDC exchange**: the job requests a short-lived token from GitHub's OIDC issuer
   (needs `permissions: id-token: write`), STS checks it against
   `github-actions-ecr-push-aws-ecs-app`'s trust policy (`aud`, `sub`, `repository_id`,
   `repository_owner_id` must all match this exact repo/branch), hands back temporary
   credentials. No stored secret involved anywhere.
5. **Image built** with `--build-arg APP_VERSION=<version>` — same value becomes both the
   app's own `/version` response and the ECR tag.
6. **Image pushed to ECR** — gated by two independent checks: the IAM policy on the
   assumed role (scoped to this one repo ARN) and the repo's own `RepositoryPolicyText`
   (only this role's ARN may push). `ImageTagMutability: IMMUTABLE` guarantees this exact
   tag has never existed before, or the push is rejected outright.
7. **The push is the real trigger**: ECR emits a `PUSH`/`SUCCESS` event on EventBridge;
   `EcrPushEventRule` matches it, assumes `EventBridgeInvokePipelineRole`, calls
   `codepipeline:StartPipelineExecution`.
8. **Same workflow run, next step**: writes `imageDetail.json` with the exact pushed URI,
   commits, tags `v<version>`, pushes both (with fetch/rebase retry on conflict).
9. **Known race, self-correcting in practice**: the EventBridge-triggered execution (step 7)
   could in principle start before step 8's commit lands. `CodeStarSourceConnection`
   source actions also auto-trigger independently on any push to their watched branch, so
   the `imageDetail.json` commit itself fires a second execution guaranteed to see the
   correct file — the later execution's result is what lands (observed directly during
   testing: multiple `Status: Superseded` executions, final one always correct). This is
   inference from observed behavior, not confirmed against AWS internals byte-for-byte —
   say so plainly if asked rather than overclaiming certainty.
10. **`Source` stage** pulls current `main` of `aws-ecs-app`, zips it, uploads to the S3
    artifact store as `SourceConfig`.
11. **`Deploy` stage** reads `taskdef.json`/`appspec.yaml`/image URI out of that package;
    CodePipeline itself calls `ecs:RegisterTaskDefinition` — a new revision now exists
    with the image field resolved.
12. **CodeDeploy creates the "green" task set** from that revision, targeting
    `GreenTargetGroup`. Real Fargate provisioning: pull image via the `ecr.api`/`ecr.dkr`/
    `s3` VPC endpoints (no NAT), start container, ECS's own `HealthCheck` must pass
    (`StartPeriod: 30`, `Retries: 3`).
13. **ALB health-checks green independently** — `/health`, every 30s, needs **2
    consecutive passes**. Separate system from ECS's own check.
14. **Traffic cuts over**: `CodeDeployDefault.ECSAllAtOnce` — the instant green is
    healthy, `AlbListener`'s default action flips from blue to green target group, all at
    once. This is the exact moment a live `/version` poll flips values with zero dropped
    requests.
15. **Bake time**: blue's task set stays alive, idle, 5 minutes
    (`TerminationWaitTimeInMinutes: 5`) as an instant-rollback option, then terminates.
    Green now plays blue's role (standby) next cycle.

## Part 2 — High demand (autoscaling)

1. Load arrives, task CPU climbs. ECS continuously reports `CPUUtilization` to
   CloudWatch regardless of whether anyone's watching.
2. The target-tracking policy is actually two auto-created CloudWatch alarms under the
   hood (real names from this account: `TargetTracking-service/aws-ecs-cluster/
   aws-ecs-app-AlarmHigh-...` / `...-AlarmLow-...`). High alarm needs CPU above
   `TargetValue: 50` for **3 consecutive 1-minute periods** — deliberately requires
   sustained breach; verified live that a 2-minute spike did *not* trigger it, a 4-minute
   one did.
3. Alarm firing invokes the scaling policy → Application Auto Scaling changes
   `DesiredCount` (e.g. 1 → 2). `ScaleOutCooldown: 60` blocks another decision for 60s.
4. ECS reconciles `DesiredCount` vs `RunningCount` by launching a new task through the
   *identical* provisioning path as any task (pull via endpoints, health-check, register
   into whichever target group is currently live — not necessarily "blue"). Once ALB
   health-checks it too, it starts receiving a share of real traffic automatically.
5. Scale-in is the same mechanism in reverse via the Low alarm and `MinCapacity: 1` —
   excess tasks deregistered (respecting `deregistration_delay: 30s` for in-flight
   requests) before termination.

## Part 3 — Task failure

Two independent watchers — genuinely separate systems, not redundant copies of the same
check:
- **ECS's own container-level `HealthCheck`** — the ECS agent itself execs the Python
  one-liner inside the container.
- **The ALB target group's health check** — the load balancer hitting `/health` over the
  network, completely separately.

- **ECS check fails 3x in a row** (past the 30s `StartPeriod` grace): ECS stops the task
  directly. The Service notices `RunningCount < DesiredCount` and starts a replacement
  through the normal provisioning path.
- **Only the ALB check fails** (task alive but not responding correctly): ALB just stops
  routing to it, doesn't kill anything. If ECS's check doesn't also fail, you can end up
  with a task that's alive but receiving zero traffic — a real, narrow gap between the two
  systems' views, not something to assume is always in lockstep.
- **Failure during an active blue/green deployment**: `AutoRollbackConfiguration:
  Enabled: true, Events: [DEPLOYMENT_FAILURE]` — CodeDeploy never flips the listener,
  terminates the broken green task set, blue (never touched) keeps serving. Production
  never sees the broken version.
- **Honest single point of failure at baseline**: with `MinCapacity: 1` / `DesiredCount:
  1`, there is exactly one task in steady state. If it fails, there's a real (if usually
  under a minute, given Fargate provisioning speed) window with zero healthy backends
  before the replacement passes both health checks. `MaxCapacity: 4` reduces the odds this
  matters under real load, but at idle baseline it's a genuine gap worth naming plainly if
  asked, not glossing over.
