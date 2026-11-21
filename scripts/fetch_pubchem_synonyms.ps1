param(
    [string]$Catalogue = 'data/catalogue/identities.csv',
    [string]$Output = 'pubchem-synonym-review.csv'
)

$ErrorActionPreference = 'Stop'
$rows = @(Import-Csv -LiteralPath $Catalogue -Delimiter '|')
if (!$rows.Count -or !($rows[0].PSObject.Properties.Name -contains 'pubchem_cid')) {
    throw 'Expected the pipe-delimited identities catalogue.'
}

$completed = @{}
if (Test-Path -LiteralPath $Output) {
    foreach ($result in (Import-Csv -LiteralPath $Output)) {
        if ($result.status -ne 'request error') {
            $completed["$($result.pubchem_cid)/$($result.inchi_key)"] = $true
        }
    }
}

foreach ($row in $rows) {
    if ($row.pubchem_cid -notmatch '^[1-9][0-9]*$') { continue }
    $identity = "$($row.pubchem_cid)/$($row.inchi_key)"
    if ($completed.ContainsKey($identity)) { continue }

    $status = 'ok'
    $detail = ''
    $synonyms = @()
    try {
        $uri = "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/$($row.pubchem_cid)/synonyms/JSON"
        $response = Invoke-RestMethod -Uri $uri -TimeoutSec 30
        $record = @($response.InformationList.Information | Where-Object { [string]$_.CID -eq $row.pubchem_cid })
        if ($record.Count -ne 1) { throw 'Unexpected CID in the PubChem response.' }
        $known = @($row.name) + @($row.aliases -split ';')
        $synonyms = @($record[0].Synonym | Where-Object { $_ -and $known -notcontains $_ } | Select-Object -Unique)
    } catch {
        $status = 'request error'
        $detail = $_.Exception.Message
    }

    [pscustomobject]@{
        name = $row.name
        inchi_key = $row.inchi_key
        pubchem_cid = $row.pubchem_cid
        status = $status
        candidates_json = ConvertTo-Json -InputObject $synonyms -Compress
        detail = $detail
    } | Export-Csv -LiteralPath $Output -Append -NoTypeInformation -Encoding UTF8

    Start-Sleep -Milliseconds 350
}
