terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.0"
    }
  }

    backend "s3" {
    bucket         = "gmail-txn-tf-state-035071592330"   # paste from script output
    key            = "prod/terraform.tfstate"
    region         = "us-west-2"
    dynamodb_table = "gmail-txn-tf-locks"
    encrypt        = true
  }                                         
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project   = "gmail-transaction-pipeline"
      ManagedBy = "terraform"
    }
  }
}

data "aws_caller_identity" "current" {}