param(
    [string]$Catalogue = 'data/catalogue/identities.csv',
    [string]$Review = 'pubchem-synonym-review.csv',
    [string]$Output = 'pubchem-synonym-gaps.csv'
)

$ErrorActionPreference = 'Stop'
$endpoint = 'https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound'
$completed = @{}
if (Test-Path -LiteralPath $Review) {
    foreach ($result in (Import-Csv -LiteralPath $Review -Encoding UTF8)) {
        if ($result.status -eq 'ok') {
            $completed["$($result.pubchem_cid)/$($result.inchi_key)"] = $true
        }
    }
}

function Get-PubChemResult([string]$Path) {
    try {
        Invoke-RestMethod -Uri "$endpoint/$Path" -TimeoutSec 20
    } catch {
        if (!$_.Exception.Response -or [int]$_.Exception.Response.StatusCode -ne 404) { throw }
    } finally {
        Start-Sleep -Milliseconds 350
    }
}

$rows = @(Import-Csv -LiteralPath $Catalogue -Delimiter '|' -Encoding UTF8)
$results = foreach ($row in $rows) {
    if ($row.pubchem_cid -and $completed.ContainsKey("$($row.pubchem_cid)/$($row.inchi_key)")) { continue }

    $records = @{}
    $names = @($row.name) + @($row.aliases -split ';')
    $errors = @()
    $lookups = @()
    if ($row.inchi_key) { $lookups += "inchikey/$($row.inchi_key)" }
    $lookups += @($names | Where-Object { $_ } | Select-Object -Unique | ForEach-Object {
        "name/$([uri]::EscapeDataString($_))"
    })

    foreach ($lookup in $lookups) {
        try {
            $response = Get-PubChemResult "$lookup/property/InChIKey,MolecularFormula,Title/JSON"
            foreach ($record in $response.PropertyTable.Properties) {
                if ($record.CID) { $records[[string]$record.CID] = $record }
            }
        } catch {
            $errors += $_.Exception.Message
        }
    }

    $candidates = @(foreach ($record in ($records.Values | Sort-Object CID)) {
        $exact = [bool]($row.inchi_key -and $row.inchi_key -ceq $record.InChIKey)
        $aliases = @()
        if ($exact -or !$row.inchi_key) {
            try {
                $response = Get-PubChemResult "cid/$($record.CID)/synonyms/JSON"
                $aliases = @($response.InformationList.Information | Where-Object {
                    $_.CID -eq $record.CID
                } | ForEach-Object { $_.Synonym } | Where-Object { $_ -and $names -notcontains $_ } | Select-Object -Unique)
            } catch {
                $errors += $_.Exception.Message
            }
        }
        [pscustomobject]@{
            cid = $record.CID
            key = $record.InChIKey
            formula = $record.MolecularFormula
            title = $record.Title
            exact_key = $exact
            synonyms = $aliases
        }
    })

    $status = if (@($candidates | Where-Object { $_.exact_key -and $_.synonyms.Count }).Count) {
        'exact key with synonyms'
    } elseif (@($candidates | Where-Object exact_key).Count) {
        'exact key without synonyms'
    } elseif ($candidates.Count) {
        'identity review needed'
    } elseif ($errors.Count) {
        'request error'
    } else {
        'no PubChem record found'
    }
    [pscustomobject]@{
        name = $row.name
        inchi_key = $row.inchi_key
        previous_cid = $row.pubchem_cid
        status = $status
        candidates_json = ConvertTo-Json -InputObject $candidates -Depth 5 -Compress
        detail = ($errors | Select-Object -Unique) -join ' | '
    }
}
$results | Export-Csv -LiteralPath $Output -NoTypeInformation -Encoding UTF8
$results | Group-Object status | Select-Object Count, Name
