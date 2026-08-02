<#
.SYNOPSIS
credential-store.ps1 -- the credential broker's ENTRY operation (design brief
harness-v3.0/specs/credential-broker-design-brief-2026-07-23.md).

.DESCRIPTION
Writes one credential (username + password) into Windows Credential Manager, the
OS-native, DPAPI-encrypted-at-rest vault -- no plaintext file is ever created, so
there is no staging file to shred (the brief's whole point: the shredding problem
is an artifact of file-based improvisation, not a real requirement). The operator's
hands raise the OS `Get-Credential` popup; this script never sees the value typed
into it as anything other than a SecureString, and echoes ONLY the credential's
NAME plus a confirmation. Re-running with the same -Name overwrites (rotation =
re-run), which is also why this script never prints "did that name already exist" --
the answer would be the first bit of information this broker ever leaked about a
name's prior state, and the operator does not need it: overwrite is always correct
under CredWrite's REPLACE_EXISTING semantics.

SECURITY INVARIANT (violating this fails the whole broker, not just this script):
the credential VALUE never appears in stdout, stderr, any log, any file, or any
error message -- not masked, not truncated, not hex. Only the NAME is ever echoed.
Values never pass as command-line arguments (argv is visible in process listings);
they pass as a SecureString from Get-Credential, or as stdin lines under -FromPipe.

.PARAMETER Name
The credential's name (letters, digits, '.', '_', '-'; 1-64 chars, must start with
a letter or digit). Becomes the vault target `<VaultPrefix>/<Name>`.

.PARAMETER FromPipe
TEST-ONLY SEAM: read username and password as two lines from stdin instead of
raising the `Get-Credential` popup. This exists solely so credential-selftest.ps1
can drive a store round-trip hermetically and non-interactively -- the OPERATOR
path is always the popup. Values still never touch argv under this switch; they
arrive over stdin, exactly the channel the security invariants require. A stray
human running this switch by hand gets the same result the self-test gets: two
lines are read and stored, no popup, no confirmation prompt -- documented as
test-only precisely because it removes the "the operator's own hands typed this"
property the popup path guarantees.

.PARAMETER VaultPrefix
Vault target namespace. Production default is "rheoscope" (targets look like
"rheoscope/api-admin"). credential-selftest.ps1 overrides this to
"rheoscope-selftest" so its fixtures can NEVER collide with, or accidentally
touch, a production "rheoscope/*" entry.

.PARAMETER DryRun
Report what would be stored (the vault target name only) and exit 0 without
raising a popup, reading stdin, or writing anything.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Name,

    [switch]$FromPipe,

    [string]$VaultPrefix = "rheoscope",

    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------- native vault access
#
# Windows Credential Manager via P/Invoke advapi32 (CredWrite) through Add-Type --
# no third-party modules, no dependency beyond the OS itself. Guarded so this block
# can run twice in one PowerShell process (e.g. a test harness that dot-sources or
# re-imports) without an "already exists" Add-Type error.
if (-not ([System.Management.Automation.PSTypeName]'Rheoscope.CredVault.NativeMethods').Type) {
    Add-Type -Namespace Rheoscope.CredVault -Name NativeMethods -MemberDefinition @'
[StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
public struct CREDENTIAL {
    public UInt32 Flags;
    public UInt32 Type;
    public string TargetName;
    public string Comment;
    public System.Runtime.InteropServices.ComTypes.FILETIME LastWritten;
    public UInt32 CredentialBlobSize;
    public IntPtr CredentialBlob;
    public UInt32 Persist;
    public UInt32 AttributeCount;
    public IntPtr Attributes;
    public string TargetAlias;
    public string UserName;
}

[DllImport("advapi32.dll", EntryPoint = "CredWriteW", CharSet = CharSet.Unicode, SetLastError = true)]
public static extern bool CredWrite(ref CREDENTIAL userCredential, UInt32 flags);
'@
}

# CRED_TYPE_GENERIC = 1 (arbitrary blob under an operator-chosen name -- the only
# type this broker uses). CRED_PERSIST_LOCAL_MACHINE = 2 (survives reboots, scoped
# to this Windows login via DPAPI -- the brief's chosen persistence tier).
$script:CRED_TYPE_GENERIC = 1
$script:CRED_PERSIST_LOCAL_MACHINE = 2

function Get-VaultTargetName {
    param([string]$Name, [string]$Prefix)
    return "$Prefix/$Name"
}

function Test-CredentialNameValid {
    # Charset restriction shared with credential-use.ps1 / credential-remove.ps1 /
    # the credential-bindings.yaml parser: a name becomes half of a vault target
    # string and the key a binding entry matches on, so it is kept to an
    # unambiguous, un-injectable charset rather than accepted verbatim.
    param([string]$Name)
    return $Name -match '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$'
}

function Test-InteractiveSessionAvailable {
    # Get-Credential's native popup does NOT reliably self-refuse under
    # -NonInteractive -- verified empirically while building this broker: a
    # console-host `-NonInteractive` invocation still attempted to raise the
    # Windows Credential UI dialog and hung waiting for a click nobody could
    # give it, rather than throwing the way the documented behavior implies.
    # So this script enforces the "never prompt when nothing can answer" intent
    # ITSELF, with two independent signals, either sufficient to refuse before
    # Get-Credential is ever called: (1) this process was itself launched with
    # -NonInteractive -- the operator/automation's own explicit statement that
    # no prompt should be attempted; (2) UserInteractive is false -- a service
    # or other non-desktop context where no popup could be shown to anyone.
    foreach ($a in [Environment]::GetCommandLineArgs()) {
        if ($a -match '(?i)^-NonInteractive$') { return $false }
    }
    if (-not [Environment]::UserInteractive) { return $false }
    return $true
}

function Write-VaultCredential {
    # Writes (TargetName, UserName, SecurePassword) into Credential Manager.
    # The password NEVER exists as a managed System.String in this function: it is
    # decrypted straight from the SecureString into an unmanaged buffer via
    # SecureStringToGlobalAllocUnicode (the same primitive .NET's own credential
    # UIs use), handed to CredWrite by pointer, and the buffer is zeroed and freed
    # in a finally block regardless of outcome. This is best-effort memory hygiene,
    # not a guarantee -- see the build report for the honest limit of what this
    # can and cannot promise.
    param(
        [string]$TargetName,
        [string]$UserName,
        [System.Security.SecureString]$SecurePassword
    )
    $bstr = [IntPtr]::Zero
    try {
        $bstr = [System.Runtime.InteropServices.Marshal]::SecureStringToGlobalAllocUnicode($SecurePassword)
        # Byte length EXCLUDING the trailing UTF-16 null terminator SecureStringTo-
        # GlobalAllocUnicode always appends -- CredWrite stores exactly this many
        # bytes, so the value read back later is the password, not the password
        # plus a null character.
        $byteLen = $SecurePassword.Length * 2

        $cred = New-Object Rheoscope.CredVault.NativeMethods+CREDENTIAL
        $cred.Flags = 0
        $cred.Type = $script:CRED_TYPE_GENERIC
        $cred.TargetName = $TargetName
        $cred.Comment = "rheoscope credential broker"
        $cred.CredentialBlobSize = [UInt32]$byteLen
        $cred.CredentialBlob = $bstr
        $cred.Persist = $script:CRED_PERSIST_LOCAL_MACHINE
        $cred.AttributeCount = 0
        $cred.Attributes = [IntPtr]::Zero
        $cred.TargetAlias = $null
        $cred.UserName = $UserName

        $ok = [Rheoscope.CredVault.NativeMethods]::CredWrite([ref]$cred, 0)
        if (-not $ok) {
            $err = [System.Runtime.InteropServices.Marshal]::GetLastWin32Error()
            return @{ Ok = $false; Error = "CredWrite failed, Win32 error $err" }
        }
        return @{ Ok = $true; Error = $null }
    } finally {
        if ($bstr -ne [IntPtr]::Zero) {
            # Zeroes the unmanaged buffer before freeing it -- the point at which
            # the plaintext password bytes stop existing anywhere this process
            # controls.
            [System.Runtime.InteropServices.Marshal]::ZeroFreeGlobalAllocUnicode($bstr)
        }
    }
}

# --------------------------------------------------------------------------- main
if (-not (Test-CredentialNameValid -Name $Name)) {
    # NOTE ON MESSAGE CONSTRUCTION THROUGHOUT THIS FILE: interpolated strings
    # ("...$Name...") joined with '+', never the '-f' format operator across a
    # '+'-joined expression. PowerShell's '-f' binds TIGHTER than '+', so
    # ("a " + "b {0}" -f $x) silently formats only the LAST segment and leaves
    # every earlier '{0}' printed as a literal placeholder -- caught empirically
    # while smoke-testing this script, not something to reintroduce by pattern-
    # matching the earlier draft.
    Write-Error ("REFUSED: credential name '$Name' is invalid -- names must be " +
        "1-64 characters, start with a letter or digit, and contain only " +
        "letters, digits, '.', '_', '-'. (This keeps the name safe to embed in " +
        "a vault target string and a bindings-file key without ambiguity.)")
    exit 1
}

$target = Get-VaultTargetName -Name $Name -Prefix $VaultPrefix

if ($DryRun) {
    Write-Output ("DRY RUN: would store a credential named '$Name' at vault " +
        "target '$target'. No popup raised, no stdin read, nothing written.")
    exit 0
}

$username = $null
$securePassword = $null

if ($FromPipe) {
    # TEST-ONLY seam -- see the .PARAMETER FromPipe doc above.
    $u = [Console]::In.ReadLine()
    $pLine = [Console]::In.ReadLine()
    if ($null -eq $u -or $null -eq $pLine) {
        Write-Error ("REFUSED: -FromPipe expects exactly two lines on stdin " +
            "(username, then password) and did not receive both.")
        exit 1
    }
    $username = $u
    $securePassword = ConvertTo-SecureString -String $pLine -AsPlainText -Force
    # Best-effort: drop the reference to the plaintext line so nothing here holds
    # it deliberately past this point. PowerShell strings are immutable and the
    # runtime does not guarantee the underlying memory is scrubbed or that no
    # other copy exists (string interning, GC compaction) -- an honest residual of
    # the -FromPipe test seam, recorded in the build report, not papered over.
    $pLine = $null
} else {
    if (-not (Test-InteractiveSessionAvailable)) {
        Write-Error ("REFUSED: cannot raise the credential-store popup -- this " +
            "session is non-interactive (either launched with -NonInteractive, " +
            "or has no interactive desktop session). credential-store always " +
            "requires the operator's own hands at an interactive console (or " +
            "-FromPipe, test-only, see the script's own header).")
        exit 1
    }
    try {
        $cred = Get-Credential -Message ("Enter the credential to store as " +
            "'$Name' in the rheoscope vault (target '$target').")
    } catch {
        $exMsg = $_.Exception.Message
        Write-Error ("REFUSED: could not raise the credential prompt ($exMsg). " +
            "This usually means the session is non-interactive; credential-store " +
            "requires an interactive console (or -FromPipe, test-only, see the " +
            "script's own header).")
        exit 1
    }
    if ($null -eq $cred) {
        Write-Error "REFUSED: no credential entered (popup cancelled)."
        exit 1
    }
    $username = $cred.UserName
    $securePassword = $cred.Password
}

if ([string]::IsNullOrWhiteSpace($username)) {
    Write-Error "REFUSED: no username entered."
    exit 1
}
if ($securePassword.Length -eq 0) {
    Write-Error "REFUSED: no password entered (an empty credential is never stored)."
    exit 1
}

$result = Write-VaultCredential -TargetName $target -UserName $username -SecurePassword $securePassword
if (-not $result.Ok) {
    $writeErr = $result.Error
    Write-Error "REFUSED: could not write credential '$Name' to the vault ($writeErr)."
    exit 1
}

Write-Output "Stored credential '$Name' (vault target '$target')."
exit 0
