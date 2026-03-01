resource "aws_ecs_cluster" "main" {
  name = "${var.project_name}-cluster-${var.environment}"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

resource "aws_ecs_cluster_capacity_providers" "main" {
  cluster_name = aws_ecs_cluster.main.name

  capacity_providers = ["FARGATE", "FARGATE_SPOT"]

  default_capacity_provider_strategy {
    base              = 1
    weight            = 100
    capacity_provider = "FARGATE_SPOT"
  }
}

resource "aws_iam_role" "ecs_execution" {
  name = "${var.project_name}-ecs-execution-role-${var.environment}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ecs-tasks.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "ecs_execution" {
  role       = aws_iam_role.ecs_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role" "ecs_task" {
  name = "${var.project_name}-ecs-task-role-${var.environment}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ecs-tasks.amazonaws.com"
        }
      }
    ]
  })
}

# Add permissions for task to read parameters/secrets if needed
resource "aws_iam_role_policy" "ecs_task_ssm" {
  name = "${var.project_name}-ecs-task-ssm"
  role = aws_iam_role.ecs_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["ssm:GetParameters", "ssm:GetParameter"]
        Resource = values(var.all_parameter_arns)
      },
      {
        Effect   = "Allow"
        Action   = ["kms:Decrypt"]
        Resource = var.ssm_kms_key_arn
      }
    ]
  })
}

resource "aws_iam_role_policy" "ecs_execution_ssm" {
  name = "${var.project_name}-ecs-execution-ssm"
  role = aws_iam_role.ecs_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["ssm:GetParameters", "ssm:GetParameter"]
        Resource = values(var.all_parameter_arns)
      },
      {
        Effect   = "Allow"
        Action   = ["kms:Decrypt"]
        Resource = var.ssm_kms_key_arn
      }
    ]
  })
}

resource "aws_iam_role_policy" "ecs_task_sqs_consume" {
  name = "${var.project_name}-ecs-task-sqs-consume-${var.environment}"
  role = aws_iam_role.ecs_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:GetQueueAttributes",
          "sqs:ChangeMessageVisibility"
        ]
        Resource = values(var.queue_arns)
      },
      {
        Effect   = "Allow"
        Action   = ["sqs:GetQueueUrl"]
        Resource = "*"
      }
    ]
  })
}

resource "aws_cloudwatch_log_group" "core" {
  name              = "/ecs/${var.project_name}-core-chat-worker"
  retention_in_days = 30
}

resource "aws_ecs_task_definition" "core" {
  family                   = "${var.project_name}-core-chat-worker"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "1024" # 1 vCPU
  memory                   = "2048" # 2 GB (required minimum for 1 vCPU on Fargate)
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  container_definitions = jsonencode([
    {
      name  = "core"
      image = var.core_chat_worker_image_url
      command = [
        "python",
        "-m",
        "apps.core.src.worker_main"
      ]

      environment = concat(
        [
          { name = "AWS_REGION", value = var.aws_region },
          { name = "AWS_ACCOUNT_ID", value = var.aws_account_id },
          { name = "PROJECT_NAME", value = var.project_name },
          { name = "ENVIRONMENT", value = var.environment },
          { name = "APP_ENV", value = var.environment }
        ],
        [for key, value in var.non_secret_env_vars : { name = key, value = value }]
      )

      secrets = [for key, arn in var.secret_parameter_arns : { name = key, valueFrom = arn }]

      healthCheck = {
        command     = ["CMD-SHELL", "kill -0 1 || exit 1"]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 60
      }

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.core.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "ecs"
        }
      }
    }
  ])
}

resource "aws_ecs_service" "core" {
  name            = "${var.project_name}-core-chat-worker"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.core.arn
  desired_count   = 1

  capacity_provider_strategy {
    capacity_provider = "FARGATE_SPOT"
    weight            = 100
  }

  network_configuration {
    subnets          = var.public_subnet_ids # Given we disabled NAT
    security_groups  = [var.ecs_security_group_id]
    assign_public_ip = true
  }
}
