# Spin down / spin up runbook

## Why this exists

The app stack (`aws-ecs-app-infra` — VPC, ALB, ECS, VPC endpoints, CodePipeline/CodeDeploy) is
the only thing here with an ongoing hourly cost (ALB + 6 interface-endpoint-hours + the Fargate
task). Everything else — both GitHub repos, the `aws-ecs-bootstrap` stack, the shared IAM roles
(`iam-lab-gitsync-*`, `github-actions-cfn-package-aws-ecs`), and the S3 bucket — costs nothing
meaningful sitting idle, so none of that gets torn down between sessions.

## Spin down

1. Empty the ECR repo first (`aws ecr batch-delete-image` for every image digest) — **not**
   `EmptyOnDelete: true` on the template, since that would let a future *accidental* rollback
   silently wipe image history too. Emptying manually only affects this deliberate teardown.
   ```
   DIGESTS=$(aws ecr describe-images --repository-name aws-ecs-app --query 'imageDetails[].imageDigest' --output json)
   aws ecr batch-delete-image --repository-name aws-ecs-app \
     --image-ids "$(echo $DIGESTS | python3 -c 'import json,sys; print(json.dumps([{"imageDigest": d} for d in json.load(sys.stdin)]))')"
   ```
2. `aws cloudformation delete-stack --stack-name aws-ecs-app-infra` — tears down all four nested
   children (network, ecr, ecs, pipeline) in one shot. Poll `aws cloudformation describe-stacks
   --stack-name aws-ecs-app-infra --query 'Stacks[0].StackStatus'` until it errors "does not exist".
3. If it hits `DELETE_FAILED`, it's usually the ALB listener/target-group drift left over from
   blue/green testing (CodeDeploy points the live listener at whichever target group is
   currently "green", but CloudFormation's template still thinks it's "blue" — a resource it's
   trying to delete looks "still in use"). Just retry `delete-stack` once the underlying ALB is
   actually gone (check with `aws elbv2 describe-load-balancers --names aws-ecs-app-alb`) — it
   resolves itself on retry.

## Two GitSync gotchas discovered spinning this down/up on 2026-08-18 and 2026-08-25

**Both of these bit us for real — don't skip either step below.**

### Gotcha 1: deleting the stack directly creates a sync blocker that never clears itself

GitSync sees a stack it's watching disappear "by something besides StackSync" and automatically
creates a blocker that permanently disables syncing for that resource — it does not self-heal, no
matter how many commits you push afterward. The console gives no way to even see this once the
stack is gone (you normally reach Git sync *through* a stack's page, and there's no stack to open
into anymore) — but the `codeconnections` CLI can see and fix it directly:

```
# Find the repo's link ID (once; reuse the value)
aws codeconnections list-repository-links
# -> note RepositoryLinkId for aws-ecs-infra

# Confirm the sync configuration and see its ConfigFile
aws codeconnections list-sync-configurations \
  --repository-link-id <link-id> --sync-type CFN_STACK_SYNC

# Check for an active blocker
aws codeconnections get-sync-blocker-summary \
  --sync-type CFN_STACK_SYNC --resource-name aws-ecs-app-infra

# Resolve it (Id comes from the previous command's output)
aws codeconnections update-sync-blocker \
  --id <blocker-id> --sync-type CFN_STACK_SYNC --resource-name aws-ecs-app-infra \
  --resolved-reason "Stack deleted deliberately for a planned spin-down; redeploying fresh."
```

### Gotcha 2: `CFN_STACK_SYNC` can only update an existing stack, never create one from scratch

Even with the blocker cleared, every sync attempt will fail with `"Failed to create changeset...
Stack: aws-ecs-app-infra not found"`. This isn't a stale-config problem — a **brand new** sync
configuration (delete the old one with `delete-sync-configuration`, make a new one with
`create-sync-configuration`) hits the identical error. GitSync's `CFN_STACK_SYNC` type only knows
how to run `UpdateStack` via changeset; it has no code path for the initial `CreateStack`. The
very first time this stack ever existed, that initial creation happened through the AWS Console's
"Create stack → Sync from Git" wizard, which does a real create behind the scenes in addition to
registering the sync configuration.

The fix: do that one initial `create-stack` yourself, directly, using the already-packaged
template sitting in the repo. Everything after this one call goes back through GitSync normally,
since the stack now exists and future pushes are plain updates:

```
cd aws-ecs-infra && git pull
aws cloudformation create-stack \
  --stack-name aws-ecs-app-infra \
  --template-body file://packaged/root-packaged.yaml \
  --role-arn arn:aws:iam::107737161507:role/iam-lab-gitsync-stack-execution-role \
  --capabilities CAPABILITY_AUTO_EXPAND CAPABILITY_NAMED_IAM CAPABILITY_IAM
```

If the sync configuration for `aws-ecs-app-infra` no longer exists at all (e.g. you deleted it
while debugging), recreate it so future pushes keep syncing:
```
aws codeconnections create-sync-configuration \
  --branch main --config-file "root-deployment.yaml" \
  --repository-link-id <link-id> --resource-name aws-ecs-app-infra \
  --role-arn arn:aws:iam::107737161507:role/iam-lab-gitsync-connection-role \
  --sync-type CFN_STACK_SYNC --publish-deployment-status ENABLED \
  --trigger-resource-update-on ANY_CHANGE --pull-request-comment ENABLED
```

## Spin up (full sequence, tested end-to-end on 2026-08-25)

1. **Check the stack is actually gone**: `aws cloudformation describe-stacks --stack-name
   aws-ecs-app-infra` should error "does not exist".
2. **Clear the sync blocker** (Gotcha 1 above) — before anything else.
3. **Manually create the stack once** (Gotcha 2 above) from `packaged/root-packaged.yaml`. If that
   file is stale (templates/ changed since the last CI packaging run), push a trivial change to
   `templates/root.yaml` first and wait for `Package Nested Templates` to finish, so the packaged
   file matches what's actually in `templates/`.
4. **Watch it deploy**: `aws cloudformation describe-stacks --stack-name aws-ecs-app-infra
   --query 'Stacks[0].StackStatus'` → expect `CREATE_IN_PROGRESS` → `CREATE_COMPLETE` in under
   ~10 minutes. All the IAM permission gaps found during initial development
   (`ecr:TagResource`, `elasticloadbalancing:DescribeTargetGroupAttributes`,
   `codestar-connections:PassConnection`, `ecs:RegisterTaskDefinition` + matching `iam:PassRole`)
   are already baked into the shared execution role, so this goes smoothly.
5. **`EcsStack` will get stuck retrying** with `CannotPullContainerError` — expected, not a bug.
   The freshly-recreated ECR repo is empty and the task definition wants tag `1.0.0`, which
   doesn't exist yet.
6. **Delete all existing git tags in `aws-ecs-app`** — they're stale once ECR is wiped, and the
   version-bump script needs to bootstrap back to `1.0.0` (matching the task definition's default
   `InitialImageTag`), not continue from wherever it left off last time:
   ```
   cd aws-ecs-app
   for t in $(git tag); do git tag -d "$t"; git push origin ":refs/tags/$t"; done
   ```
7. **Push any commit to `aws-ecs-app`** to trigger `build-and-push.yml` and get a real `1.0.0`
   image into ECR. A trivial one-line change is fine. Within a few minutes the stuck ECS service
   retries successfully and stabilizes on its own — no further action needed.
8. **Get the new ALB DNS name** (changes every time the ALB is recreated):
   ```
   aws cloudformation describe-stacks --stack-name aws-ecs-app-infra --query 'Stacks[0].Outputs'
   ```
9. **Confirm end-to-end**: `curl http://<new-alb-dns>/health` and `/version`.

Total time start to finish: ~20 minutes including both GitSync gotchas above, once you know to
expect them.
