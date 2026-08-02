<#
.SYNOPSIS
credential-selftest.ps1 -- hermetic self-test suite for the credential broker
(design brief harness-v3.0/specs/credential-broker-design-brief-2026-07-23.md;
build-acceptance criteria folded from the brief's cross-vendor round-1 log).

.DESCRIPTION
Black-box tests credential-store.ps1 / credential-use.ps1 / credential-remove.ps1
by invoking each as a SEPARATE OS PROCESS (never dot-sourced, never `&`-called
in-process) and capturing its stdout/stderr, exactly the way an operator or a
session actually runs them. This is deliberate: it proves what a real invocation
actually emits, not what an in-process function call happens to return, and it
sidesteps any Add-Type "-namespace already exists" collisions between scripts.

HERMETICITY (every fixture is this suite's own, never a production surface):
  * -VaultPrefix "rheoscope-selftest" on every store/use/remove call -- this
    suite can NEVER read, write, or delete a "rheoscope/*" production entry,
    because the vault target string ("rheoscope-selftest/<name>") is simply a
    different string than any production target could ever be.
  * -BindingsPath always points at a fixture file this suite writes into its own
    temp directory. The live deploy/credential-bindings.yaml is NEVER read by
    this suite.
  * Every vault entry this suite creates is removed in a `finally` block --
    named removal for everything this run tracked creating, PLUS a defensive
    CredEnumerate sweep over the "rheoscope-selftest/*" namespace that deletes
    any stray entry a bug might have left behind under a name this run didn't
    anticipate. The temp fixture directory is removed the same way.

THE LEAK CHECK (build-acceptance criterion, not a per-case afterthought): every
child-process invocation this suite makes is captured through ONE choke point
(Invoke-Capture / Invoke-CaptureConcurrent) that appends its combined
stdout+stderr to $script:AllCaptures. After every case has run, ONE pass scans
EVERY captured stream from EVERY invocation for $script:TestSecret -- a
high-entropy, per-run-unique password used for every credential this suite
stores. If that string appears ANYWHERE in ANY captured stream, the whole run
fails loudly, attributed to the case that produced it. What this DOES cover:
every byte credential-store.ps1, credential-use.ps1, and credential-remove.ps1
themselves wrote to their own stdout/stderr, across every case in this file.
What it does NOT cover: bytes a CONSUMING process (the stdin-mode child, or a
real HTTP server) might itself choose to leak -- that is the design brief's own
recorded residual ("consumer-side leakage"), not something this broker's own
output-stream discipline can control, and this suite's stdin-mode child is
deliberately built to compute a hash rather than echo anything, so it cannot
accidentally demonstrate a leak it was never at risk of causing.

Exit: 0 if every case passed (or was explicitly, honestly skipped with a
reason). 1 if any case failed, INCLUDING a leak-check failure.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot

# ------------------------------------------------------------------ scaffolding
$script:total = 0
$script:failed = 0
$script:skipped = 0
$script:AllCaptures = New-Object System.Collections.Generic.List[object]

function TCase {
    param([string]$Name, [bool]$Ok, [string]$Detail = '')
    $script:total++
    $mark = if ($Ok) { 'ok ' } else { 'XX ' }
    if ($Detail) {
        Write-Output "  $mark $Name -- $Detail"
    } else {
        Write-Output "  $mark $Name"
    }
    if (-not $Ok) { $script:failed++ }
}

function TSkip {
    param([string]$Name, [string]$Reason)
    $script:skipped++
    Write-Output "  -- $Name (SKIPPED: $Reason)"
}

function Get-Flat {
    # Write-Error's formatted rendering word-wraps at the host's console width
    # even when stderr is fully redirected to a pipe -- confirmed empirically
    # while building this suite: a phrase this suite checks for can arrive
    # with a newline (plus the next line's leading indent) inserted between two
    # of its words. Assertions that check for a MULTI-WORD phrase collapse
    # whitespace runs first so a wrap point never causes a false failure. This
    # is never applied to the leak-scan: the secret marker is a single
    # unbroken token, and word-wrap only ever inserts breaks BETWEEN words, so
    # the marker's own bytes cannot be split by it -- flattening there would
    # only ever hide a real leak, never manufacture a false one, so it is
    # deliberately left out of that scan.
    param([string]$Text)
    if (-not $Text) { return '' }
    return ($Text -replace '\s+', ' ').Trim()
}

# ------------------------------------------------------------------ win32 argv quoting
# Duplicated from credential-use.ps1 -- see that copy's comment for why this
# exists (PowerShell 5.1 / .NET Framework has no ProcessStartInfo.ArgumentList,
# so a single correctly-quoted Arguments string must be built by hand). This
# suite needs its own copy because it launches the OTHER scripts as separate
# processes and must build THEIR command lines the same principled way.
function ConvertTo-Win32ArgumentString {
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

$script:Utf8NoBom = New-Object System.Text.UTF8Encoding($false)

# LOAD-BEARING (found the hard way, by dumping a child's RAW received bytes,
# not just its decoded text -- see credential-use.ps1's own copy of this exact
# fix and comment for the full mechanism): .NET's Process class builds a
# child's StandardInput StreamWriter from THIS PROCESS's OWN
# [Console]::InputEncoding, and merely reading `.BaseStream` on that writer (the
# only way .NET Framework exposes the raw pipe) flushes its pending preamble --
# a UTF-8 BOM (EF BB BF), on a host whose default console input encoding emits
# one -- onto the child's stdin BEFORE this suite's own no-BOM writer gets to
# write anything. That stray 3-byte prefix landed on credential-store.ps1's
# stored USERNAME during this suite's own early debugging (confirmed via
# `cmdkey /list`, which displays it as mojibake) and would have silently
# invalidated every stdin-mode hash comparison in this file. Setting THIS
# session's own Console.InputEncoding to a no-BOM UTF-8 encoding, once, before
# any child is started, eliminates it at the source for every Invoke-Capture /
# Start-CaptureAsync call in this file.
try { [Console]::InputEncoding = $script:Utf8NoBom } catch { }

function Invoke-Capture {
    # Runs `powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass
    # -File <ScriptPath> <ScriptArgs...>` as a fresh process, optionally writing
    # -StdinLines to its stdin (the ONLY channel a test credential value ever
    # travels through -- never as an element of $ScriptArgs, which becomes argv).
    # Bounded by -TimeoutMs with a Kill() fallback so a regression that hangs
    # (like the Get-Credential-popup hang this build found and fixed) fails the
    # suite instead of hanging it forever.
    param(
        [string]$CaseLabel,
        [string]$ScriptPath,
        [string[]]$ScriptArgs = @(),
        [string[]]$StdinLines = $null,
        [int]$TimeoutMs = 20000
    )
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = 'powershell.exe'
    $full = @('-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', $ScriptPath) + $ScriptArgs
    $psi.Arguments = ConvertTo-Win32ArgumentString -ArgList $full
    $psi.UseShellExecute = $false
    $psi.RedirectStandardInput = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.StandardOutputEncoding = $script:Utf8NoBom
    $psi.StandardErrorEncoding = $script:Utf8NoBom
    $psi.CreateNoWindow = $true

    $proc = New-Object System.Diagnostics.Process
    $proc.StartInfo = $psi
    [void]$proc.Start()

    # ReadToEndAsync() Tasks, NOT Register-ObjectEvent/BeginOutputReadLine --
    # LOAD-BEARING, found via a real, reproducible flake in this exact suite:
    # PowerShell 5.1's Register-ObjectEvent queues each OutputDataReceived/
    # ErrorDataReceived LINE as a separate engine event, and for a message
    # spanning several lines in quick succession (credential-use.ps1's -Prompt
    # refusal, for one), the engine can invoke those queued -Action scriptblocks
    # OUT OF THE ORDER the lines actually arrived in -- silently scrambling a
    # multi-line message before this suite's own assertions ever see it. That
    # produced an intermittent, otherwise-inexplicable failure on the -Prompt
    # case during this build (confirmed by printing the captured text: whole
    # sentences appeared transposed). A Task-based ReadToEndAsync() has no such
    # queue: each stream is read strictly in arrival order into one buffer.
    $stdoutTask = $proc.StandardOutput.ReadToEndAsync()
    $stderrTask = $proc.StandardError.ReadToEndAsync()

    if ($StdinLines -and $StdinLines.Count -gt 0) {
        $writer = New-Object System.IO.StreamWriter($proc.StandardInput.BaseStream, $script:Utf8NoBom)
        $writer.NewLine = "`n"
        foreach ($line in $StdinLines) { $writer.WriteLine($line) }
        $writer.Flush()
        $writer.Close()
    } else {
        $proc.StandardInput.Close()
    }

    $finished = $proc.WaitForExit($TimeoutMs)
    if (-not $finished) {
        try { $proc.Kill() } catch {}
        $proc.WaitForExit(3000) | Out-Null
    } else {
        $proc.WaitForExit()
    }
    # The process has exited (or was killed), so both streams have hit EOF and
    # these Tasks complete within moments; the timeout is a defensive backstop
    # only, never expected to fire.
    [System.Threading.Tasks.Task]::WaitAll(@($stdoutTask, $stderrTask), 15000) | Out-Null

    $result = [PSCustomObject]@{
        CaseLabel = $CaseLabel
        TimedOut  = -not $finished
        ExitCode  = if ($finished) { $proc.ExitCode } else { -1 }
        StdOut    = if ($stdoutTask.IsCompleted) { $stdoutTask.Result } else { '' }
        StdErr    = if ($stderrTask.IsCompleted) { $stderrTask.Result } else { '' }
    }
    $script:AllCaptures.Add($result) | Out-Null
    return $result
}

function Start-CaptureAsync {
    # Same process-construction as Invoke-Capture, split into "start" and
    # "complete" phases -- used ONLY for the two HTTP cases, which must run
    # CONCURRENTLY with this process's own loopback HttpListener.
    # Invoke-Capture's single-call shape blocks this thread in WaitForExit()
    # immediately after starting the child, which would deadlock against a
    # listener that needs THIS SAME THREAD free to accept the child's
    # connection. Splitting start/complete lets the caller service the
    # listener in between. (An earlier draft used Start-Process with
    # -RedirectStandardOutput/-PassThru for this instead; empirically, on this
    # host, that combination leaves the returned Process object's ExitCode
    # unreadable even after WaitForExit(...) returns true and HasExited is
    # true -- a known-flaky combination. System.Diagnostics.Process, driven
    # directly, does not have that problem, so it is used here too, for
    # consistency with Invoke-Capture and because it is the combination this
    # suite actually verified works.)
    param(
        [string]$CaseLabel,
        [string]$ScriptPath,
        [string[]]$ScriptArgs = @()
    )
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = 'powershell.exe'
    $full = @('-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', $ScriptPath) + $ScriptArgs
    $psi.Arguments = ConvertTo-Win32ArgumentString -ArgList $full
    $psi.UseShellExecute = $false
    $psi.RedirectStandardInput = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.StandardOutputEncoding = $script:Utf8NoBom
    $psi.StandardErrorEncoding = $script:Utf8NoBom
    $psi.CreateNoWindow = $true

    $proc = New-Object System.Diagnostics.Process
    $proc.StartInfo = $psi
    [void]$proc.Start()

    # Task-based, order-preserving capture -- see Invoke-Capture's comment for
    # why Register-ObjectEvent/BeginOutputReadLine is deliberately not used.
    $stdoutTask = $proc.StandardOutput.ReadToEndAsync()
    $stderrTask = $proc.StandardError.ReadToEndAsync()
    # -Mode http never reads stdin; closing it immediately is correct (and
    # required -- an unclosed stdin pipe would otherwise leave the child
    # waiting on input it will never receive in this mode).
    $proc.StandardInput.Close()

    return [PSCustomObject]@{
        CaseLabel  = $CaseLabel
        Process    = $proc
        StdOutTask = $stdoutTask
        StdErrTask = $stderrTask
    }
}

function Complete-CaptureAsync {
    param($Handle, [int]$TimeoutMs = 20000)
    $finished = $Handle.Process.WaitForExit($TimeoutMs)
    if (-not $finished) {
        try { $Handle.Process.Kill() } catch {}
        $Handle.Process.WaitForExit(3000) | Out-Null
    } else {
        $Handle.Process.WaitForExit()
    }
    [System.Threading.Tasks.Task]::WaitAll(@($Handle.StdOutTask, $Handle.StdErrTask), 15000) | Out-Null

    $result = [PSCustomObject]@{
        CaseLabel = $Handle.CaseLabel
        TimedOut  = -not $finished
        ExitCode  = if ($finished) { $Handle.Process.ExitCode } else { -1 }
        StdOut    = if ($Handle.StdOutTask.IsCompleted) { $Handle.StdOutTask.Result } else { '' }
        StdErr    = if ($Handle.StdErrTask.IsCompleted) { $Handle.StdErrTask.Result } else { '' }
    }
    $script:AllCaptures.Add($result) | Out-Null
    return $result
}

# ------------------------------------------------------------------ fixtures
$script:FixtureDir = Join-Path $env:TEMP ("rheoscope-selftest-" + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $script:FixtureDir -Force | Out-Null

# Per-run, high-entropy, unique password -- the string the universal leak-scan
# looks for. Reused across every test credential this suite stores: the scan is
# a cross-cutting invariant over ALL captured output, not a per-credential
# secret, so one shared marker is both sufficient and simpler to reason about.
$script:TestSecret = "RhSelfTest-" + [Guid]::NewGuid().ToString('N').Substring(0, 24)
$script:TestUser = 'selftestuser'

$httpPort1 = 18901
$httpPort2 = 18902
$httpPort3 = 18903   # redirect TARGET -- separately monitored so "never fetched" is observed, not inferred

$validBindingsPath = Join-Path $script:FixtureDir 'valid-bindings.yaml'
@"
bindings:
  - name: st-roundtrip
    destinations: ["st-stdin-channel"]
  - name: st-wild
    destinations: ["https://*.selftest.local/*"]
    rotates_at: nowhere -- fixture only
  - name: st-http
    destinations: ["http://127.0.0.1:$httpPort1/*"]
  - name: st-httpredir
    destinations: ["http://127.0.0.1:$httpPort2/*"]
"@ | Set-Content -LiteralPath $validBindingsPath -Encoding UTF8

$malformedBindingsPath = Join-Path $script:FixtureDir 'malformed-bindings.yaml'
'bindings: "this is a string, not a list"' | Set-Content -LiteralPath $malformedBindingsPath -Encoding UTF8

$voidBindingsPath = Join-Path $script:FixtureDir 'void-bindings.yaml'
@"
bindings:
  - name: st-void-valid
    destinations: ["st-stdin-channel"]
  - name: bad name with spaces
    destinations: ["whatever"]
"@ | Set-Content -LiteralPath $voidBindingsPath -Encoding UTF8

$absentBindingsPath = Join-Path $script:FixtureDir 'does-not-exist.yaml'

$shaChildPath = Join-Path $script:FixtureDir 'sha-child.ps1'
@'
$bytes = [Console]::In.ReadToEnd()
$sha = [System.Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($bytes))
[Console]::Out.Write([BitConverter]::ToString($sha).Replace("-", "").ToLower())
'@ | Set-Content -LiteralPath $shaChildPath -Encoding UTF8

$VaultPrefix = 'rheoscope-selftest'
$storedNames = New-Object System.Collections.Generic.List[string]

$storeScript = Join-Path $here 'credential-store.ps1'
$useScript = Join-Path $here 'credential-use.ps1'
$removeScript = Join-Path $here 'credential-remove.ps1'

Write-Output 'credential-selftest.ps1'
Write-Output "  vault namespace : $VaultPrefix/*"
Write-Output "  fixture dir     : $script:FixtureDir"
Write-Output ''

try {
    # ============================================================ TC1: round trip
    Write-Output '-- round trip: store -> use (stdin, hash-verified) -> remove --'
    $storeRes = Invoke-Capture -CaseLabel 'tc1-store' -ScriptPath $storeScript `
        -ScriptArgs @('-Name', 'st-roundtrip', '-FromPipe', '-VaultPrefix', $VaultPrefix) `
        -StdinLines @($script:TestUser, $script:TestSecret)
    $storedNames.Add('st-roundtrip')
    TCase 'store: exit 0' ($storeRes.ExitCode -eq 0) "exit=$($storeRes.ExitCode)"
    TCase 'store: echoes only the name' ($storeRes.StdOut -match [Regex]::Escape("Stored credential 'st-roundtrip'"))

    $expectedHashBytes = [Text.Encoding]::UTF8.GetBytes("$($script:TestUser)`n$($script:TestSecret)`n")
    $expectedHash = [BitConverter]::ToString(
        [System.Security.Cryptography.SHA256]::Create().ComputeHash($expectedHashBytes)
    ).Replace('-', '').ToLower()

    $useRes = Invoke-Capture -CaseLabel 'tc1-use-stdin' -ScriptPath $useScript -ScriptArgs @(
        '-Name', 'st-roundtrip', '-Destination', 'st-stdin-channel', '-Mode', 'stdin',
        '-Command', 'powershell.exe', '-Arguments', $shaChildPath,
        '-VaultPrefix', $VaultPrefix, '-BindingsPath', $validBindingsPath
    )
    TCase 'use (stdin): exit 0' ($useRes.ExitCode -eq 0) "exit=$($useRes.ExitCode); stderr=$($useRes.StdErr.Trim())"
    TCase 'use (stdin): child computed the EXPECTED hash of "user`nsecret`n"' `
        ($useRes.StdOut -match [Regex]::Escape($expectedHash)) `
        $(if (-not ($useRes.StdOut -match [Regex]::Escape($expectedHash))) { "expected=$expectedHash actual=$(Get-Flat $useRes.StdOut)" } else { '' })

    $removeRes1 = Invoke-Capture -CaseLabel 'tc1-remove-1' -ScriptPath $removeScript `
        -ScriptArgs @('-Name', 'st-roundtrip', '-VaultPrefix', $VaultPrefix)
    TCase 'remove: exit 0, reports removed' `
        ($removeRes1.ExitCode -eq 0 -and $removeRes1.StdOut -match 'Removed credential')

    # ============================================================ TC10: idempotent remove
    $removeRes2 = Invoke-Capture -CaseLabel 'tc10-remove-again' -ScriptPath $removeScript `
        -ScriptArgs @('-Name', 'st-roundtrip', '-VaultPrefix', $VaultPrefix)
    TCase 'remove is idempotent: second remove exit 0, "not found"' `
        ($removeRes2.ExitCode -eq 0 -and $removeRes2.StdOut -match 'was not found')

    # ============================================================ TC2: unbound name
    Write-Output ''
    Write-Output '-- authorization checks (all via -DryRun, no vault/network touched) --'
    $r = Invoke-Capture -CaseLabel 'tc2-unbound-name' -ScriptPath $useScript -ScriptArgs @(
        '-Name', 'st-does-not-exist', '-Destination', 'anything', '-Mode', 'http', '-DryRun',
        '-VaultPrefix', $VaultPrefix, '-BindingsPath', $validBindingsPath
    )
    TCase 'unbound name refused' ($r.ExitCode -eq 1 -and (Get-Flat $r.StdErr) -match 'no entry in the bindings')

    # ============================================================ TC3: off-pattern destination
    $r = Invoke-Capture -CaseLabel 'tc3-off-pattern' -ScriptPath $useScript -ScriptArgs @(
        '-Name', 'st-wild', '-Destination', 'https://not-selftest.example.org/x', '-Mode', 'http', '-DryRun',
        '-VaultPrefix', $VaultPrefix, '-BindingsPath', $validBindingsPath
    )
    TCase 'bound name + off-pattern destination refused' `
        ($r.ExitCode -eq 1 -and (Get-Flat $r.StdErr) -match 'does not match any bound pattern')

    # ============================================================ TC4: wildcard match
    $r = Invoke-Capture -CaseLabel 'tc4-wildcard-match' -ScriptPath $useScript -ScriptArgs @(
        '-Name', 'st-wild', '-Destination', 'https://sub.selftest.local/deep/path', '-Mode', 'http', '-DryRun',
        '-VaultPrefix', $VaultPrefix, '-BindingsPath', $validBindingsPath
    )
    TCase 'wildcard pattern matches the correct destination' `
        ($r.ExitCode -eq 0 -and (Get-Flat $r.StdOut) -match 'checks PASSED')

    # ============================================================ TC5: absent bindings file
    $r = Invoke-Capture -CaseLabel 'tc5-absent-bindings' -ScriptPath $useScript -ScriptArgs @(
        '-Name', 'st-wild', '-Destination', 'https://sub.selftest.local/x', '-Mode', 'http', '-DryRun',
        '-VaultPrefix', $VaultPrefix, '-BindingsPath', $absentBindingsPath
    )
    TCase 'absent bindings file => refuse (empty set)' `
        ($r.ExitCode -eq 1 -and (Get-Flat $r.StdErr) -match 'bindings set is EMPTY')

    # ============================================================ TC6: malformed bindings
    $r = Invoke-Capture -CaseLabel 'tc6-malformed-bindings' -ScriptPath $useScript -ScriptArgs @(
        '-Name', 'st-wild', '-Destination', 'https://sub.selftest.local/x', '-Mode', 'http', '-DryRun',
        '-VaultPrefix', $VaultPrefix, '-BindingsPath', $malformedBindingsPath
    )
    TCase 'malformed bindings (top-level not a list) => refuse (empty set)' `
        ($r.ExitCode -eq 1 -and (Get-Flat $r.StdErr) -match 'bindings set is EMPTY')

    # ============================================================ TC7: one bad entry voids all
    $r = Invoke-Capture -CaseLabel 'tc7-one-bad-entry-voids-all' -ScriptPath $useScript -ScriptArgs @(
        '-Name', 'st-void-valid', '-Destination', 'st-stdin-channel', '-Mode', 'http', '-DryRun',
        '-VaultPrefix', $VaultPrefix, '-BindingsPath', $voidBindingsPath
    )
    TCase 'ONE invalid entry voids ALL entries (a same-file VALID name now refuses too)' `
        ($r.ExitCode -eq 1 -and (Get-Flat $r.StdErr) -match 'bindings set is EMPTY') `
        "st-void-valid would otherwise match st-stdin-channel; the malformed sibling entry must void the whole file"

    # ============================================================ TC8: financial denylist
    $r = Invoke-Capture -CaseLabel 'tc8-financial-denylist' -ScriptPath $useScript -ScriptArgs @(
        '-Name', 'st-wild', '-Destination', 'https://www.paypal.com/login', '-Mode', 'http', '-DryRun',
        '-VaultPrefix', $VaultPrefix, '-BindingsPath', $validBindingsPath
    )
    $flatFin = Get-Flat $r.StdErr
    TCase 'financial-denylist destination refused with the policy message' (
        $r.ExitCode -eq 1 -and
        $flatFin -match 'BY POLICY' -and
        $flatFin -match 'operator enters those personally' -and
        $flatFin -match 'DEFENSE-IN-DEPTH'
    )

    # ============================================================ TC9: -Prompt structurally wired
    Write-Output ''
    Write-Output '-- Tier B (-Prompt) path --'
    $r = Invoke-Capture -CaseLabel 'tc9-prompt-noninteractive' -ScriptPath $useScript -ScriptArgs @(
        '-Name', 'st-wild', '-Destination', 'https://sub.selftest.local/x', '-Mode', 'http', '-Prompt',
        '-VaultPrefix', $VaultPrefix, '-BindingsPath', $validBindingsPath
    ) -TimeoutMs 15000
    if ($r.TimedOut) {
        TCase '-Prompt path refuses cleanly when non-interactive (did not hang)' $false 'TIMED OUT -- this is the exact regression this suite exists to catch'
    } else {
        # Must be refused for the RIGHT reason (the interactive-prompt guard),
        # not an earlier, unrelated refusal -- otherwise this case would pass
        # even if the -Prompt code path were never reached at all.
        $flatPrompt = Get-Flat $r.StdErr
        TCase '-Prompt path structurally wired: reaches the prompt guard and refuses cleanly, non-interactive' `
            ($r.ExitCode -eq 1 -and $flatPrompt -match 'cannot raise the Tier B credential prompt' -and $flatPrompt -match 'AFTER all binding/policy checks above passed') `
            "exit=$($r.ExitCode); stderr=$flatPrompt"
    }

    # ============================================================ HTTP mode (real network, loopback only)
    Write-Output ''
    Write-Output '-- HTTP mode: real loopback requests (Basic-auth delivery + redirect refusal) --'
    $listenersOk = $true
    try {
        $probe = New-Object System.Net.HttpListener
        $probe.Prefixes.Add("http://127.0.0.1:$httpPort1/")
        $probe.Start()
        $probe.Stop()
    } catch {
        $listenersOk = $false
    }

    if (-not $listenersOk) {
        TSkip 'http mode: Basic-auth header delivered to allowed loopback destination' 'HttpListener could not bind a loopback port in this environment'
        TSkip 'http mode: redirect is refused, never followed' 'HttpListener could not bind a loopback port in this environment'
        TSkip 'http mode: redirect target endpoint observed zero requests' 'HttpListener could not bind a loopback port in this environment'
    } else {
        # ---- Basic-auth delivery ----
        $storeHttp = Invoke-Capture -CaseLabel 'tc-http1-store' -ScriptPath $storeScript `
            -ScriptArgs @('-Name', 'st-http', '-FromPipe', '-VaultPrefix', $VaultPrefix) `
            -StdinLines @($script:TestUser, $script:TestSecret)
        $storedNames.Add('st-http')
        TCase 'http-1 setup: credential stored' ($storeHttp.ExitCode -eq 0)

        $listener1 = New-Object System.Net.HttpListener
        $listener1.Prefixes.Add("http://127.0.0.1:$httpPort1/")
        $listener1.Start()
        $handle1 = Start-CaptureAsync -CaseLabel 'tc-http1-use' -ScriptPath $useScript -ScriptArgs @(
            '-Name', 'st-http', '-Destination', "http://127.0.0.1:$httpPort1/ok", '-Mode', 'http',
            '-VaultPrefix', $VaultPrefix, '-BindingsPath', $validBindingsPath
        )
        $ctxTask1 = $listener1.GetContextAsync()
        $expectedAuth = 'Basic ' + [Convert]::ToBase64String(
            [Text.Encoding]::UTF8.GetBytes("$($script:TestUser):$($script:TestSecret)"))
        if ($ctxTask1.Wait(10000)) {
            $ctx1 = $ctxTask1.Result
            $seenAuth = $ctx1.Request.Headers['Authorization']
            $bytes = [Text.Encoding]::UTF8.GetBytes('loopback-ok')
            $ctx1.Response.StatusCode = 200
            $ctx1.Response.ContentLength64 = $bytes.Length
            $ctx1.Response.OutputStream.Write($bytes, 0, $bytes.Length)
            $ctx1.Response.OutputStream.Close()
            TCase 'http-1: server received the EXACT expected Basic-auth header' ($seenAuth -eq $expectedAuth)
        } else {
            TCase 'http-1: server received the EXACT expected Basic-auth header' $false 'no request arrived within 10s'
        }
        $useHttp1 = Complete-CaptureAsync -Handle $handle1
        $listener1.Stop()
        $listener1.Close()
        TCase 'http-1: credential-use reports HTTP 200 and exits 0' `
            ($useHttp1.ExitCode -eq 0 -and (Get-Flat $useHttp1.StdOut) -match 'HTTP 200') `
            "exit=$($useHttp1.ExitCode); stdout=$(Get-Flat $useHttp1.StdOut); stderr=$(Get-Flat $useHttp1.StdErr)"

        $removeHttp1 = Invoke-Capture -CaseLabel 'tc-http1-remove' -ScriptPath $removeScript `
            -ScriptArgs @('-Name', 'st-http', '-VaultPrefix', $VaultPrefix)
        TCase 'http-1 cleanup: removed' ($removeHttp1.ExitCode -eq 0)

        # ---- redirect refusal ----
        $storeHttp2 = Invoke-Capture -CaseLabel 'tc-http2-store' -ScriptPath $storeScript `
            -ScriptArgs @('-Name', 'st-httpredir', '-FromPipe', '-VaultPrefix', $VaultPrefix) `
            -StdinLines @($script:TestUser, $script:TestSecret)
        $storedNames.Add('st-httpredir')
        TCase 'http-2 setup: credential stored' ($storeHttp2.ExitCode -eq 0)

        $listener2 = New-Object System.Net.HttpListener
        $listener2.Prefixes.Add("http://127.0.0.1:$httpPort2/")
        $listener2.Start()
        # The redirect TARGET gets its own monitored listener: "never fetched"
        # must be an observation at the target, not an inference from the
        # origin listener (which a followed off-host Location could never
        # reach anyway) -- cross-vendor round-2 correction, 2026-07-23.
        $listener3 = New-Object System.Net.HttpListener
        $listener3.Prefixes.Add("http://127.0.0.1:$httpPort3/")
        $listener3.Start()
        $redirTarget = "http://127.0.0.1:$httpPort3/steal-target"
        $handle2 = Start-CaptureAsync -CaseLabel 'tc-http2-use' -ScriptPath $useScript -ScriptArgs @(
            '-Name', 'st-httpredir', '-Destination', "http://127.0.0.1:$httpPort2/redirect", '-Mode', 'http',
            '-VaultPrefix', $VaultPrefix, '-BindingsPath', $validBindingsPath
        )
        $ctxTask2 = $listener2.GetContextAsync()
        $ctxTask3 = $listener3.GetContextAsync()
        $firstPath = $null
        if ($ctxTask2.Wait(10000)) {
            $ctx2 = $ctxTask2.Result
            $firstPath = $ctx2.Request.Url.AbsolutePath
            $ctx2.Response.StatusCode = 302
            $ctx2.Response.Headers.Add('Location', $redirTarget)
            $ctx2.Response.Close()
        }
        # A second GetContextAsync with a SHORT wait: if the broker (wrongly)
        # re-requested the ORIGIN, a second request lands here within a couple
        # of seconds.
        $ctxTask2b = $listener2.GetContextAsync()
        $followed = $ctxTask2b.Wait(3000)
        # The load-bearing observation: the TARGET listener must have seen
        # ZERO requests in the same window.
        $targetFetched = $ctxTask3.Wait(3000)
        $listener2.Stop()
        $listener2.Close()
        $listener3.Stop()
        $listener3.Close()
        TCase 'http-2: origin server received exactly ONE request' `
            ($firstPath -eq '/redirect' -and -not $followed)
        TCase 'http-2: redirect TARGET endpoint observed ZERO requests (never fetched, observed at the target itself)' `
            (-not $targetFetched)

        $useHttp2 = Complete-CaptureAsync -Handle $handle2
        $flatHttp2 = Get-Flat $useHttp2.StdErr
        TCase 'http-2: credential-use REFUSES the redirect (never follows it)' (
            $useHttp2.ExitCode -eq 1 -and
            $flatHttp2 -match 'Redirects are NEVER followed' -and
            $flatHttp2 -match [Regex]::Escape("127.0.0.1:$httpPort3/steal-target")
        ) "exit=$($useHttp2.ExitCode); stderr=$flatHttp2"

        $removeHttp2 = Invoke-Capture -CaseLabel 'tc-http2-remove' -ScriptPath $removeScript `
            -ScriptArgs @('-Name', 'st-httpredir', '-VaultPrefix', $VaultPrefix)
        TCase 'http-2 cleanup: removed' ($removeHttp2.ExitCode -eq 0)
    }

    # ============================================================ universal leak scan
    Write-Output ''
    Write-Output '-- universal leak scan: every captured stream from every case above --'
    $leakHits = New-Object System.Collections.Generic.List[string]
    foreach ($cap in $script:AllCaptures) {
        if ($cap.StdOut -match [Regex]::Escape($script:TestSecret)) {
            $leakHits.Add("$($cap.CaseLabel): TestSecret found in STDOUT")
        }
        if ($cap.StdErr -match [Regex]::Escape($script:TestSecret)) {
            $leakHits.Add("$($cap.CaseLabel): TestSecret found in STDERR")
        }
    }
    TCase "no credential bytes in any captured stream (scanned $($script:AllCaptures.Count) invocations)" `
        ($leakHits.Count -eq 0) `
        $(if ($leakHits.Count -gt 0) { $leakHits -join '; ' } else { '' })

} finally {
    # ============================================================ cleanup
    Write-Output ''
    Write-Output '-- cleanup --'
    foreach ($n in $storedNames) {
        try {
            & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $removeScript `
                -Name $n -VaultPrefix $VaultPrefix 2>&1 | Out-Null
        } catch {
            Write-Output "  cleanup warning: could not remove '$n': $($_.Exception.Message)"
        }
    }

    # Defensive sweep: enumerate the ENTIRE rheoscope-selftest/* namespace and
    # delete anything still there, catching any name a bug might have created
    # under an identifier this run didn't track above. Never touches
    # "rheoscope/*" (production) -- the filter string is namespace-anchored.
    if (-not ([System.Management.Automation.PSTypeName]'Rheoscope.CredVault.SelfTestSweep').Type) {
        Add-Type -Namespace Rheoscope.CredVault -Name SelfTestSweep -MemberDefinition @'
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
[DllImport("advapi32.dll", EntryPoint = "CredEnumerateW", CharSet = CharSet.Unicode, SetLastError = true)]
public static extern bool CredEnumerate(string filter, UInt32 flags, out UInt32 count, out IntPtr pCredentials);
[DllImport("advapi32.dll", EntryPoint = "CredDeleteW", CharSet = CharSet.Unicode, SetLastError = true)]
public static extern bool CredDelete(string target, UInt32 type, UInt32 flags);
[DllImport("advapi32.dll", EntryPoint = "CredFree", SetLastError = false)]
public static extern void CredFree(IntPtr cred);
'@
    }
    try {
        $countRef = [UInt32]0
        $arrPtr = [IntPtr]::Zero
        $ok = [Rheoscope.CredVault.SelfTestSweep]::CredEnumerate("$VaultPrefix/*", 0, [ref]$countRef, [ref]$arrPtr)
        if ($ok -and $countRef -gt 0) {
            for ($i = 0; $i -lt $countRef; $i++) {
                $itemPtr = [System.Runtime.InteropServices.Marshal]::ReadIntPtr($arrPtr, $i * [IntPtr]::Size)
                $c = [System.Runtime.InteropServices.Marshal]::PtrToStructure(
                    $itemPtr, [type]'Rheoscope.CredVault.SelfTestSweep+CREDENTIAL')
                Write-Output "  defensive sweep: found stray vault entry '$($c.TargetName)' -- deleting"
                [void][Rheoscope.CredVault.SelfTestSweep]::CredDelete($c.TargetName, 1, 0)
            }
            [Rheoscope.CredVault.SelfTestSweep]::CredFree($arrPtr)
        }
    } catch {
        Write-Output "  defensive sweep warning: $($_.Exception.Message)"
    }

    try {
        if (Test-Path -LiteralPath $script:FixtureDir) {
            Remove-Item -LiteralPath $script:FixtureDir -Recurse -Force -ErrorAction SilentlyContinue
        }
    } catch {
        Write-Output "  cleanup warning: could not remove fixture dir: $($_.Exception.Message)"
    }
}

# ------------------------------------------------------------------ report
Write-Output ''
Write-Output "credential-selftest.ps1: $script:total case(s), $script:failed failed, $script:skipped skipped."
if ($script:failed -gt 0) {
    Write-Output 'RESULT: FAIL'
    exit 1
}
Write-Output 'RESULT: PASS'
exit 0
