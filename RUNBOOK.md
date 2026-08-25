# Spin down / spin up runbook

## Why this exists

The app stack (`aws-ecs-app-infra` — VPC, ALB, ECS, VPC endpoints, CodePipeline/CodeDeploy) is
the only thing here with an ongoing hourly cost (ALB + 6 interface-endpoint-hours + the Fargate
task). Everything else — both GitHub repos, the `aws-ecs-bootstrap` stack, the shared IAM roles
(`iam-lab-gitsync-*`, `github-actions-cfn-package-aws-ecs`), and the S3 bucket — costs nothing
meaningful sitting idle, so none of that gets torn down between sessions.

## Spin down (what was done on 2026-08-18)

1. Emptied the ECR repo first (`aws ecr batch-delete-image` for every image digest) — **not**
   `EmptyOnDelete: true` on the template, since that would let a future *accidental* rollback
   silently wipe image history too. Emptying manually only affects this deliberate teardown.
2. `aws cloudformation delete-stack --stack-name aws-ecs-app-infra` — tears down all four nested
   children (network, ecr, ecs, pipeline) in one shot.
3. Left the ECR *repository* resource itself alone (just emptied) so CloudFormation still deletes
   a clean, empty, CloudFormation-tracked resource rather than hitting the "orphaned resource"
   problem we fought earlier in the week.

## IMPORTANT: clear the GitSync sync blocker first (learned the hard way on 2026-08-25)

Deleting the stack directly via `aws cloudformation delete-stack` (rather than through a "delete
sync configuration" flow in the console) makes GitSync treat that deletion as "something besides
StackSync modified my target" and it **automatically creates a sync blocker that disables syncing
indefinitely** — it will not self-heal, and pushing more commits does nothing until the blocker is
cleared. The console gives no way to see this once the stack is gone (you normally reach Git sync
*through* a stack's page), but the `codeconnections` CLI can see and fix it directly:

```
# 1. Find the repo's link ID
aws codeconnections list-repository-links
# -> note the RepositoryLinkId for aws-ecs-infra

# 2. Confirm the sync configuration still exists and see its ConfigFile
aws codeconnections list-sync-configurations \
  --repository-link-id <link-id> --sync-type CFN_STACK_SYNC

# 3. Check for an active blocker
aws codeconnections get-sync-blocker-summary \
  --sync-type CFN_STACK_SYNC --resource-name aws-ecs-app-infra

# 4. Resolve it (grab the Id from step 3's output)
aws codeconnections update-sync-blocker \
  --id <blocker-id> --sync-type CFN_STACK_SYNC --resource-name aws-ecs-app-infra \
  --resolved-reason "Stack deleted deliberately for a planned spin-down; redeploying fresh."
```

Only after this does a new commit (or the existing latest commit) actually get deployed. Do this
**immediately after every deliberate stack deletion**, before waiting on anything else.

## Spin up

1. **Check the stack is actually gone**: `aws cloudformation describe-stacks --stack-name
   aws-ecs-app-infra` should return a "does not exist" error. If it's stuck in
   `DELETE_FAILED`, check which resource failed first before retrying.
2. **Clear the sync blocker** — see the section above. Do this before anything else; without it,
   nothing will happen no matter how many commits you push.
3. **Force a fresh GitSync deploy** by pushing a trivial change to `templates/root.yaml` (e.g.
   touch the description comment), which triggers the packaging workflow and a fresh sync attempt:
   ```
   cd aws-ecs-infra
   git pull
   # make a 1-line no-op edit to templates/root.yaml
   git add templates/root.yaml
   git commit -m "chore: redeploy after weekly spin-down"
   git push
   ```
4. **Watch it deploy**: `aws cloudformation describe-stacks --stack-name aws-ecs-app-infra
   --query 'Stacks[0].StackStatus'`. Expect `CREATE_IN_PROGRESS` → `CREATE_COMPLETE`. All the
   IAM permission gaps found during initial development (`ecr:TagResource`,
   `elasticloadbalancing:DescribeTargetGroupAttributes`, `codestar-connections:PassConnection`,
   `ecs:RegisterTaskDefinition` + matching `iam:PassRole`) are already baked into the shared
   execution role, so this should go smoothly without repeating that debugging.
5. **The ECS service will get stuck retrying** with `CannotPullContainerError` — this is
   expected, not a bug. The freshly-recreated ECR repo is empty, and the task definition
   references a specific tag (`1.0.0` by default) that doesn't exist yet.
6. **Delete all existing git tags in `aws-ecs-app`** (`v1.0.0`, `v1.1.x`, ...) before pushing —
   they're stale once ECR is wiped, and the version-bump script needs to bootstrap back to
   `1.0.0` (matching the task definition's default `InitialImageTag`), not continue from wherever
   it left off last time:
   ```
   cd aws-ecs-app
   for t in $(git tag); do git tag -d "$t"; git push origin ":refs/tags/$t"; done
   ```
7. **Push any commit to `aws-ecs-app`** to trigger `build-and-push.yml` and get a real image
   into ECR. If there's no real code change to make, a trivial one works fine. Within a few
   minutes of the image landing, the stuck ECS service will retry successfully and stabilize.
8. **Get the new ALB DNS name** (it changes every time the ALB is recreated):
   ```
   aws cloudformation describe-stacks --stack-name aws-ecs-app-infra \
     --query 'Stacks[0].Outputs'
   ```
9. **Confirm end-to-end**: `curl http://<new-alb-dns>/version` and `/health`.

Total expected time: network + ECR + ECS + pipeline stack creation historically took under
10 minutes once the permission fixes were in place; add a couple more minutes for the image
push and the ECS service's first successful retry cycle.
