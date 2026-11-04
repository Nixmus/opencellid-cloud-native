variable "subscription_id" {
  description = "ID de la suscripción Azure for Students"
  type        = string
}

variable "location" {
  description = "Región permitida por la política de tu suscripción, por ejemplo eastus2"
  type        = string
}

variable "github_owner" {
  description = "Tu usuario de GitHub en minúsculas"
  type        = string
}

variable "opencellid_download_url" {
  description = "Enlace de descarga de Colombia copiado de la página de OpenCellID"
  type        = string
  sensitive   = true
}
