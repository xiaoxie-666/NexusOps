param(
  [string]$BaseUrl = "http://127.0.0.1:18081",
  [string]$WebhookToken = "",
  [string]$OutputPath = ".\eval-results\nexusops-webhook-report.json"
)
$ErrorActionPreference = "Stop"
$BaseUrl = $BaseUrl.TrimEnd('/')
$headers = @{}
if ($WebhookToken) { $headers["X-NexusOps-Webhook-Token"] = $WebhookToken }
$fp = "nexusops-eval-" + [Guid]::NewGuid().ToString("N")
$startsAt = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ")
$alert = @{
  status = "firing"; fingerprint = $fp; startsAt = $startsAt
  labels = @{ alertname = "NexusOpsEvalApi5xx"; severity = "warning"; service = "nexusops-eval-service"; environment = "TEST" }
  annotations = @{ summary = "Synthetic API 5xx incident for NexusOps evaluation"; description = "This is a synthetic, local verification alert. No production fault." }
}
function Send-Alert([string]$status) {
    $alert.status = $status
    $payload = @{ receiver = "nexusops-evaluation"; status = $status; alerts = @($alert) }
    $json = $payload | ConvertTo-Json -Depth 10 -Compress
    $bytes = [Text.Encoding]::UTF8.GetBytes($json)
    return Invoke-RestMethod -Uri "$BaseUrl/api/webhooks/alertmanager" -Method Post -ContentType "application/json; charset=utf-8" -Headers $headers -Body $bytes
}
Write-Host "NexusOps Webhook deduplication test (creates one DEMO Incident)" -ForegroundColor Cyan
$first = Send-Alert "firing"
$second = Send-Alert "firing"
$third = Send-Alert "resolved"
$one = $first.alerts[0]; $two = $second.alerts[0]; $three = $third.alerts[0]
$checks = [ordered]@{
    firstCreatesOne = ($first.created -eq 1 -and $one.created)
    duplicateDeduplicated = ($second.created -eq 0 -and $second.deduplicated -eq 1)
    sameIncidentId = ($one.incidentId -gt 0 -and $one.incidentId -eq $two.incidentId -and $two.incidentId -eq $three.incidentId)
    occurrenceCountThree = ($three.occurrenceCount -eq 3)
    resolvedUpdatesAlert = ($three.status -eq "RESOLVED" -and $third.created -eq 0)
}
$report = [ordered]@{
    testedAtUtc = [DateTime]::UtcNow.ToString("o")
    endpoint = "$BaseUrl/api/webhooks/alertmanager"
    simulated = $true
    fingerprint = $fp
    incidentId = $one.incidentId
    createdOnFirst = $first.created
    deduplicatedOnSecond = $second.deduplicated
    resolvedStatus = $three.status
    totalOccurrences = $three.occurrenceCount
    checks = $checks
    passed = @( $checks.GetEnumerator() | Where-Object { -not $_.Value } ).Count -eq 0
}
$dir = Split-Path -Parent $OutputPath
if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
$report | ConvertTo-Json -Depth 8 | Out-File -FilePath $OutputPath -Encoding utf8
foreach ($check in $checks.GetEnumerator()) {
  $label = if ($check.Value) { "PASS" } else { "FAIL" }
  Write-Host "[$label] $($check.Key)"
}
Write-Host "Created Incident ID: $($one.incidentId); report: $OutputPath"
if (-not $report.passed) { throw "Webhook verification did not pass all checks" }
