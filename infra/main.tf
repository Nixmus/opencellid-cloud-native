terraform {
  required_version = ">= 1.9"
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}

provider "azurerm" {
  features {}
  subscription_id = var.subscription_id
}

resource "random_string" "suffix" {
  length  = 6
  special = false
  upper   = false
}

resource "random_password" "db" {
  length  = 28
  special = false
}

locals {
  name         = "cells${random_string.suffix.result}"
  images       = "ghcr.io/${var.github_owner}"
  database_url = "postgresql://appadmin:${random_password.db.result}@${azurerm_postgresql_flexible_server.db.fqdn}:5432/cells?sslmode=require"
}

resource "azurerm_resource_group" "main" {
  name     = "rg-opencellid"
  location = var.location
}

# ---------- Archivos y cola ----------
resource "azurerm_storage_account" "data" {
  name                     = "st${local.name}"
  resource_group_name      = azurerm_resource_group.main.name
  location                 = azurerm_resource_group.main.location
  account_tier             = "Standard"
  account_replication_type = "LRS"
  min_tls_version          = "TLS1_2"
}

resource "azurerm_storage_container" "raw" {
  name                  = "raw"
  storage_account_id    = azurerm_storage_account.data.id
  container_access_type = "private"
}

resource "azurerm_storage_container" "function_code" {
  name                  = "function-code"
  storage_account_id    = azurerm_storage_account.data.id
  container_access_type = "private"
}

resource "azurerm_storage_queue" "ingest" {
  name               = "ingest-jobs"
  storage_account_id = azurerm_storage_account.data.id
}

# ---------- Monitoreo ----------
resource "azurerm_log_analytics_workspace" "main" {
  name                = "log-${local.name}"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  sku                 = "PerGB2018"
  retention_in_days   = 30
  daily_quota_gb      = 0.5
}

resource "azurerm_application_insights" "main" {
  name                = "appi-${local.name}"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  workspace_id        = azurerm_log_analytics_workspace.main.id
  application_type    = "web"
}

# ---------- Base de datos ----------
resource "azurerm_postgresql_flexible_server" "db" {
  name                   = "psql-${local.name}"
  resource_group_name    = azurerm_resource_group.main.name
  location               = azurerm_resource_group.main.location
  version                = "16"
  administrator_login    = "appadmin"
  administrator_password = random_password.db.result
  sku_name               = "B_Standard_B1ms"
  storage_mb             = 32768
  backup_retention_days  = 7

  lifecycle {
    ignore_changes = [zone]
  }
}

resource "azurerm_postgresql_flexible_server_database" "cells" {
  name      = "cells"
  server_id = azurerm_postgresql_flexible_server.db.id
}

resource "azurerm_postgresql_flexible_server_configuration" "extensions" {
  name      = "azure.extensions"
  server_id = azurerm_postgresql_flexible_server.db.id
  value     = "POSTGIS"
}

# Permite conexiones desde servicios de Azure. La conexión exige TLS y contraseña.
resource "azurerm_postgresql_flexible_server_firewall_rule" "azure" {
  name             = "allow-azure-services"
  server_id        = azurerm_postgresql_flexible_server.db.id
  start_ip_address = "0.0.0.0"
  end_ip_address   = "0.0.0.0"
}

# ---------- Contenedores ----------
resource "azurerm_container_app_environment" "main" {
  name                       = "cae-${local.name}"
  resource_group_name        = azurerm_resource_group.main.name
  location                   = azurerm_resource_group.main.location
  logs_destination           = "log-analytics"
  log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id
}

resource "azurerm_container_app" "api" {
  name                         = "ca-api"
  resource_group_name          = azurerm_resource_group.main.name
  container_app_environment_id = azurerm_container_app_environment.main.id
  revision_mode                = "Single"

  secret {
    name  = "database-url"
    value = local.database_url
  }

  # Sin acceso público: solo la web puede llamarla.
  ingress {
    external_enabled = false
    target_port      = 8000
    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    min_replicas = 0
    max_replicas = 3

    http_scale_rule {
      name                = "http"
      concurrent_requests = "20"
    }

    container {
      name   = "api"
      image  = "${local.images}/cells-api:latest"
      cpu    = 0.25
      memory = "0.5Gi"

      env {
        name        = "DATABASE_URL"
        secret_name = "database-url"
      }

      liveness_probe {
        transport = "HTTP"
        port      = 8000
        path      = "/health"
      }

      readiness_probe {
        transport = "HTTP"
        port      = 8000
        path      = "/ready"
      }
    }
  }

  # La imagen la actualiza GitHub Actions, no Terraform.
  lifecycle {
    ignore_changes = [template[0].container[0].image]
  }
}

resource "azurerm_container_app" "web" {
  name                         = "ca-web"
  resource_group_name          = azurerm_resource_group.main.name
  container_app_environment_id = azurerm_container_app_environment.main.id
  revision_mode                = "Single"

  ingress {
    external_enabled = true
    target_port      = 80
    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    min_replicas = 0
    max_replicas = 2

    http_scale_rule {
      name                = "http"
      concurrent_requests = "50"
    }

    container {
      name   = "web"
      image  = "${local.images}/cells-web:latest"
      cpu    = 0.25
      memory = "0.5Gi"

      env {
        name  = "API_URL"
        value = "http://${azurerm_container_app.api.name}"
      }
    }
  }

  lifecycle {
    ignore_changes = [template[0].container[0].image]
  }
}

resource "azurerm_container_app_job" "ingestor" {
  name                         = "job-ingestor"
  location                     = azurerm_resource_group.main.location
  resource_group_name          = azurerm_resource_group.main.name
  container_app_environment_id = azurerm_container_app_environment.main.id
  replica_timeout_in_seconds   = 1800
  replica_retry_limit          = 1

  secret {
    name  = "storage-connection"
    value = azurerm_storage_account.data.primary_connection_string
  }

  secret {
    name  = "database-url"
    value = local.database_url
  }

  event_trigger_config {
    parallelism              = 1
    replica_completion_count = 1

    scale {
      min_executions              = 0
      max_executions              = 1
      polling_interval_in_seconds = 60

      rules {
        name             = "queue"
        custom_rule_type = "azure-queue"
        metadata = {
          accountName = azurerm_storage_account.data.name
          queueName   = azurerm_storage_queue.ingest.name
          queueLength = "1"
        }
        authentication {
          secret_name       = "storage-connection"
          trigger_parameter = "connection"
        }
      }
    }
  }

  template {
    container {
      name   = "ingestor"
      image  = "${local.images}/cells-ingestor:latest"
      cpu    = 0.5
      memory = "1Gi"

      env {
        name        = "STORAGE_CONNECTION"
        secret_name = "storage-connection"
      }

      env {
        name        = "DATABASE_URL"
        secret_name = "database-url"
      }
    }
  }

  lifecycle {
    ignore_changes = [template[0].container[0].image]
  }
}

# ---------- Función diaria ----------
resource "azurerm_service_plan" "function" {
  name                = "asp-${local.name}"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  sku_name            = "FC1"
  os_type             = "Linux"
}

resource "azurerm_function_app_flex_consumption" "downloader" {
  name                = "func-${local.name}"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  service_plan_id     = azurerm_service_plan.function.id

  storage_container_type      = "blobContainer"
  storage_container_endpoint  = "${azurerm_storage_account.data.primary_blob_endpoint}${azurerm_storage_container.function_code.name}"
  storage_authentication_type = "StorageAccountConnectionString"
  storage_access_key          = azurerm_storage_account.data.primary_access_key

  runtime_name           = "python"
  runtime_version        = "3.11"
  maximum_instance_count = 40
  instance_memory_in_mb  = 2048

  app_settings = {
    DATA_STORAGE_CONNECTION = azurerm_storage_account.data.primary_connection_string
    OPENCELLID_DOWNLOAD_URL = var.opencellid_download_url
  }

  site_config {
    application_insights_connection_string = azurerm_application_insights.main.connection_string
  }
}
