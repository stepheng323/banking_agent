locals {
  prefix = "/${var.project_name}/${var.environment}"
}

resource "aws_ssm_parameter" "secret" {
  for_each = var.secret_env_vars

  name      = "${local.prefix}/${each.key}"
  type      = "SecureString"
  value     = each.value
  overwrite = var.overwrite
}

resource "aws_ssm_parameter" "non_secret" {
  for_each = var.non_secret_env_vars

  name      = "${local.prefix}/${each.key}"
  type      = "String"
  value     = each.value
  overwrite = var.overwrite
}
