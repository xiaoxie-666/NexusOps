$ErrorActionPreference = "Stop"

Write-Host "Applying NexusOps database migrations..." -ForegroundColor Cyan
docker compose exec app alembic upgrade head

Write-Host "Migration status:" -ForegroundColor Green
docker compose exec app alembic current
