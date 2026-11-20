param(
    [string]$BaseUrl = "http://127.0.0.1:18081",
    [string]$Username = "engineer",
    [string]$Password = $env:NEXUSOPS_PASSWORD,
    [switch]$CreateSmokeIncident,
    [switch]$RunAgentAnalysis
)

$ErrorActionPreference = "Stop"
$BaseUrl = $BaseUrl.TrimEnd('/')
if ([string]::IsNullOrWhiteSpace($Password)) {
    $securePassword = Read-Host "Password for $Username" -AsSecureString
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassword)
    try { $Password = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
}
$pair = "${Username}:${Password}"
$encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($pair))
$headers = @{ Authorization = "Basic $encoded" }

function Assert-Ok($Condition, $Message) {
    if (-not $Condition) { throw $Message }
    Write-Host "[PASS] $Message" -ForegroundColor Green
}

Write-Host "NexusOps final verification: $BaseUrl" -ForegroundColor Cyan
$health = Invoke-RestMethod -Uri "$BaseUrl/actuator/health" -Method Get
Assert-Ok ($health.status -eq "UP") "Application health is UP"

$profile = Invoke-RestMethod -Uri "$BaseUrl/api/profile" -Headers $headers -Method Get
Assert-Ok ($profile.username -eq $Username) "Basic Auth profile accepted for $Username"

$dashboard = Invoke-RestMethod -Uri "$BaseUrl/api/dashboard/summary" -Headers $headers -Method Get
Assert-Ok ($null -ne $dashboard.metrics.totalIncidents) "Dashboard summary endpoint returned metrics"

$incidents = Invoke-RestMethod -Uri "$BaseUrl/api/incidents?page=1&pageSize=10" -Headers $headers -Method Get
Assert-Ok ($null -ne $incidents.total) "Incident list endpoint returned pagination metadata"

$alerts = Invoke-RestMethod -Uri "$BaseUrl/api/alerts?limit=10" -Headers $headers -Method Get
Assert-Ok ($null -ne $alerts) "Alert ingestion query endpoint is reachable"

if ($CreateSmokeIncident) {
    $payload = @{
        title = "NexusOps final verification smoke incident"
        description = "Temporary verification incident created by verify_final.ps1. No production action is requested."
        serviceName = "nexusops-smoke"
        environment = "TEST"
        category = "API"
        severity = "P3"
        source = "MANUAL"
        assignee = $Username
        impactScope = "Local smoke test only"
    } | ConvertTo-Json -Depth 5
    $created = Invoke-RestMethod -Uri "$BaseUrl/api/incidents" -Headers $headers -Method Post -ContentType "application/json" -Body $payload
    Assert-Ok ($created.id -gt 0) "Incident create endpoint accepted a smoke incident"
    if ($RunAgentAnalysis) {
        $analysis = Invoke-RestMethod -Uri "$BaseUrl/api/incidents/$($created.id)/analyze" -Headers $headers -Method Post
        Assert-Ok ($analysis.status -in @("COMPLETED", "DEGRADED")) "Multi-Agent analysis endpoint returned a recorded result"
        Assert-Ok (-not $analysis.operationGuard.executionAllowed) "OperationGuard keeps direct execution disabled"
    }
}

Write-Host "All selected NexusOps final checks passed." -ForegroundColor Green
