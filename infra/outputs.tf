output "resource_group" {
  value = azurerm_resource_group.main.name
}

output "web_url" {
  value = "https://${azurerm_container_app.web.ingress[0].fqdn}"
}

output "function_app" {
  value = azurerm_function_app_flex_consumption.downloader.name
}

output "postgres_server" {
  value = azurerm_postgresql_flexible_server.db.name
}

output "database_url" {
  value     = local.database_url
  sensitive = true
}
