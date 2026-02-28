locals {
  prefix = "/${var.project_name}/${var.environment}"
}

resource "aws_ssm_parameter" "secret" {
  for_each = nonsensitive(toset(keys(var.secret_env_vars)))

  name      = "${local.prefix}/${each.key}"
  type      = "SecureString"
  value     = var.secret_env_vars[each.key]
  overwrite = var.overwrite

  lifecycle {
    ignore_changes = [value]
  }
}

resource "aws_ssm_parameter" "non_secret" {
  for_each = var.non_secret_env_vars

  name      = "${local.prefix}/${each.key}"
  type      = "String"
  value     = each.value
  overwrite = var.overwrite
}
