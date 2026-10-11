#!/usr/bin/env bash
# Tear down a Cascade deployment completely (DEPLOY.md, "Teardown"). Run from the repository root with credentials
# for the account, e.g.:  AWS_REGION=eu-central-1 scripts/aws_teardown.sh
#
# 1. delete what the app created at runtime (not in Terraform state): artifact Lambdas and their log groups,
#    integration secrets, running SageMaker jobs;
# 2. `terraform apply -var teardown_ready=true` so buckets and image repositories may be deleted with contents;
# 3. `terraform destroy`.
set -euo pipefail
REGION="${AWS_REGION:-$(aws configure get region)}"
TF="terraform -chdir=infra/terraform"
echo "Tearing down Cascade in $REGION (account $(aws sts get-caller-identity --query Account --output text))"

for fn in $(aws lambda list-functions --region "$REGION" --query "Functions[?starts_with(FunctionName,'cascade-artifact-')].FunctionName" --output text); do
  echo "  lambda $fn"; aws lambda delete-function --region "$REGION" --function-name "$fn"
  aws logs delete-log-group --region "$REGION" --log-group-name "/aws/lambda/$fn" 2>/dev/null || true
done
for s in $(aws secretsmanager list-secrets --region "$REGION" --filters Key=name,Values=cascade/integrations/ --query "SecretList[].ARN" --output text); do
  echo "  secret $s"; aws secretsmanager delete-secret --region "$REGION" --secret-id "$s" --force-delete-without-recovery >/dev/null
done
for j in $(aws sagemaker list-training-jobs --region "$REGION" --status-equals InProgress --name-contains cascade-dl --query "TrainingJobSummaries[].TrainingJobName" --output text); do
  echo "  stop training job $j"; aws sagemaker stop-training-job --region "$REGION" --training-job-name "$j"
done

$TF apply -input=false -auto-approve -var teardown_ready=true
$TF destroy -input=false -auto-approve -var teardown_ready=true
echo "Done. Check the console for anything left: Cost Explorer by tag Project=cascade."
