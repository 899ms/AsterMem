<#
    AsterMem CLI (Windows / PowerShell) - AI agent gateway to a self-hosted AsterMem memory service

    Background: astermem.sh depends on bash + curl + python3, which Agents (Cursor / Claude Code)
    on Windows don't have by default. A behaviorally identical PowerShell version is needed.
    Design intent: Sub-commands and exit codes map 1:1 to astermem.sh, output format stays
    consistent, SKILL.md usage only needs script name change; JSON assembly is handled by
    ConvertTo-Json for proper escaping.
    Key constraints: Credentials are read only from %USERPROFILE%\.astermem\credentials or
    env vars, never hardcoded; exit codes align with the sh version (2 missing credentials /
    3 network error / 4 HTTP non-200 / 5 invalid JSON arguments).

    Usage:
      astermem.ps1 quick "<text>" [top_k]            # semantic quick match (preferred for recall)
      astermem.ps1 search "<query>" [limit]          # search memories
      astermem.ps1 add "<title>" "<content>" [tags,csv] [priority]
      astermem.ps1 get <mem_id|trunk_id>
      astermem.ps1 update <mem_id> <field> "<value>" # field: title|content|status|priority
      astermem.ps1 patch <mem_id> "<old_text>" "<new_text>"
      astermem.ps1 delete <mem_id>                   # archives (soft delete)
      astermem.ps1 list [status] [limit]
      astermem.ps1 tags "<tag1,tag2>" [limit]        # list memories by tags
      astermem.ps1 stats
      astermem.ps1 profile [core|standard|full]      # one-call user profile (fields + AI claims)
      astermem.ps1 config
      astermem.ps1 provider <id> '<json_patch>'
      astermem.ps1 test-provider <id>
      astermem.ps1 rebuild
      astermem.ps1 rebuild-status
      astermem.ps1 api <METHOD> </api/path> ['<json>'] [confirm]
      astermem.ps1 call <tool> '<json_arguments>'    # raw access to any agent tool

    Copyright (c) 2026 Asterove. AGPL-3.0 License
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)][string]$Command = "help",
    [Parameter(Position = 1, ValueFromRemainingArguments = $true)][string[]]$Args = @()
)

$ErrorActionPreference = "Stop"

function Show-Usage {
    Write-Host @"
astermem.ps1 quick "<text>" [top_k]            # semantic quick match (preferred for recall)
astermem.ps1 search "<query>" [limit]          # search memories
astermem.ps1 add "<title>" "<content>" [tags,csv] [priority]
astermem.ps1 get <mem_id|trunk_id>
astermem.ps1 update <mem_id> <field> "<value>" # field: title|content|status|priority
astermem.ps1 patch <mem_id> "<old_text>" "<new_text>"
astermem.ps1 delete <mem_id>                   # archives (soft delete)
astermem.ps1 list [status] [limit]
astermem.ps1 tags "<tag1,tag2>" [limit]        # list memories by tags
astermem.ps1 stats
astermem.ps1 profile [core|standard|full]      # one-call user profile (fields + AI claims)
astermem.ps1 config
astermem.ps1 provider <id> '<json_patch>'
astermem.ps1 test-provider <id>
astermem.ps1 rebuild
astermem.ps1 rebuild-status
astermem.ps1 api <METHOD> </api/path> ['<json>'] [confirm]
astermem.ps1 call <tool> '<json_arguments>'    # raw access to any agent tool
"@
}

function Get-Arg {
    param([int]$Index, [string]$Default = $null, [string]$Required = $null)
    if ($Args.Count -gt $Index -and $Args[$Index] -ne "") { return $Args[$Index] }
    if ($Required) {
        Write-Error "[astermem] $Required"
        exit 1
    }
    return $Default
}

# Credential parsing: file contains KEY=VALUE lines; env vars take precedence over file
$credFile = if ($env:ASTERMEM_CREDENTIALS) { $env:ASTERMEM_CREDENTIALS } else { Join-Path $env:USERPROFILE ".astermem\credentials" }
$baseUrl = $env:ASTERMEM_BASE_URL
$token = $env:ASTERMEM_TOKEN

if (Test-Path $credFile) {
    foreach ($line in Get-Content $credFile) {
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$') {
            $key, $value = $Matches[1], $Matches[2].Trim('"')
            if ($key -eq "ASTERMEM_BASE_URL" -and -not $baseUrl) { $baseUrl = $value }
            if ($key -eq "ASTERMEM_TOKEN" -and -not $token) { $token = $value }
        }
    }
}

if (-not $baseUrl -or -not $token) {
    Write-Host @"
[astermem] Missing credentials.
Create $credFile with:
  ASTERMEM_BASE_URL=http://localhost:<port>
  ASTERMEM_TOKEN=ast_xxxxxxxx
Get a token from the AsterMem web UI: Admin -> API Tokens.
"@ -ForegroundColor Red
    exit 2
}

$baseUrl = $baseUrl.TrimEnd("/")

function Invoke-AgentTool {
    param([string]$Tool, [hashtable]$Arguments)

    $payload = @{ tool = $Tool; arguments = $Arguments } | ConvertTo-Json -Depth 12 -Compress
    try {
        $response = Invoke-RestMethod -Method Post -Uri "$baseUrl/api/agent/call" `
            -Headers @{ Authorization = "Bearer $token" } `
            -ContentType "application/json; charset=utf-8" `
            -Body ([System.Text.Encoding]::UTF8.GetBytes($payload)) -TimeoutSec 60
    } catch [System.Net.WebException], [Microsoft.PowerShell.Commands.HttpResponseException] {
        $status = $null
        if ($_.Exception.Response) { $status = [int]$_.Exception.Response.StatusCode }
        if ($status) {
            Write-Host "[astermem] HTTP $status`: $($_.ErrorDetails.Message)" -ForegroundColor Red
            exit 4
        }
        Write-Host "[astermem] Network error reaching $baseUrl (is the AsterMem server running?)" -ForegroundColor Red
        exit 3
    } catch {
        Write-Host "[astermem] Network error reaching $baseUrl (is the AsterMem server running?)" -ForegroundColor Red
        exit 3
    }

    # Output the result field as plain text, directly readable by the Agent
    $result = if ($null -ne $response.result) { $response.result } else { $response }
    if ($result -is [string]) { Write-Output $result }
    else { Write-Output ($result | ConvertTo-Json -Depth 12) }
}

function Invoke-AsterMemApi {
    param([string]$Method, [string]$Path, [string]$JsonBody = "", [bool]$Confirm = $false)
    $headers = @{ Authorization = "Bearer $token" }
    if ($Confirm) {
        $cleanPath = $Path.Split("?")[0]
        $headers["X-AsterMem-Confirm"] = "$($Method.ToUpper()) $cleanPath"
    }
    $params = @{
        Method = $Method
        Uri = "$baseUrl$Path"
        Headers = $headers
        TimeoutSec = 120
    }
    if ($JsonBody) {
        try { $null = $JsonBody | ConvertFrom-Json } catch {
            Write-Host "[astermem] invalid JSON body" -ForegroundColor Red
            exit 5
        }
        $params["ContentType"] = "application/json; charset=utf-8"
        $params["Body"] = [System.Text.Encoding]::UTF8.GetBytes($JsonBody)
    }
    try {
        $result = Invoke-RestMethod @params
        if ($result -is [string]) { Write-Output $result }
        else { Write-Output ($result | ConvertTo-Json -Depth 12) }
    } catch [System.Net.WebException], [Microsoft.PowerShell.Commands.HttpResponseException] {
        $status = if ($_.Exception.Response) { [int]$_.Exception.Response.StatusCode } else { 0 }
        if ($status) {
            Write-Host "[astermem] HTTP $status`: $($_.ErrorDetails.Message)" -ForegroundColor Red
            exit 4
        }
        Write-Host "[astermem] Network error reaching $baseUrl" -ForegroundColor Red
        exit 3
    }
}

function Split-Tags {
    param([string]$Csv)
    if (-not $Csv) { return @() }
    return @($Csv.Split(",") | ForEach-Object { $_.Trim() } | Where-Object { $_ })
}

switch ($Command) {
    "quick" {
        $text = Get-Arg 0 -Required 'usage: astermem.ps1 quick "<text>" [top_k]'
        Invoke-AgentTool "quick_match" @{ text = $text; top_k = [int](Get-Arg 1 "6") }
    }
    "search" {
        $query = Get-Arg 0 -Required 'usage: astermem.ps1 search "<query>" [limit]'
        Invoke-AgentTool "search_memories" @{ query = $query; limit = [int](Get-Arg 1 "10") }
    }
    "add" {
        $title = Get-Arg 0 -Required 'usage: astermem.ps1 add "<title>" "<content>" [tags,csv] [priority]'
        $content = Get-Arg 1 -Required "content required"
        Invoke-AgentTool "add_memory" @{
            title    = $title
            content  = $content
            tags     = Split-Tags (Get-Arg 2 "")
            priority = [int](Get-Arg 3 "5")
        }
    }
    "get" {
        $id = Get-Arg 0 -Required "usage: astermem.ps1 get <mem_id|trunk_id>"
        if ($id.StartsWith("trunk_")) { Invoke-AgentTool "get_trunk" @{ trunk_id = $id } }
        else { Invoke-AgentTool "get_memory" @{ memory_id = $id } }
    }
    "update" {
        $id = Get-Arg 0 -Required 'usage: astermem.ps1 update <mem_id> <field> "<value>"'
        $field = Get-Arg 1 -Required "field required: title|content|status|priority"
        $value = Get-Arg 2 -Required "value required"
        $payload = @{ memory_id = $id }
        $payload[$field] = if ($field -eq "priority") { [int]$value } else { $value }
        Invoke-AgentTool "update_memory" $payload
    }
    "patch" {
        $id = Get-Arg 0 -Required 'usage: astermem.ps1 patch <mem_id> "<old_text>" "<new_text>"'
        Invoke-AgentTool "patch_memory" @{
            memory_id = $id
            old_text  = (Get-Arg 1 -Required "old_text required")
            new_text  = (Get-Arg 2 -Required "new_text required")
        }
    }
    "delete" {
        $id = Get-Arg 0 -Required "usage: astermem.ps1 delete <mem_id>"
        Invoke-AgentTool "delete_memory" @{ memory_id = $id }
    }
    "list" {
        Invoke-AgentTool "list_memories" @{ status = (Get-Arg 0 "active"); limit = [int](Get-Arg 1 "20") }
    }
    "tags" {
        $tags = Get-Arg 0 -Required 'usage: astermem.ps1 tags "tag1,tag2" [limit]'
        Invoke-AgentTool "list_memories_by_tag" @{ tags = Split-Tags $tags; limit = [int](Get-Arg 1 "20") }
    }
    "stats" {
        Invoke-AgentTool "get_memory_stats" @{}
    }
    "profile" {
        Invoke-AgentTool "get_profile" @{ level = (Get-Arg 0 "standard") }
    }
    "config" {
        Invoke-AgentTool "get_system_config" @{}
    }
    "provider" {
        $id = Get-Arg 0 -Required "usage: astermem.ps1 provider <id> '<json_patch>'"
        $raw = Get-Arg 1 "{}"
        try {
            $patch = $raw | ConvertFrom-Json -AsHashtable
            if ($patch -isnot [hashtable]) { throw "provider patch must be an object" }
        } catch {
            Write-Host "[astermem] invalid provider JSON patch" -ForegroundColor Red
            exit 5
        }
        $patch["provider_id"] = $id
        Invoke-AgentTool "configure_provider" $patch
    }
    "test-provider" {
        Invoke-AgentTool "test_provider" @{ provider_id = (Get-Arg 0 -Required "usage: astermem.ps1 test-provider <id>") }
    }
    "rebuild" {
        Invoke-AgentTool "rebuild_vector_index" @{ confirm = $true }
    }
    "rebuild-status" {
        Invoke-AgentTool "get_vector_rebuild_status" @{}
    }
    "api" {
        $method = Get-Arg 0 -Required "usage: astermem.ps1 api <METHOD> </api/path> ['<json>'] [confirm]"
        $path = Get-Arg 1 -Required "API path required"
        Invoke-AsterMemApi $method.ToUpper() $path (Get-Arg 2 "") ((Get-Arg 3 "") -eq "confirm")
    }
    "call" {
        $tool = Get-Arg 0 -Required "usage: astermem.ps1 call <tool> '<json_arguments>'"
        $raw = Get-Arg 1 "{}"
        # Validate caller-provided JSON to avoid sending bad arguments to the server
        try {
            $parsed = $raw | ConvertFrom-Json -AsHashtable
        } catch {
            Write-Host "[astermem] invalid JSON arguments" -ForegroundColor Red
            exit 5
        }
        Invoke-AgentTool $tool $parsed
    }
    default { Show-Usage }
}
