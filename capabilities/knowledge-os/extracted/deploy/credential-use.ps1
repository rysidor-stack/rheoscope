<#
.SYNOPSIS
credential-use.ps1 -- the credential broker's USE operation, THE broker (design
brief harness-v3.0/specs/credential-broker-design-brief-2026-07-23.md).

.DESCRIPTION
Takes a credential NAME and a DESTINATION, checks the operator-pinned binding
config, fetches (or prompts for, under -Prompt) the value, and injects it
DIRECTLY into the consuming channel -- an HTTP Authorization header on a request
this script makes itself, or a child process's stdin. The value never appears in
this script's own stdout, stderr, logs, or any error message; only the request's
or child's RESULT is returned. Sessions address credentials by name, never by
value -- this script is the only code path that ever touches one.

CHECK ORDER (deliberately fixed, cheapest and most policy-load-bearing first, all
of them BEFORE the vault or the popup is ever touched -- an unauthorized call
never gets far enough to expose anything):
  1. financial-domain denylist (an ALWAYS-ON policy check; not gated by whether
     the name/destination happen to be bound -- see the design brief's "Excluded
     entirely" section: this is best-effort defense-in-depth, never the control)
  2. bindings loaded and non-empty (absent / unparseable / malformed / any single
     invalid entry => the WHOLE set is empty => refuse, for every name)
  3. the requested NAME has a binding entry
  4. the requested DESTINATION matches one of that name's destination patterns
Only after all four pass does this script ever read the vault or raise a prompt.

FINAL-RESOLVED-DESTINATION BINDING (round-1 fold, load-bearing): in -Mode http,
redirects are sent with -MaximumRedirection semantics DISABLED at the .NET layer
(AllowAutoRedirect = $false). A 3xx response is NOT followed; it is reported as a
refusal naming the redirect target, because a redirect chain that lands off the
allow-listed pattern would otherwise exfiltrate the credential on an otherwise-
sanctioned path.

SECURITY INVARIANT: the credential VALUE never appears in stdout, stderr, any
log, any file, or any error message -- not masked, not truncated, not hex. It is
delivered ONLY into the consuming channel: an HTTP Authorization header this
script constructs and sends itself (http mode), or a child process's stdin
stream (stdin mode). It never becomes a command-line argument to anything.

.PARAMETER Name
The credential's name, as passed to credential-store.ps1.

.PARAMETER Destination
The destination string a binding pattern is matched against verbatim (a URL for
http mode; any operator-meaningful descriptor for stdin mode -- see
credential-bindings.yaml's schema comment for the pattern syntax).

.PARAMETER Mode
'http' (default): this script makes the HTTP request itself, injecting an
Authorization header, and returns the response status + body. 'stdin': starts
-Command (with -Arguments) and writes "username`npassword`n" to its stdin.

.PARAMETER Command
-Mode stdin only: the executable to start.

.PARAMETER Arguments
-Mode stdin only: arguments to -Command, passed as a true argument ARRAY (never
a single shell-joined string) so no value here is ever re-parsed by a shell.

.PARAMETER Method
-Mode http only: HTTP method (default GET).

.PARAMETER Body
-Mode http only: optional request body string.

.PARAMETER ContentType
-Mode http only: optional Content-Type header for -Body.

.PARAMETER AuthScheme
-Mode http only: 'Basic' (default; Authorization: Basic base64(username:password))
or 'Bearer' (Authorization: Bearer <password>; the vault entry's username is
unused -- for API keys stored with the key as the password field).

.PARAMETER Prompt
Tier B: raise the `Get-Credential` popup at THIS use instead of reading the
vault. Nothing is stored. The same binding checks above still apply in full --
Tier B changes where the value comes from, never whether the destination is
authorized.

.PARAMETER VaultPrefix
Vault target namespace (default "rheoscope"; credential-selftest.ps1 overrides
to "rheoscope-selftest").

.PARAMETER BindingsPath
Path to the bindings YAML (default deploy/credential-bindings.yaml next to this
script; credential-selftest.ps1 overrides to its own temp fixture -- the live
file is never read by the self-test).

.PARAMETER DryRun
Run every authorization check (financial denylist, bindings-empty, name-bound,
destination-pattern) and report PASS/REFUSE without ever touching the vault, the
popup, or the network/child process. This is also how credential-selftest.ps1
proves matching/refusal behavior without needing a live credential or a live
network endpoint.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Name,

    [Parameter(Mandatory = $true)]
    [string]$Destination,

    [ValidateSet('http', 'stdin')]
    [string]$Mode = 'http',

    [string]$Command,

    [string[]]$Arguments = @(),

    [string]$Method = 'GET',

    [string]$Body,

    [string]$ContentType,

    [ValidateSet('Basic', 'Bearer')]
    [string]$AuthScheme = 'Basic',

    [switch]$Prompt,

    [string]$VaultPrefix = "rheoscope",

    [string]$BindingsPath,

    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

if (-not $BindingsPath) {
    $BindingsPath = Join-Path $PSScriptRoot 'credential-bindings.yaml'
}

# ---------------------------------------------------------------- native vault access
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

[DllImport("advapi32.dll", EntryPoint = "CredReadW", CharSet = CharSet.Unicode, SetLastError = true)]
public static extern bool CredRead(string target, UInt32 type, UInt32 flags, out IntPtr credentialPtr);

[DllImport("advapi32.dll", EntryPoint = "CredFree", SetLastError = false)]
public static extern void CredFree(IntPtr cred);
'@
}

$script:CRED_TYPE_GENERIC = 1
$script:ERROR_NOT_FOUND = 1168

# ------------------------------------------------------------------ name / matching
function Test-CredentialNameValid {
    param([string]$Name)
    return $Name -match '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$'
}

function Test-InteractiveSessionAvailable {
    # Same guard as credential-store.ps1 -- see that script's copy of this
    # function for why it exists (Get-Credential's native popup does not
    # reliably self-refuse under -NonInteractive; verified empirically, not
    # assumed). Duplicated rather than shared because this repo's deploy/ is a
    # flat directory of standalone scripts with no shared module, by the
    # build's own scope.
    foreach ($a in [Environment]::GetCommandLineArgs()) {
        if ($a -match '(?i)^-NonInteractive$') { return $false }
    }
    if (-not [Environment]::UserInteractive) { return $false }
    return $true
}

function Test-WildcardMatch {
    # A `*`-glob, not a regex: every regex metacharacter in the operator-authored
    # pattern is escaped EXCEPT the operator's own `*`, which alone becomes `.*`.
    # An operator writing `https://*.example.com/*` must not accidentally hand out
    # a regex-injection surface via `.` or `?` taking on unintended regex meaning.
    # Matched against the WHOLE destination string, case-insensitively (per the
    # design brief's own example) -- anchored both ends, so a pattern only
    # matches destinations it fully covers, never a substring of a longer one.
    param([string]$Pattern, [string]$Value)
    $escaped = [Regex]::Escape($Pattern)
    $regexPattern = '^' + ($escaped -replace '\\\*', '.*') + '$'
    return [Regex]::IsMatch($Value, $regexPattern, [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)
}

# ------------------------------------------------------------------ bindings loader
function ConvertFrom-BindingScalar {
    # Strips ONE matching pair of surrounding quotes (' or "). A value that OPENS
    # with a quote but does not CLOSE with the same one is ambiguous -> $null,
    # never silently kept as-is (mirrors deploy/promote-candidate.py's
    # parse_binding, same doctrine, restated for this file's own strict parser).
    param([string]$Value)
    if ($Value.Length -ge 1 -and ($Value[0] -eq "'" -or $Value[0] -eq '"')) {
        if ($Value.Length -ge 2 -and $Value[$Value.Length - 1] -eq $Value[0]) {
            return $Value.Substring(1, $Value.Length - 2)
        }
        return $null
    }
    return $Value
}

function ConvertFrom-DestinationsFlowSeq {
    # Parses a `[item1, "item2", ...]` flow sequence -- the ONLY list shape this
    # parser accepts -- into a string array, or returns $null on anything that
    # does not fit that exact shape. Splits on commas OUTSIDE quotes so a quoted
    # item may itself be empty-safe-guarded but never contain an un-escaped comma
    # ambiguity. Returns an empty array (never $null) for a syntactically valid
    # but EMPTY `[]` -- the caller decides whether zero destinations is itself
    # malformed (it does: a binding with nothing it authorizes is not a shape the
    # documented schema produces on purpose).
    param([string]$Seq)
    $s = $Seq.Trim()
    if (-not ($s.StartsWith('[') -and $s.EndsWith(']'))) { return $null }
    $inner = $s.Substring(1, $s.Length - 2).Trim()
    if ($inner -eq '') { return @() }

    $items = New-Object System.Collections.Generic.List[string]
    $cur = New-Object System.Text.StringBuilder
    $quoteChar = [char]0
    $inQuote = $false
    foreach ($ch in $inner.ToCharArray()) {
        if ($inQuote) {
            [void]$cur.Append($ch)
            if ($ch -eq $quoteChar) { $inQuote = $false }
            continue
        }
        if ($ch -eq "'" -or $ch -eq '"') {
            $inQuote = $true
            $quoteChar = $ch
            [void]$cur.Append($ch)
            continue
        }
        if ($ch -eq ',') {
            $items.Add($cur.ToString())
            [void]$cur.Clear()
            continue
        }
        [void]$cur.Append($ch)
    }
    if ($inQuote) { return $null }   # unterminated quote -- malformed
    $items.Add($cur.ToString())

    $result = New-Object System.Collections.Generic.List[string]
    foreach ($it in $items) {
        $trimmed = $it.Trim()
        if ($trimmed -eq '') { return $null }
        $u = ConvertFrom-BindingScalar -Value $trimmed
        if ($null -eq $u -or $u -eq '') { return $null }
        $result.Add($u)
    }
    # Comma-operator prefix (,$x) prevents PowerShell's automatic single/zero-
    # element array unwrapping from turning a real array into a scalar or $null
    # on its way out of this function -- a well-known PowerShell pitfall that
    # would otherwise silently corrupt a one-destination or two-destination list.
    return , $result.ToArray()
}

function Read-CredentialBindings {
    # FAIL-SAFE CONTRACT, restated for this file (same doctrine as
    # deploy/origin-config.yaml's load_origin_config / deploy/signing-config.yaml's
    # load_signing_config): file absent, unreadable, the top-level key not
    # EXACTLY `bindings:`, or ANY single entry structurally invalid (bad name
    # charset, duplicate name, missing/malformed `destinations:`, an empty
    # destinations list, an unrecognised trailing key) => the ENTIRE bindings set
    # degrades to EMPTY. A partially-honored trust config is worse than an
    # honestly empty one -- one bad entry must void every entry, not just itself.
    #
    # DELIBERATELY NOT A GENERAL YAML PARSER. This accepts ONLY the exact shape
    # documented in credential-bindings.yaml's own header comment. Strictness is
    # the point: a shape this function doesn't recognise must be malformed, never
    # partially understood.
    param([string]$Path)

    $empty = @{}
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $empty }

    try {
        $raw = Get-Content -LiteralPath $Path -Raw -Encoding UTF8 -ErrorAction Stop
    } catch {
        return $empty
    }
    if ($null -eq $raw) { $raw = '' }
    $raw = $raw -replace "^\xFEFF", ''   # strip a UTF-8 BOM Get-Content may have kept
    $rawLines = $raw -split "`r?`n"

    $nameCharset = '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$'

    # Blank and pure-comment lines (first non-whitespace char '#') never carry
    # structure and are dropped before the shape check; everything else keeps its
    # original indentation, which the shape check depends on.
    $content = New-Object System.Collections.Generic.List[string]
    foreach ($line in $rawLines) {
        $t = $line.Trim()
        if ($t -eq '' -or $t.StartsWith('#')) { continue }
        $content.Add($line)
    }
    if ($content.Count -eq 0) { return $empty }
    if ($content[0].TrimEnd() -ne 'bindings:') { return $empty }

    $entries = @{}
    $i = 1
    while ($i -lt $content.Count) {
        $nameMatch = [Regex]::Match($content[$i], '^  - name:\s*(.*)$')
        if (-not $nameMatch.Success) { return $empty }
        $name = ConvertFrom-BindingScalar -Value ($nameMatch.Groups[1].Value.Trim())
        if ($null -eq $name -or $name -eq '' -or -not ($name -match $nameCharset)) { return $empty }
        if ($entries.ContainsKey($name)) { return $empty }   # duplicate name -- ambiguous
        $i++
        if ($i -ge $content.Count) { return $empty }         # `- name:` with nothing after it

        $destMatch = [Regex]::Match($content[$i], '^    destinations:\s*(\[.*\])\s*$')
        if (-not $destMatch.Success) { return $empty }
        $destinations = ConvertFrom-DestinationsFlowSeq -Seq $destMatch.Groups[1].Value
        if ($null -eq $destinations -or $destinations.Count -eq 0) { return $empty }
        $i++

        if ($i -lt $content.Count) {
            $rotatesMatch = [Regex]::Match($content[$i], '^    rotates_at:\s*(.*)$')
            if ($rotatesMatch.Success) {
                # Documentation-only field: parsed just enough to consume the
                # line and validate it isn't itself malformed (unterminated
                # quote); its value is never read by any authorization check.
                $rv = ConvertFrom-BindingScalar -Value ($rotatesMatch.Groups[1].Value.Trim())
                if ($null -eq $rv) { return $empty }
                $i++
            }
        }

        # Anything else at 4-space-or-deeper indent that wasn't consumed above,
        # and isn't the start of the next entry, is an unrecognised key --
        # malformed, never silently skipped.
        if ($i -lt $content.Count -and $content[$i] -match '^    \S') { return $empty }

        $entries[$name] = $destinations
    }
    return $entries
}

# ------------------------------------------------------------------ financial denylist
# Best-effort, INCOMPLETE by construction and honestly labeled as such (design
# brief: "the exclusion is a policy held by people, not a detector"). This exists
# purely as defense-in-depth for the case where a financial credential was bound
# despite the operator-side policy against ever doing so -- its silence about a
# destination is NEVER a safety signal, and it must never be mistaken for the
# actual control (which is the operator never storing/binding these at all).
$script:FinancialDenylistPatterns = @(
    '*paypal.com*', '*venmo.com*', '*cash.app*', '*zellepay.com*', '*stripe.com*', '*squareup.com*',
    '*chase.com*', '*bankofamerica.com*', '*wellsfargo.com*', '*citibank.com*', '*usbank.com*',
    '*capitalone.com*', '*pnc.com*', '*tdbank.com*', '*ally.com*', '*discover.com*',
    '*visa.com*', '*mastercard.com*', '*americanexpress.com*',
    '*fidelity.com*', '*schwab.com*', '*vanguard.com*', '*etrade.com*', '*robinhood.com*', '*tdameritrade.com*',
    '*coinbase.com*', '*binance.com*', '*kraken.com*', '*gemini.com*',
    '*irs.gov*', '*ssa.gov*', '*treasurydirect.gov*', '*uscis.gov*'
)

function Get-FinancialDenylistHit {
    param([string]$Destination)
    foreach ($pattern in $script:FinancialDenylistPatterns) {
        if (Test-WildcardMatch -Pattern $pattern -Value $Destination) { return $pattern }
    }
    return $null
}

# ------------------------------------------------------------------ vault read
function Read-VaultCredential {
    # Returns @{ Found; UserName; SecurePassword } and NEVER a plaintext password
    # string. CredentialBlob bytes are copied out, converted to a SecureString
    # directly, and the intermediate managed byte array is zeroed before this
    # function returns -- the same "minimize the plaintext window" discipline
    # credential-store.ps1 applies on the write side.
    param([string]$Target)

    $ptr = [IntPtr]::Zero
    try {
        $ok = [Rheoscope.CredVault.NativeMethods]::CredRead($Target, $script:CRED_TYPE_GENERIC, 0, [ref]$ptr)
        if (-not $ok) {
            return @{ Found = $false; UserName = $null; SecurePassword = $null }
        }
        $cred = [System.Runtime.InteropServices.Marshal]::PtrToStructure(
            $ptr, [type]'Rheoscope.CredVault.NativeMethods+CREDENTIAL')

        $blobBytes = New-Object byte[] ($cred.CredentialBlobSize)
        if ($cred.CredentialBlobSize -gt 0) {
            [System.Runtime.InteropServices.Marshal]::Copy($cred.CredentialBlob, $blobBytes, 0, $cred.CredentialBlobSize)
        }
        $secure = New-Object System.Security.SecureString
        # Bytes were written as UTF-16LE by credential-store.ps1's
        # SecureStringToGlobalAllocUnicode path -- decode two bytes at a time
        # back into UTF-16 code units, feeding the SecureString char-by-char so a
        # plaintext managed String of the password is never materialized here.
        for ($bi = 0; $bi -lt $blobBytes.Length - 1; $bi += 2) {
            $ch = [BitConverter]::ToChar($blobBytes, $bi)
            $secure.AppendChar($ch)
        }
        $secure.MakeReadOnly()
        [Array]::Clear($blobBytes, 0, $blobBytes.Length)

        return @{ Found = $true; UserName = $cred.UserName; SecurePassword = $secure }
    } finally {
        if ($ptr -ne [IntPtr]::Zero) {
            [Rheoscope.CredVault.NativeMethods]::CredFree($ptr)
        }
    }
}

function ConvertFrom-SecureStringPlain {
    # The one unavoidable step: a value delivered into a consuming channel
    # (an HTTP header this script builds, or a child's stdin stream) must exist
    # in plaintext at the moment of delivery -- there is no channel-native way to
    # hand a SecureString to an HTTP header or a pipe. This function is called
    # ONLY at the point of use, and its result is passed straight to the
    # consuming channel and then discarded; see the build report for the honest
    # limit of "discarded" in a managed runtime (no guaranteed scrub, possible
    # GC-compaction copies).
    param([System.Security.SecureString]$SecureString)
    $bstr = [IntPtr]::Zero
    try {
        $bstr = [System.Runtime.InteropServices.Marshal]::SecureStringToGlobalAllocUnicode($SecureString)
        return [System.Runtime.InteropServices.Marshal]::PtrToStringUni($bstr)
    } finally {
        if ($bstr -ne [IntPtr]::Zero) {
            [System.Runtime.InteropServices.Marshal]::ZeroFreeGlobalAllocUnicode($bstr)
        }
    }
}

# ------------------------------------------------------------------ argv quoting
function ConvertTo-Win32ArgumentString {
    # The CommandLineToArgvW-compatible quoting algorithm (the same rules
    # .NET's own ProcessStartInfo.ArgumentList uses internally on newer runtimes;
    # PowerShell 5.1 runs on .NET Framework, which has no ArgumentList property,
    # so this is hand-rolled here). Needed because -Arguments values are
    # OPERATOR-SUPPLIED and must reach the child EXACTLY as given -- a naive
    # join-with-spaces would let an argument containing a space or quote silently
    # split into two arguments or break the child's own parsing.
    param([string[]]$ArgList)
    $sb = New-Object System.Text.StringBuilder
    foreach ($arg in $ArgList) {
        if ($sb.Length -gt 0) { [void]$sb.Append(' ') }
        if ($null -eq $arg -or $arg.Length -eq 0) {
            [void]$sb.Append('""')
            continue
        }
        $needsQuotes = ($arg -match '[\s"]')
        if ($needsQuotes) { [void]$sb.Append('"') }
        $i = 0
        while ($i -lt $arg.Length) {
            $backslashes = 0
            while ($i -lt $arg.Length -and $arg[$i] -eq '\') { $backslashes++; $i++ }
            if ($i -eq $arg.Length) {
                [void]$sb.Append('\' * ($backslashes * 2))
            } elseif ($arg[$i] -eq '"') {
                [void]$sb.Append('\' * ($backslashes * 2 + 1))
                [void]$sb.Append('"')
                $i++
            } else {
                [void]$sb.Append('\' * $backslashes)
                [void]$sb.Append($arg[$i])
                $i++
            }
        }
        if ($needsQuotes) { [void]$sb.Append('"') }
    }
    return $sb.ToString()
}

# =================================================================== main
if (-not (Test-CredentialNameValid -Name $Name)) {
    Write-Error ("REFUSED: credential name '$Name' is invalid -- names must be " +
        "1-64 characters, start with a letter or digit, and contain only " +
        "letters, digits, '.', '_', '-'.")
    exit 1
}

# ---- check 1: financial denylist -- ALWAYS on, independent of any binding ----
$finHit = Get-FinancialDenylistHit -Destination $Destination
if ($finHit) {
    Write-Error ("REFUSED: destination '$Destination' matches the financial-" +
        "services denylist pattern '$finHit'. Financial credentials (banking, " +
        "cards, brokerage, crypto exchanges, government ID/tax) are excluded " +
        "from this broker BY POLICY, not by this detector -- the operator " +
        "enters those personally, always, no exceptions. This denylist is " +
        "DEFENSE-IN-DEPTH ONLY: it is a best-effort, admittedly incomplete list " +
        "of known domains, and a destination NOT on this list is not thereby " +
        "safe. If '$Name' is a financial credential, it should never have been " +
        "stored or bound in this broker at all.")
    exit 1
}

# ---- check 2: bindings loaded and non-empty ----
$bindings = Read-CredentialBindings -Path $BindingsPath
if ($bindings.Count -eq 0) {
    Write-Error ("REFUSED: the credential bindings set is EMPTY (bindings file " +
        "'$BindingsPath' is absent, unreadable, not in the exact documented " +
        "shape, or contains at least one structurally invalid entry -- ANY " +
        "single invalid entry voids the WHOLE set, by design). Every " +
        "credential-use call refuses while the bindings set is empty; ask the " +
        "operator to add or fix the binding for '$Name' in that file.")
    exit 1
}

# ---- check 3: name is bound ----
if (-not $bindings.ContainsKey($Name)) {
    Write-Error ("REFUSED: credential '$Name' has no entry in the bindings " +
        "file -- an unbound name is never usable, however valid the vault " +
        "entry (or -Prompt value) behind it might be. Ask the operator to add " +
        "a binding for '$Name' with the destination pattern(s) it should be " +
        "allowed against.")
    exit 1
}

# ---- check 4: destination matches one of the name's patterns ----
$patterns = $bindings[$Name]
$matched = $null
foreach ($p in $patterns) {
    if (Test-WildcardMatch -Pattern $p -Value $Destination) { $matched = $p; break }
}
if (-not $matched) {
    $patternList = $patterns -join ', '
    Write-Error ("REFUSED: destination '$Destination' does not match any bound " +
        "pattern for credential '$Name' (bound patterns: $patternList). This " +
        "is the injection defense: a session cannot point an existing " +
        "credential at a new destination -- the destination set is pinned by " +
        "the operator, out-of-band, in credential-bindings.yaml.")
    exit 1
}

if ($Mode -eq 'stdin' -and [string]::IsNullOrWhiteSpace($Command)) {
    Write-Error "REFUSED: -Mode stdin requires -Command."
    exit 1
}

if ($DryRun) {
    Write-Output ("DRY RUN: all authorization checks PASSED for credential " +
        "'$Name' against destination '$Destination' (matched pattern " +
        "'$matched', mode '$Mode'). No vault read, no prompt, no network call, " +
        "no child process started.")
    exit 0
}

# ---- fetch the value: Tier B prompt, or the vault ----
$username = $null
$securePassword = $null

if ($Prompt) {
    if (-not (Test-InteractiveSessionAvailable)) {
        Write-Error ("REFUSED: cannot raise the Tier B credential prompt -- " +
            "this session is non-interactive (either launched with " +
            "-NonInteractive, or has no interactive desktop session). -Prompt " +
            "always requires the operator's own hands at an interactive " +
            "console. (This refusal fires AFTER all binding/policy checks " +
            "above passed -- '$Name' against '$Destination' is authorized; " +
            "only the interactive prompt itself is unavailable right now.)")
        exit 1
    }
    try {
        $cred = Get-Credential -Message ("Tier B: enter the credential to use " +
            "as '$Name' against '$Destination'. Nothing will be stored.")
    } catch {
        $exMsg = $_.Exception.Message
        Write-Error ("REFUSED: could not raise the Tier B credential prompt " +
            "($exMsg). This usually means the session is non-interactive; " +
            "-Prompt requires an interactive console.")
        exit 1
    }
    if ($null -eq $cred) {
        Write-Error "REFUSED: no credential entered (Tier B popup cancelled)."
        exit 1
    }
    $username = $cred.UserName
    $securePassword = $cred.Password
} else {
    $target = "$VaultPrefix/$Name"
    $vaultResult = Read-VaultCredential -Target $target
    if (-not $vaultResult.Found) {
        Write-Error ("REFUSED: credential '$Name' is bound and the destination " +
            "matches, but no vault entry exists at target '$target'. Ask the " +
            "operator to run credential-store.ps1 -Name $Name once (or use " +
            "-Prompt for a Tier B one-time credential).")
        exit 1
    }
    $username = $vaultResult.UserName
    $securePassword = $vaultResult.SecurePassword
}

# =================================================================== delivery
if ($Mode -eq 'stdin') {
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $Command
    $psi.Arguments = ConvertTo-Win32ArgumentString -ArgList $Arguments
    $psi.UseShellExecute = $false
    $psi.RedirectStandardInput = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    # Pinned OUTPUT encoding (not the console/OEM default) so the child's own
    # stdout/stderr decode predictably. StandardInputEncoding has no equivalent
    # here: it is a .NET Core-only ProcessStartInfo member and this script runs
    # under PowerShell 5.1 / .NET Framework, where it does not exist -- input
    # encoding is instead pinned explicitly below by wrapping StandardInput's
    # raw BaseStream in our own UTF-8 StreamWriter.
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    $psi.StandardOutputEncoding = $utf8NoBom
    $psi.StandardErrorEncoding = $utf8NoBom

    # LOAD-BEARING, found only by running the delivery end-to-end and inspecting
    # the RAW BYTES the child actually received (byte-for-byte, not decoded
    # text): .NET's Process class creates the child's StandardInput StreamWriter
    # using THIS PROCESS's OWN [Console]::InputEncoding, and merely ACCESSING
    # that writer's .BaseStream property (the only way .NET Framework exposes
    # the raw stream) flushes that writer's pending preamble onto the pipe --
    # even though nothing was ever explicitly written through it. On a host
    # whose console input encoding is UTF-8 WITH the byte-order-mark flag set
    # (common default), that preamble is three bytes: EF BB BF, a UTF-8 BOM,
    # silently prepended to the FIRST line the child receives -- BEFORE our own
    # no-BOM StreamWriter ever writes a byte. A consumer reading text (most
    # PowerShell/`[Console]::In` readers) may silently swallow a recognized BOM
    # and hide this; a consumer reading or hashing raw bytes (exactly what this
    # broker promises delivers the credential verbatim) would not, and three
    # unexpected leading bytes on the username line is not "verbatim". Setting
    # THIS PROCESS's own Console.InputEncoding to a no-BOM UTF-8 encoding before
    # the child starts makes .NET construct that internal writer with a preamble
    # of zero bytes, so accessing .BaseStream has nothing to flush. Confirmed by
    # dumping the child's raw received bytes with and without this line.
    try { [Console]::InputEncoding = $utf8NoBom } catch { }

    $proc = New-Object System.Diagnostics.Process
    $proc.StartInfo = $psi
    try {
        [void]$proc.Start()
    } catch {
        $exMsg = $_.Exception.Message
        Write-Error "REFUSED: could not start '$Command' ($exMsg)."
        exit 1
    }

    # Reads started BEFORE writing stdin: a child that writes enough output to
    # fill its OS pipe buffer before we finish writing stdin would otherwise
    # deadlock (classic sync-read-after-write hazard). Output is captured here
    # and only ever written out AFTER the credential has left this process's
    # hands -- these buffers hold the CHILD's own output, which is the child's
    # responsibility not to leak, not this broker's.
    #
    # ReadToEndAsync() Tasks, NOT Register-ObjectEvent/BeginOutputReadLine --
    # found the hard way, via a reproducible flake while building this
    # broker's own self-test: PowerShell 5.1's Register-ObjectEvent queues each
    # OutputDataReceived/ErrorDataReceived line as a separate engine event, and
    # for a message that spans several lines in quick succession, the engine
    # can invoke those queued -Action scriptblocks OUT OF THE ORDER the lines
    # were actually received in -- silently reordering a multi-line message
    # before it ever reaches this script's own output. A Task-based
    # ReadToEndAsync() has no such queue: it reads the stream's bytes strictly
    # in arrival order into one buffer, so what this script relays is
    # byte-order-faithful to what the child actually wrote, every time.
    $stdoutTask = $proc.StandardOutput.ReadToEndAsync()
    $stderrTask = $proc.StandardError.ReadToEndAsync()

    $plainPassword = $null
    # Our OWN StreamWriter over the raw BaseStream, UTF-8-no-BOM, LF line
    # endings -- deterministic and independent of console codepage or CRLF
    # translation, so a test harness computing an expected hash over
    # "username`npassword`n" agrees byte-for-byte with what the child receives.
    $stdinWriter = New-Object System.IO.StreamWriter($proc.StandardInput.BaseStream, $utf8NoBom)
    $stdinWriter.NewLine = "`n"
    try {
        $stdinWriter.WriteLine($username)
        $plainPassword = ConvertFrom-SecureStringPlain -SecureString $securePassword
        $stdinWriter.WriteLine($plainPassword)
        $stdinWriter.Flush()
    } finally {
        # The ONLY place the password exists as a managed string in this mode,
        # and only for the two lines above. Overwritten immediately; see the
        # build report for what this can and cannot guarantee in a managed
        # runtime.
        if ($plainPassword) { $plainPassword = [string]::new([char]0, $plainPassword.Length) }
        $plainPassword = $null
        $stdinWriter.Close()
    }

    $proc.WaitForExit()
    # The process has exited, so both streams have hit EOF and these Tasks are
    # either already complete or complete within moments; the timeout here is
    # only a defensive backstop against a hung Task, never expected to fire.
    [System.Threading.Tasks.Task]::WaitAll(@($stdoutTask, $stderrTask), 15000) | Out-Null
    $stdoutText = if ($stdoutTask.IsCompleted) { $stdoutTask.Result } else { '' }
    $stderrText = if ($stderrTask.IsCompleted) { $stderrTask.Result } else { '' }

    Write-Output "credential-use: '$Command' exited with code $($proc.ExitCode)."
    if ($stdoutText.Length -gt 0) {
        Write-Output "----- child stdout -----"
        Write-Output $stdoutText.TrimEnd("`n")
    }
    if ($stderrText.Length -gt 0) {
        Write-Output "----- child stderr -----"
        Write-Output $stderrText.TrimEnd("`n")
    }
    exit $proc.ExitCode
}

# ---- http mode ----
$plainPassword = $null
$authValue = $null
try {
    $plainPassword = ConvertFrom-SecureStringPlain -SecureString $securePassword
    if ($AuthScheme -eq 'Basic') {
        $pair = "$($username):$($plainPassword)"
        $b64 = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($pair))
        $authValue = "Basic $b64"
        $pair = [string]::new([char]0, $pair.Length)
    } else {
        $authValue = "Bearer $plainPassword"
    }
} finally {
    if ($plainPassword) { $plainPassword = [string]::new([char]0, $plainPassword.Length) }
    $plainPassword = $null
}

$req = [System.Net.HttpWebRequest]::Create($Destination)
$req.Method = $Method
# THE ROUND-1 FOLD, MECHANICALLY: redirects are never followed by this client.
# .NET does not throw for a 3xx response when AllowAutoRedirect is false -- it
# hands back the 3xx response itself, Location header intact and unfetched. That
# is exactly the property this check needs: report the redirect target as a
# refusal WITHOUT ever issuing a second request that would carry the same
# Authorization header to a destination this binding never authorized.
$req.AllowAutoRedirect = $false
$req.Headers['Authorization'] = $authValue
$authValue = $null
if ($Body) {
    if ($ContentType) { $req.ContentType = $ContentType }
    $bodyBytes = [System.Text.Encoding]::UTF8.GetBytes($Body)
    $req.ContentLength = $bodyBytes.Length
    $reqStream = $req.GetRequestStream()
    try { $reqStream.Write($bodyBytes, 0, $bodyBytes.Length) } finally { $reqStream.Close() }
} elseif ($Method -ne 'GET' -and $Method -ne 'HEAD') {
    $req.ContentLength = 0
}

try {
    $resp = $req.GetResponse()
} catch [System.Net.WebException] {
    if ($_.Exception.Response) {
        $resp = $_.Exception.Response
    } else {
        $exMsg = $_.Exception.Message
        Write-Error "REFUSED: HTTP request to '$Destination' failed ($exMsg)."
        exit 1
    }
}

try {
    $status = [int]$resp.StatusCode
    if ($status -ge 300 -and $status -lt 400) {
        $location = $resp.Headers['Location']
        Write-Error ("REFUSED: '$Destination' responded with a redirect " +
            "(HTTP $status) to '$location'. Redirects are NEVER followed by " +
            "this broker -- a redirect past the bound destination pattern is " +
            "treated as exfiltration risk (the round-1 design fold: binding " +
            "must be checked at the FINAL RESOLVED destination, and the only " +
            "way to guarantee that is to never resolve past what was already " +
            "checked). If '$location' is itself a legitimate, operator-" +
            "approved destination, ask the operator to add it to '$Name`'s " +
            "own bindings and call credential-use against it directly.")
        exit 1
    }
    $stream = $resp.GetResponseStream()
    $reader = New-Object System.IO.StreamReader($stream)
    $respBody = $reader.ReadToEnd()
    $reader.Close()
    Write-Output "credential-use: HTTP $status from '$Destination'."
    if ($respBody) {
        Write-Output "----- response body -----"
        Write-Output $respBody
    }
    if ($status -ge 400) { exit 1 }
    exit 0
} finally {
    $resp.Close()
}
