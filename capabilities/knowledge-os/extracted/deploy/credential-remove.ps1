<#
.SYNOPSIS
credential-remove.ps1 -- the credential broker's LIFECYCLE operation (design brief
harness-v3.0/specs/credential-broker-design-brief-2026-07-23.md, added round-1
fold: eliminating staging files is not the same as eliminating credential
lifecycle deletion -- vault entries still need removal).

.DESCRIPTION
Deletes one named entry from Windows Credential Manager and echoes ONLY the name
plus a reminder that this operation removes the LOCAL copy this broker holds --
it does not, and cannot, revoke or rotate the credential at the remote
provider. That remains a separate operator act, per the design brief's
rotation-playbook discipline (each binding's `rotates_at` field names where).

Idempotent by design: removing a name that is not present in the vault is not an
error -- it is reported plainly and exits 0, so a cleanup script (or an operator
re-running this twice by habit) never has to special-case "already gone".

SECURITY INVARIANT: this script never reads or reports the credential's value.
CredDelete removes the vault entry by name; nothing about the entry's contents
is ever retrieved.

.PARAMETER Name
The credential's name, as passed to credential-store.ps1.

.PARAMETER VaultPrefix
Vault target namespace (default "rheoscope"; credential-selftest.ps1 uses
"rheoscope-selftest" so it can never touch a production entry).

.PARAMETER DryRun
Report what would be removed and exit 0 without deleting anything.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Name,

    [string]$VaultPrefix = "rheoscope",

    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

if (-not ([System.Management.Automation.PSTypeName]'Rheoscope.CredVault.NativeMethods').Type) {
    Add-Type -Namespace Rheoscope.CredVault -Name NativeMethods -MemberDefinition @'
[DllImport("advapi32.dll", EntryPoint = "CredDeleteW", CharSet = CharSet.Unicode, SetLastError = true)]
public static extern bool CredDelete(string target, UInt32 type, UInt32 flags);
'@
}

$script:CRED_TYPE_GENERIC = 1
# ERROR_NOT_FOUND, the Win32 error CredDelete reports for an absent target --
# checked by number, never by locale-dependent message text.
$script:ERROR_NOT_FOUND = 1168

function Test-CredentialNameValid {
    param([string]$Name)
    return $Name -match '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$'
}

if (-not (Test-CredentialNameValid -Name $Name)) {
    # NOTE ON MESSAGE CONSTRUCTION: interpolated strings joined with '+', never
    # '-f' across a '+'-joined expression -- '-f' binds tighter than '+' in
    # PowerShell, so a trailing '-f' only formats the LAST segment and leaves
    # every earlier '{0}' printed literally. Caught empirically smoke-testing
    # credential-store.ps1; kept out of this file from the start.
    Write-Error ("REFUSED: credential name '$Name' is invalid -- names must be " +
        "1-64 characters, start with a letter or digit, and contain only " +
        "letters, digits, '.', '_', '-'.")
    exit 1
}

$target = "$VaultPrefix/$Name"

if ($DryRun) {
    Write-Output ("DRY RUN: would remove credential '$Name' (vault target " +
        "'$target'). Nothing deleted.")
    exit 0
}

$ok = [Rheoscope.CredVault.NativeMethods]::CredDelete($target, $script:CRED_TYPE_GENERIC, 0)
if ($ok) {
    Write-Output ("Removed credential '$Name' (vault target '$target'). " +
        "Reminder: this deletes the LOCAL copy only. If '$Name' still exists " +
        "at the remote provider, rotating or revoking it there is a separate " +
        "operator act -- this broker never talks to the provider on your " +
        "behalf for that.")
    exit 0
}

$err = [System.Runtime.InteropServices.Marshal]::GetLastWin32Error()
if ($err -eq $script:ERROR_NOT_FOUND) {
    Write-Output ("Credential '$Name' (vault target '$target') was not found " +
        "-- nothing to remove. (Idempotent: removing an already-absent " +
        "credential is not an error.)")
    exit 0
}

Write-Error "REFUSED: could not remove credential '$Name' (Win32 error $err)."
exit 1
