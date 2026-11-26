output "aws_region" {
  description = "POPIA-compliant deployment region."
  value       = var.aws_region
}

output "ecs_cluster_name" {
  description = "ECS Fargate cluster name."
  value       = aws_ecs_cluster.main.name
}

output "rds_endpoint" {
  description = "Private PostgreSQL endpoint for the annuity audit ledger."
  value       = aws_db_instance.ledger.endpoint
}

output "payout_queue_url" {
  description = "SQS queue URL receiving transactional outbox payout events."
  value       = aws_sqs_queue.payout_events.url
}

output "payout_dlq_arn" {
  description = "SQS Dead-Letter Queue ARN after 3 failed disbursement attempts."
  value       = aws_sqs_queue.payout_dlq.arn
}
