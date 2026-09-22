terraform {
  required_version = ">= 1.7.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }

  # Local backend, deliberately: this is a single-developer dev environment, not a shared
  # long-lived one — no remote state locking/collaboration concerns to solve for yet. Promote to
  # an S3+DynamoDB (or Terraform Cloud) backend the moment a second person or a staging/prod
  # environment needs to share state.
  backend "local" {
    path = "terraform.tfstate"
  }
}

provider "aws" {
  region  = var.aws_region
  profile = var.aws_profile

  default_tags {
    tags = local.common_tags
  }
}
