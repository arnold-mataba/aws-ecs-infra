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

## Spin up (do this Monday)

1. **Check the stack is actually gone**: `aws cloudformation describe-stacks --stack-name
   aws-ecs-app-infra` should return a "does not exist" error. If it's stuck in
   `DELETE_FAILED`, check which resource failed first before retrying.
2. **Force a fresh GitSync deploy.** GitSync watches `packaged/root-packaged.yaml` for new
   commits — it does not automatically notice an out-of-band stack deletion and recreate it on
   its own. Push a trivial change to `templates/root.yaml` (e.g. touch the description comment)
   to trigger the packaging workflow and a fresh sync attempt:
   ```
   cd aws-ecs-infra
   git pull
   # make a 1-line no-op edit to templates/root.yaml
   git add templates/root.yaml
   git commit -m "chore: redeploy after weekly spin-down"
   git push
   ```
3. **Watch it deploy**: `aws cloudformation describe-stacks --stack-name aws-ecs-app-infra
   --query 'Stacks[0].StackStatus'`. Expect `CREATE_IN_PROGRESS` → `CREATE_COMPLETE`. All the
   IAM permission gaps found during initial development (`ecr:TagResource`,
   `elasticloadbalancing:DescribeTargetGroupAttributes`, `codestar-connections:PassConnection`,
   `ecs:RegisterTaskDefinition` + matching `iam:PassRole`) are already baked into the shared
   execution role, so this should go smoothly without repeating that debugging.
4. **The ECS service will get stuck retrying** with `CannotPullContainerError` — this is
   expected, not a bug. The freshly-recreated ECR repo is empty, and the task definition
   references a specific tag (`1.0.0` by default) that doesn't exist yet.
5. **Push any commit to `aws-ecs-app`** to trigger `build-and-push.yml` and get a real image
   into ECR. If there's no real code change to make, a trivial one works fine — the version
   bump logic handles it automatically. Within a few minutes of the image landing, the stuck
   ECS service will retry successfully and stabilize on its own.
6. **Get the new ALB DNS name** (it changes every time the ALB is recreated):
   ```
   aws cloudformation describe-stacks --stack-name aws-ecs-app-infra \
     --query 'Stacks[0].Outputs'
   ```
7. **Confirm end-to-end**: `curl http://<new-alb-dns>/version` and `/health`.

Total expected time: network + ECR + ECS + pipeline stack creation historically took under
10 minutes once the permission fixes were in place; add a couple more minutes for the image
push and the ECS service's first successful retry cycle.
