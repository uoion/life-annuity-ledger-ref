variable "aws_region" {
  description = "AWS region — Cape Town (af-south-1) enforces South African POPIA data residency."
  type        = string
  default     = "af-south-1"
}

variable "project_name" {
  description = "Short resource prefix for the Life Annuity Ledger service."
  type        = string
  default     = "life-annuity-ledger"
}

variable "environment" {
  description = "Deployment environment (dev, staging, prod)."
  type        = string
  default     = "prod"
}

variable "container_image" {
  description = "Immutable container image URI for the FastAPI service."
  type        = string
  default     = "ghcr.io/uoion/life-annuity-ledger-ref:0.1.0"
}

variable "desired_count" {
  description = "Number of ECS Fargate tasks to run across availability zones."
  type        = number
  default     = 2
}

variable "db_instance_class" {
  description = "RDS PostgreSQL instance class."
  type        = string
  default     = "db.t4g.micro"
}

variable "db_username" {
  description = "RDS PostgreSQL master username (password managed via AWS Secrets Manager)."
  type        = string
  default     = "annuity_admin"
}

variable "enable_multi_az" {
  description = "Enable synchronous Multi-AZ replication for RDS PostgreSQL."
  type        = bool
  default     = true
}

variable "deletion_protection" {
  description = "Prevent accidental RDS deletion in regulated production environments."
  type        = bool
  default     = true
}

variable "skip_final_snapshot" {
  description = "Whether to skip final RDS snapshot on destroy (must be false in production)."
  type        = bool
  default     = false
}
