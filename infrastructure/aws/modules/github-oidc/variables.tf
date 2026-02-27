variable "project_name" {
  description = "The name of the project"
  type        = string
}

variable "environment" {
  description = "The deployment environment"
  type        = string
}

variable "github_repo_owner" {
  description = "The GitHub username or organization name"
  type        = string
}

variable "github_repo_name" {
  description = "The GitHub repository name"
  type        = string
}
