[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateLength(2, 32768)]
    [string]$InputJson
)

$ErrorActionPreference = 'Stop'
$registryPath = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\references\operation-registry.json'))

function New-ValidationResult {
    param(
        [bool]$Valid,
        [string]$SafeReasonCode,
        [string[]]$Errors
    )

    [ordered]@{
        valid = $Valid
        safe_reason_code = $SafeReasonCode
        errors = @($Errors)
    } | ConvertTo-Json -Depth 5 -Compress
}

function Test-PlainObject {
    param([object]$Value)

    return $null -ne $Value -and
        $Value -isnot [System.Array] -and
        $Value -is [System.Management.Automation.PSCustomObject]
}

function Get-PropertyNames {
    param([object]$Value)

    if (-not (Test-PlainObject -Value $Value)) {
        return @()
    }

    return @($Value.PSObject.Properties | ForEach-Object { $_.Name })
}

function Find-ProhibitedContent {
    param(
        [object]$Value,
        [string]$Path = '$'
    )

    $prohibitedName = '(?i)(password|passwd|secret|token|authorization|private[_-]?key|certificate|client[_-]?secret|credential|cookie)'
    $prohibitedValue = '(?i)(^Bearer\s+|-----BEGIN [A-Z ]*PRIVATE KEY-----|^[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}$)'
    $results = New-Object System.Collections.Generic.List[string]

    if ($null -eq $Value) {
        return @()
    }

    if ($Value -is [System.Array]) {
        for ($index = 0; $index -lt $Value.Count; $index++) {
            foreach ($result in @(Find-ProhibitedContent -Value $Value[$index] -Path "$Path[$index]")) {
                $results.Add($result)
            }
        }
        return $results.ToArray()
    }

    if ($Value -is [System.Management.Automation.PSCustomObject]) {
        foreach ($property in $Value.PSObject.Properties) {
            $propertyPath = "$Path.$($property.Name)"
            if ($property.Name -match $prohibitedName) {
                $results.Add("Prohibited property name at $propertyPath.")
            }
            foreach ($result in @(Find-ProhibitedContent -Value $property.Value -Path $propertyPath)) {
                $results.Add($result)
            }
        }
        return $results.ToArray()
    }

    if ($Value -is [string]) {
        if ($Value.Length -gt 4096) {
            $results.Add("Oversized string at $Path.")
        }
        elseif ($Value -match $prohibitedValue) {
            $results.Add("Prohibited secret-like value at $Path.")
        }
    }

    return $results.ToArray()
}

function Test-GuidValue {
    param([object]$Value)

    $parsed = [guid]::Empty
    return $Value -is [string] -and
        [guid]::TryParse([string]$Value, [ref]$parsed) -and
        $parsed -ne [guid]::Empty
}

function Add-SchemaErrors {
    param(
        [string]$Name,
        [object]$Value,
        [object]$Schema,
        [System.Collections.Generic.List[string]]$Errors
    )

    if (-not (Test-PlainObject -Value $Value)) {
        $Errors.Add("$Name must be one JSON object.")
        return
    }

    if (-not (Test-PlainObject -Value $Schema)) {
        $Errors.Add("Operation registry schema for $Name is invalid.")
        return
    }

    $valueNames = @(Get-PropertyNames -Value $Value)
    $propertySpecs = if (Test-PlainObject -Value $Schema.properties) { $Schema.properties } else { [pscustomobject]@{} }
    $allowedNames = @(Get-PropertyNames -Value $propertySpecs)
    $requiredNames = @($Schema.required)

    foreach ($requiredName in $requiredNames) {
        if ($valueNames -notcontains [string]$requiredName) {
            $Errors.Add("$Name is missing required property: $requiredName.")
        }
    }

    foreach ($valueName in $valueNames) {
        if ($allowedNames -notcontains $valueName) {
            $Errors.Add("$Name contains unknown property: $valueName.")
            continue
        }

        $spec = $propertySpecs.PSObject.Properties[$valueName].Value
        $propertyValue = $Value.PSObject.Properties[$valueName].Value
        $type = [string]$spec.type

        switch ($type) {
            'string' {
                if ($propertyValue -isnot [string] -or [string]::IsNullOrWhiteSpace([string]$propertyValue)) {
                    $Errors.Add("$Name.$valueName must be a non-empty string.")
                }
                elseif ($null -ne $spec.max_length -and ([string]$propertyValue).Length -gt [int]$spec.max_length) {
                    $Errors.Add("$Name.$valueName exceeds its maximum length.")
                }
            }
            'boolean' {
                if ($propertyValue -isnot [bool]) {
                    $Errors.Add("$Name.$valueName must be a boolean.")
                }
            }
            'guid' {
                if (-not (Test-GuidValue -Value $propertyValue)) {
                    $Errors.Add("$Name.$valueName must be a non-empty GUID.")
                }
            }
            'integer' {
                if ($propertyValue -isnot [int] -and $propertyValue -isnot [long]) {
                    $Errors.Add("$Name.$valueName must be an integer.")
                }
            }
            default {
                $Errors.Add("Operation registry contains unsupported type for $Name.$valueName.")
            }
        }

        if ($null -ne $spec.enum -and @($spec.enum) -notcontains $propertyValue) {
            $Errors.Add("$Name.$valueName is not an allowed value.")
        }
    }
}

try {
    $envelope = $InputJson | ConvertFrom-Json
}
catch {
    New-ValidationResult -Valid $false -SafeReasonCode 'INVALID_EXECUTION_ENVELOPE' -Errors @('Input is not valid JSON.')
    exit 2
}

try {
    $registry = Get-Content -LiteralPath $registryPath -Raw | ConvertFrom-Json
}
catch {
    New-ValidationResult -Valid $false -SafeReasonCode 'DEPENDENCY_UNAVAILABLE' -Errors @('Approved operation registry is unavailable or invalid.')
    exit 2
}

$errors = New-Object System.Collections.Generic.List[string]
$topLevelProperties = @(
    'mode',
    'ticket_sys_id',
    'correlation_id',
    'attempt_id',
    'tenant_id',
    'target_type',
    'target_id',
    'operation',
    'parameters',
    'intended_state',
    'risk',
    'plan_fingerprint',
    'approval_entry_id',
    'claim_id',
    'claim_version'
)

if (-not (Test-PlainObject -Value $envelope)) {
    $errors.Add('Envelope must be one JSON object.')
}
else {
    $actualTopLevel = @(Get-PropertyNames -Value $envelope)

    foreach ($name in $topLevelProperties) {
        if ($actualTopLevel -notcontains $name) {
            $errors.Add("Missing required property: $name.")
        }
    }

    foreach ($name in $actualTopLevel) {
        if ($topLevelProperties -notcontains $name) {
            $errors.Add("Unknown top-level property: $name.")
        }
    }

    if ($null -ne $envelope.PSObject.Properties['mode'] -and
        ($envelope.mode -isnot [string] -or [string]$envelope.mode -notin @('preflight', 'execute', 'verify_only'))) {
        $errors.Add('mode must be preflight, execute, or verify_only.')
    }

    if ($null -ne $envelope.PSObject.Properties['ticket_sys_id'] -and
        ($envelope.ticket_sys_id -isnot [string] -or [string]$envelope.ticket_sys_id -notmatch '^[0-9a-fA-F]{32}$')) {
        $errors.Add('ticket_sys_id must be a 32-character ServiceNow sys_id.')
    }

    if ($null -ne $envelope.PSObject.Properties['correlation_id'] -and
        ($envelope.correlation_id -isnot [string] -or [string]::IsNullOrWhiteSpace([string]$envelope.correlation_id) -or [string]$envelope.correlation_id -notmatch '^[A-Za-z0-9._:-]{1,128}$')) {
        $errors.Add('correlation_id must use the approved bounded character set and length.')
    }

    if ($null -ne $envelope.PSObject.Properties['tenant_id'] -and -not (Test-GuidValue -Value $envelope.tenant_id)) {
        $errors.Add('tenant_id must be a non-empty GUID.')
    }

    if ($null -ne $envelope.PSObject.Properties['target_id'] -and
        $null -ne $envelope.target_id -and
        -not (Test-GuidValue -Value $envelope.target_id)) {
        $errors.Add('target_id must be null or a resolved non-empty GUID.')
    }

    if ($null -ne $envelope.PSObject.Properties['target_type'] -and
        ($envelope.target_type -isnot [string] -or [string]$envelope.target_type -notmatch '^[a-z][a-z0-9-]{0,63}$')) {
        $errors.Add('target_type is invalid.')
    }

    if ($null -ne $envelope.PSObject.Properties['operation'] -and
        ($envelope.operation -isnot [string] -or [string]$envelope.operation -notmatch '^[a-z][a-z0-9-]{0,63}$')) {
        $errors.Add('operation is invalid.')
    }

    if ($null -ne $envelope.PSObject.Properties['risk'] -and
        ($envelope.risk -isnot [string] -or [string]$envelope.risk -notin @('standard', 'high', 'pending-preflight'))) {
        $errors.Add('risk must be standard, high, or pending-preflight.')
    }

    $boundedIds = @('attempt_id', 'plan_fingerprint', 'approval_entry_id', 'claim_id')
    foreach ($boundedId in $boundedIds) {
        $property = $envelope.PSObject.Properties[$boundedId]
        if ($null -ne $property -and $null -ne $property.Value -and
            ($property.Value -isnot [string] -or [string]$property.Value -notmatch '^[A-Za-z0-9._:-]{1,256}$')) {
            $errors.Add("$boundedId must be null or use the approved bounded character set and length.")
        }
    }

    $claimVersionProperty = $envelope.PSObject.Properties['claim_version']
    if ($null -ne $claimVersionProperty -and $null -ne $claimVersionProperty.Value -and
        (($claimVersionProperty.Value -isnot [int] -and $claimVersionProperty.Value -isnot [long]) -or [long]$claimVersionProperty.Value -lt 0)) {
        $errors.Add('claim_version must be null or a non-negative integer.')
    }

    if ($null -ne $envelope.PSObject.Properties['parameters'] -and -not (Test-PlainObject -Value $envelope.parameters)) {
        $errors.Add('parameters must be one JSON object.')
    }

    if ($null -ne $envelope.PSObject.Properties['intended_state'] -and -not (Test-PlainObject -Value $envelope.intended_state)) {
        $errors.Add('intended_state must be one JSON object.')
    }

    if ($envelope.mode -eq 'preflight') {
        if ($envelope.risk -notin @('standard', 'high', 'pending-preflight')) {
            $errors.Add('preflight risk is invalid.')
        }
    }
    elseif ($envelope.mode -in @('execute', 'verify_only')) {
        if ($envelope.risk -notin @('standard', 'high')) {
            $errors.Add("$($envelope.mode) requires a final standard or high risk classification.")
        }
        foreach ($requiredExecutionId in @('attempt_id', 'plan_fingerprint', 'approval_entry_id', 'claim_id')) {
            if ([string]::IsNullOrWhiteSpace([string]$envelope.PSObject.Properties[$requiredExecutionId].Value)) {
                $errors.Add("$($envelope.mode) requires $requiredExecutionId.")
            }
        }
        if ($null -eq $envelope.claim_version) {
            $errors.Add("$($envelope.mode) requires a non-negative integer claim_version.")
        }
    }

    foreach ($contentError in @(Find-ProhibitedContent -Value $envelope)) {
        $errors.Add($contentError)
    }
}

$operationContract = $null
if ($errors.Count -eq 0) {
    if (-not (Test-PlainObject -Value $registry) -or
        [int]$registry.schema_version -ne 1 -or
        -not (Test-PlainObject -Value $registry.operations)) {
        $errors.Add('Approved operation registry structure is invalid.')
    }
    else {
        $operationProperty = $registry.operations.PSObject.Properties[[string]$envelope.operation]
        if ($null -eq $operationProperty) {
            New-ValidationResult -Valid $false -SafeReasonCode 'UNSUPPORTED_OPERATION' -Errors @('Operation is not in the approved immutable registry.')
            exit 2
        }
        $operationContract = $operationProperty.Value
    }
}

if ($errors.Count -eq 0) {
    if (-not (Test-PlainObject -Value $operationContract)) {
        $errors.Add('Approved operation contract is invalid.')
    }
    else {
        if ([string]$operationContract.target_type -ne [string]$envelope.target_type) {
            $errors.Add('target_type does not match the approved operation contract.')
        }
        if ([string]$operationContract.target_lifecycle -notin @('existing', 'create')) {
            $errors.Add('Approved operation contract has an invalid target_lifecycle.')
        }
        elseif ($envelope.mode -in @('execute', 'verify_only') -and [string]$operationContract.target_lifecycle -eq 'existing' -and
            -not (Test-GuidValue -Value $envelope.target_id)) {
            $errors.Add("$($envelope.mode) requires a resolved target_id GUID for an existing-target operation.")
        }
        elseif ($envelope.mode -eq 'execute' -and [string]$operationContract.target_lifecycle -eq 'create' -and $null -ne $envelope.target_id) {
            $errors.Add('execute requires a null target_id before a create-target operation.')
        }
        if ($envelope.risk -ne 'pending-preflight' -and [string]$operationContract.risk -ne [string]$envelope.risk) {
            $errors.Add('risk does not match the approved operation contract.')
        }
        if ([string]::IsNullOrWhiteSpace([string]$operationContract.handler)) {
            $errors.Add('Approved operation contract has no handler identifier.')
        }

        Add-SchemaErrors -Name 'parameters' -Value $envelope.parameters -Schema $operationContract.parameters -Errors $errors
        Add-SchemaErrors -Name 'intended_state' -Value $envelope.intended_state -Schema $operationContract.intended_state -Errors $errors

        if (@(Get-PropertyNames -Value $envelope.intended_state).Count -eq 0) {
            $errors.Add('intended_state must not be empty.')
        }
    }
}

if ($errors.Count -gt 0) {
    New-ValidationResult -Valid $false -SafeReasonCode 'INVALID_EXECUTION_ENVELOPE' -Errors $errors.ToArray()
    exit 2
}

New-ValidationResult -Valid $true -SafeReasonCode 'VALID' -Errors @()
exit 0
