# init.ps1 -- Rheoscope instantiation script (Windows / PowerShell 5.1+).
#
# Reads project.yaml in this directory, substitutes *.template files, wires
# enabled capabilities into runtime locations, deletes the capabilities/ catalog,
# and stamps instantiated metadata.
#
# Flags:
#   -DryRun   Non-destructive: writes substituted output to dry-run-output/,
#             leaves *.template sources in place, does NOT wire capabilities,
#             does NOT delete capabilities/, does NOT stamp project.yaml.
#   -WhatIf   Standard PowerShell ShouldProcess flag. With -WhatIf, this script
#             does a parse-only confirmation and exits 0. Used for the Phase 0
#             exit-criterion syntax check.
#   -Hooks    Wire security hooks into .claude/settings.local.json without
#             prompting (see §6b).
#   -NoHooks  Skip wiring security hooks without prompting (see §6b).
#
# Hard dependency: powershell-yaml module (per Decision V2-11).
#
# Authored per the v2 build plan Phase 0 (off-tree authoring artifact, not committed;
# the committed record of that phase is BUILD-LOG.md Phase 0).

[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [switch]$DryRun,
    [switch]$Hooks,
    [switch]$NoHooks
)

$ErrorActionPreference = 'Stop'
$scriptRoot = $PSScriptRoot
if (-not $scriptRoot) { $scriptRoot = (Get-Location).Path }

if ($Hooks -and $NoHooks) {
    Write-Error "ERROR: cannot combine -Hooks and -NoHooks"
    exit 1
}

# Parse-only syntax-check mode: -WhatIf returns 0 after argument binding.
if ($WhatIfPreference) {
    Write-Output "init.ps1: -WhatIf set; parse-only check passed. No actions performed."
    exit 0
}

function Fail([string]$msg) {
    Write-Error $msg
    exit 1
}

function Info([string]$msg) {
    Write-Output $msg
}

function Write-FileNoBom([string]$Path, [string]$Content) {
    # Writes exactly $Content (preserving line endings) as UTF-8 without BOM.
    $utf8NoBom = New-Object System.Text.UTF8Encoding $false
    $parent = Split-Path $Path -Parent
    if ($parent -and -not (Test-Path $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    [System.IO.File]::WriteAllText($Path, $Content, $utf8NoBom)
}

# ---------- 0. Windows long-path preflight (moved as early as possible -- stranger-test
# RUN 2, 2026-07-24, Finding 1) ----------
# RUN 1's fix (below) only mitigates git operations, and only when a .git already
# exists -- neither condition covers a fresh copy with no git history yet, which is
# exactly how init.ps1 crashed in RUN 2: a plain PowerShell Get-ChildItem -Recurse at
# step 5 (scanning for *.template files) threw a DirIOError deep inside this harness's
# own test-fixture tree, before this git-config line (or anything git-related) ever ran.
# Moved to the top of the script -- right after the function definitions it depends on,
# before the write-probe or any other file operation -- so both mitigations are in place
# as early as the script structure allows.

# Windows MAX_PATH mitigation (stranger-test RUN 1, 2026-07-24, Finding 1): this
# harness's own deep test-fixture tree can exceed the 260-char default path limit on
# Windows. Local repo config only -- never --global/--system; the OS-level
# LongPathsEnabled setting stays the operator's own call, never flipped by this script.
$gitCmd = Get-Command git -ErrorAction SilentlyContinue
if ($gitCmd -and (Test-Path (Join-Path $scriptRoot '.git'))) {
    & $gitCmd.Source -C $scriptRoot config core.longpaths true
    Info "git config core.longpaths true (local to this repo only -- Windows long-path mitigation)"
}

# Pre-flight path-length guard (stranger-test RUN 2, 2026-07-24, Finding 1): RUN 1's
# git-only mitigation above does not stop init.ps1's own plain-PowerShell file
# operations (Get-ChildItem -Recurse at step 5, and others) from crashing with a raw
# DirIOError partway through, on a Windows host without long-path support, once the
# destination path is deep enough to combine with this harness's own deepest shipped
# fixture path and exceed MAX_PATH. Measure the risk and fail LOUD here -- before any
# file operation -- rather than let the operator hit a confusing mid-run crash.
#
# Known deepest fixture path shipped in this repo (relative to repo root, measured
# 2026-07-25 after the MAX_PATH-at-source fixture-name shortening pass): capabilities/
# knowledge-os/extracted/deploy/test-fixtures/loop-state/trees/inv-pp-nokey/handoffs/
# 2026-07-01-fppnk/confidence-audit.md -- 132 characters (was 172 before the fixture
# tree's directory/file names were compressed to short slugs; see check-loop-state.py's
# self-test tree_cases comments and TEMPLATE-README.md's long-path note for the mapping
# from old prose-length names to the new slugs). Update this constant if the fixture
# tree grows deeper (grep -rn 'deepestKnownFixtureRelPathLength' to find every place
# it's declared).
$deepestKnownFixtureRelPathLength = 132
$maxPathSafe = 259  # usable chars under Windows' 260-char MAX_PATH (incl. null terminator)

$isWindowsHost = [System.Environment]::OSVersion.Platform -eq [System.PlatformID]::Win32NT
if ($isWindowsHost) {
    $longPathsEnabled = $false
    try {
        $regVal = Get-ItemPropertyValue -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem' -Name 'LongPathsEnabled' -ErrorAction Stop
        $longPathsEnabled = ($regVal -eq 1)
    } catch {
        $longPathsEnabled = $false
    }

    $worstCaseLength = $scriptRoot.Length + 1 + $deepestKnownFixtureRelPathLength
    if ((-not $longPathsEnabled) -and ($worstCaseLength -gt $maxPathSafe)) {
        Fail @"
ERROR: this destination path is too long for Windows' default MAX_PATH (260 chars)
once combined with this harness's own deepest shipped fixture path -- init.ps1 would
crash partway through (a DirIOError from Get-ChildItem while scanning for *.template
files), not fail cleanly. Confirmed by stranger-test RUN 2, Finding 1.

  Destination root:            $scriptRoot  ($($scriptRoot.Length) chars)
  + deepest known fixture path: ~$deepestKnownFixtureRelPathLength chars
  = worst case:                 ~$worstCaseLength chars  (safe limit: $maxPathSafe)

Two ways to unblock, in order of preference:
  1. Instantiate at a shorter destination path (fewer/shorter parent directories).
  2. If the destination path is fixed and cannot move, create a short directory
     junction and run init.ps1 through that instead -- no admin rights required, and
     your files stay at the real path (the junction is only an access alias):
       mklink /J C:\short-alias "$scriptRoot"
       cd C:\short-alias
       .\init.ps1 ...
     Remove the junction once init has finished. If you use this workaround, re-check
     .claude/settings.local.json afterward: init substitutes the Read-allow permission from
     the directory you ran it in, so it will carry the alias path, and should be updated to
     the real path once the junction is removed (the hook wiring itself is unaffected, since
     it resolves paths via `$CLAUDE_PROJECT_DIR` at runtime, not this baked-in permission).

A third option, enabling Windows long-path support system-wide, requires admin and is
your own call to make (see TEMPLATE-README.md's Windows long-path note):
  HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem  LongPathsEnabled = 1  (DWORD)
"@
    } elseif (-not $longPathsEnabled) {
        Info "Windows long-path preflight: worst case ~$worstCaseLength chars, within the $maxPathSafe-char safe margin. Continuing."
    } else {
        Info "Windows long-path preflight: LongPathsEnabled is set -- long paths supported, skipping the guard."
    }
} else {
    # POSIX: no comparable MAX_PATH ceiling to guard against. Advisory-only note.
    Info "Windows long-path preflight: not a Windows host, skipping (POSIX has no comparable MAX_PATH ceiling)."
}

# ---------- 1. Pre-flight ----------

$projectYamlPath = Join-Path $scriptRoot 'project.yaml'
if (-not (Test-Path $projectYamlPath)) {
    Fail "ERROR: project.yaml not found at $projectYamlPath. Copy project.yaml.example to project.yaml and edit before running init."
}

if (-not (Get-Module -ListAvailable powershell-yaml)) {
    Fail "ERROR: powershell-yaml module not installed. Install with: Install-Module powershell-yaml -Scope CurrentUser -Force"
}
Import-Module powershell-yaml -ErrorAction Stop

try {
    $probe = Join-Path $scriptRoot ".init-probe-$([Guid]::NewGuid())"
    Set-Content -Path $probe -Value 'probe' -ErrorAction Stop
    Remove-Item $probe -Force -ErrorAction Stop
} catch {
    Fail "ERROR: target directory not writable: $scriptRoot"
}

# ---------- 2. Parse project.yaml ----------

$pyContent = Get-Content $projectYamlPath -Raw -Encoding UTF8
try {
    $py = ConvertFrom-Yaml $pyContent
} catch {
    Fail "ERROR: failed to parse project.yaml: $($_.Exception.Message)"
}

# Idempotency: real-mode init refuses to run on an already-instantiated project.
# Dry-run is allowed (it's non-destructive by design).
if (-not $DryRun) {
    $alreadyStamped = $py.instantiated_date
    if ($alreadyStamped -and ([string]$alreadyStamped).Length -gt 0) {
        Fail "ERROR: project.yaml shows this project is already instantiated (date: $alreadyStamped). Init is not re-runnable."
    }
}

# ---------- 3. Schema validation (defensive re-check) ----------

$requiredFields = @('project_name','project_slug','project_description','capabilities','personnel','tier_examples','template_version')
foreach ($req in $requiredFields) {
    if (-not $py.ContainsKey($req)) {
        Fail "ERROR: project.yaml missing required field: $req"
    }
}

if ([string]$py.project_slug -notmatch '^[a-z0-9][a-z0-9-]*$') {
    Fail "ERROR: project_slug must match ^[a-z0-9][a-z0-9-]*\$ (got: $($py.project_slug))"
}

# Single-source version gate (v2.0 #10a): the expected version is read at runtime from
# the VERSION file at the repo root -- never hardcoded here. VERSION is the template's
# single version source; project.yaml.template_version is the instantiated project's.
# Init consumes VERSION at step 8 so a finished project carries exactly one.
$versionFile = Join-Path $scriptRoot 'VERSION'
if (-not (Test-Path $versionFile -PathType Leaf)) {
    # On an already-instantiated project VERSION is correctly absent (consumed at init).
    # Real mode never reaches here instantiated (the idempotency check exits first), so
    # this branch exists for post-init -DryRun: gate against the stamped version rather
    # than advising a restore that init-validate check 8 would then flag.
    if ($py.instantiated_date -and ([string]$py.instantiated_date).Length -gt 0) {
        $expectedVersion = [string]$py.template_version
    } else {
        Fail "ERROR: VERSION file not found at $versionFile. The harness template ships a one-line VERSION file (the template's single version source); restore it from the template you cloned."
    }
} else {
    $expectedVersion = ([System.IO.File]::ReadAllText($versionFile)).Trim()
}
if ($expectedVersion -notmatch '^[0-9]+(\.[0-9]+){1,2}$') {
    Fail "ERROR: VERSION file at $versionFile must contain a bare version on one line (e.g. 2.0). Got: '$expectedVersion'. Check for stray characters or a UTF-8 BOM."
}
# YAML parsers coerce an unquoted 2.0 to a number, and the trailing zero is lost when
# stringified ("2") -- the comparison below would then fail with a baffling message.
# Require the quoted-string form the schema declares and the example ships.
if ($py.template_version -isnot [string]) {
    Fail "ERROR: template_version must be a quoted string in project.yaml (e.g. template_version: `"$expectedVersion`"). YAML parsed it as a number, which loses trailing zeros (2.0 becomes 2)."
}
if ([string]$py.template_version -ne $expectedVersion) {
    Fail "ERROR: template_version mismatch -- this harness is v$expectedVersion but project.yaml declares '$($py.template_version)'. project.yaml's template_version must match the harness you're instantiating from. If your project was created on an older harness, init is not a migration tool (it runs once); use the matching harness version or follow a documented migration."
}

$validCaps = @('knowledge-os','stress-testing','code-conventions')
foreach ($k in @($py.capabilities.Keys)) {
    if ($validCaps -notcontains $k) {
        Fail "ERROR: capabilities.$k is not a valid capability key. Valid keys: $($validCaps -join ', '). (handoffs is core; kickoff-orchestration and operate-sentinel are documentation-only -- none of these is toggled.)"
    }
}
foreach ($cap in $validCaps) {
    if (-not $py.capabilities.ContainsKey($cap)) {
        Fail "ERROR: capabilities.$cap is required (boolean). Set true or false in project.yaml."
    }
}

if ($py.personnel.Count -lt 1) {
    Fail "ERROR: personnel must contain at least one entry."
}

foreach ($p in $py.personnel) {
    foreach ($f in @('tag','name','role','domains_owned')) {
        if (-not $p.ContainsKey($f)) {
            Fail "ERROR: personnel entry missing required field '$f'."
        }
    }
}

# ---------- 4. Build substitution dictionary ----------

$dict = @{}

# Simple
$dict['project_name'] = [string]$py.project_name
$dict['project_slug'] = [string]$py.project_slug
$dict['project_description'] = [string]$py.project_description
$dict['template_version'] = [string]$py.template_version
$dict['instantiated_date'] = (Get-Date -Format 'yyyy-MM-dd')
$dict['project_root_path'] = ($scriptRoot -replace '\\','/')

# Dotted-access
$dict['tier_examples.T1'] = [string]$py.tier_examples.T1
$dict['tier_examples.T2'] = [string]$py.tier_examples.T2
$dict['tier_examples.T3'] = [string]$py.tier_examples.T3
$dict['tier_examples.T4'] = [string]$py.tier_examples.T4

# Computed
$tags = @($py.personnel | ForEach-Object { [string]$_.tag })
$dict['personnel_tags_csv'] = ($tags -join ', ')

$neutralTags = if ($py.neutral_source_tags -and $py.neutral_source_tags.Count -gt 0) {
    @($py.neutral_source_tags | ForEach-Object { [string]$_ })
} else {
    @('session','ref','field','system')
}
$dict['source_tags_csv'] = (($tags + $neutralTags) -join ', ')

$compassLines = @()
foreach ($p in $py.personnel) {
    $domainsList = @($p.domains_owned | ForEach-Object { [string]$_ })
    $domains = if ($domainsList -contains '*' -or $domainsList -contains '**') {
        'all'
    } else {
        ($domainsList -join ', ')
    }
    $compassLines += "- **$($p.name)** (tag: $($p.tag)) - $($p.role). Domains: $domains."
}
$dict['personnel_compass_block'] = ($compassLines -join "`n")

# wiki_domains_table
$knowledgeOsEnabled = [bool]$py.capabilities['knowledge-os']
$wikiDomains = if ($py.wiki_domains) { @($py.wiki_domains) } else { @() }
if ($knowledgeOsEnabled -and $wikiDomains.Count -gt 0) {
    $rows = @('| Domain | Scope | Description |','|---|---|---|')
    foreach ($d in $wikiDomains) {
        $rows += "| $($d.name) | $($d.default_scope) | $($d.description) |"
    }
    $dict['wiki_domains_table'] = ($rows -join "`n")
} else {
    $dict['wiki_domains_table'] = '(No domains declared yet - populate during INIT.md walkthrough.)'
}

# governance_docs_list
$govDocs = if ($py.governance_docs) { @($py.governance_docs) } else { @() }
if ($govDocs.Count -gt 0) {
    $govLines = @()
    foreach ($g in $govDocs) { $govLines += "- $($g.path) - $($g.description)" }
    $dict['governance_docs_list'] = ($govLines -join "`n")
} else {
    $dict['governance_docs_list'] = '(No governance docs declared yet.)'
}

# enabled_capabilities_list (also computes the wiring set)
$enabled = @()
foreach ($cap in $validCaps) {
    if ([bool]$py.capabilities[$cap]) { $enabled += $cap }
}
if ($enabled.Count -gt 0) {
    $dict['enabled_capabilities_list'] = (($enabled | ForEach-Object { "- $_" }) -join "`n")
} else {
    $dict['enabled_capabilities_list'] = '(No capabilities enabled.)'
}

# ---------- 5. Substitute *.template files ----------

$templateFiles = Get-ChildItem -Path $scriptRoot -Recurse -Filter '*.template' -File
$dryRunRoot = Join-Path $scriptRoot 'dry-run-output'

# Dry-run: clean any prior dry-run-output/ so stale files from a previous run don't
# mislead inspection (v1.1-18).
if ($DryRun -and (Test-Path $dryRunRoot)) {
    Remove-Item $dryRunRoot -Recurse -Force
    Info "[dry-run] cleaned previous dry-run-output/"
}

foreach ($tf in $templateFiles) {
    # Skip anything inside dry-run-output (defensive against re-runs)
    if ($tf.FullName.StartsWith($dryRunRoot)) { continue }

    $content = [System.IO.File]::ReadAllText($tf.FullName)
    $unknownVars = New-Object System.Collections.Generic.List[string]
    $substituted = [regex]::Replace($content, '\{\{([a-zA-Z0-9_.\-]+)\}\}', {
        param($m)
        $key = $m.Groups[1].Value
        if ($dict.ContainsKey($key)) {
            return [string]$dict[$key]
        } else {
            [void]$unknownVars.Add($key)
            return $m.Value
        }
    })
    if ($unknownVars.Count -gt 0) {
        $unique = $unknownVars | Select-Object -Unique
        Fail "ERROR: unknown {{variable}}(s) in $($tf.FullName): $($unique -join ', '). Either add to the Substitution Dictionary Contract and wire here, or remove from the template."
    }

    $relativePath = $tf.FullName.Substring($scriptRoot.Length).TrimStart([char]'\',[char]'/')
    $targetRelative = $relativePath -replace '\.template$',''

    if ($DryRun) {
        $outputPath = Join-Path $dryRunRoot $targetRelative
        Write-FileNoBom -Path $outputPath -Content $substituted
        Info "[dry-run] substituted: $targetRelative"
    } else {
        $outputPath = Join-Path $scriptRoot $targetRelative
        Write-FileNoBom -Path $outputPath -Content $substituted
        Remove-Item $tf.FullName -Force
        Info "substituted: $targetRelative"
    }
}

if ($DryRun) {
    Write-Output ""
    Write-Output "DRY RUN - substituted output written to dry-run-output/. Source .template files untouched. Capabilities not wired. project.yaml not stamped."
    exit 0
}

# ---------- 6. Wire capabilities (real mode only) ----------

# Migration steps per enabled toggled capability (per each capability's RECIPE.md).
$migrationMap = @{
    'knowledge-os' = @(
        @{ src = 'capabilities/knowledge-os/extracted/compile';     dst = '.claude/skills/compile' },
        @{ src = 'capabilities/knowledge-os/extracted/audit';       dst = '.claude/skills/audit' },
        @{ src = 'capabilities/knowledge-os/extracted/wiki-schema.md'; dst = 'docs/wiki-schema.md' },
        @{ src = 'capabilities/knowledge-os/extracted/deploy';        dst = 'deploy' },
        @{ src = 'capabilities/knowledge-os/extracted/discover';      dst = '.claude/skills/discover' },
        @{ src = 'capabilities/knowledge-os/extracted/engine';        dst = 'docs/engine' }
    )
    'stress-testing' = @()
    'code-conventions' = @(
        @{ src = 'capabilities/code-conventions/examples'; dst = 'methodology/code-conventions.examples' }
    )
}

$instantiatedCaps = @()
foreach ($cap in $validCaps) {
    if (-not [bool]$py.capabilities[$cap]) { continue }
    $instantiatedCaps += $cap

    if ($cap -eq 'stress-testing') {
        Info "stress-testing: retired 2026-07-10 -- /preflight ships as a core skill (superseded /grill); nothing to wire."
    }

    if ($cap -eq 'knowledge-os') {
        # Empty-state artifacts (fixes day-1 misdetection, backlog W6-1): /flight-plan's
        # Step 0.6 knowledge-os detection reads wiki/HEALTH.md + wiki/REVIEW.md; without them
        # present, a freshly-wired project misdetects as build-only on its very first
        # /flight-plan run. docs/wiki-schema.md section 11 documents these files' empty states
        # as existing artifacts but nothing created them -- this closes that gap.
        #
        # deploy/project.py's skeleton CLI was evaluated for this and does NOT fit: run in a
        # scratch dir, it writes ECO-4 determinism-check skeletons under --out/projection/
        # (never the live wiki/ locations -- by design, per its own docstring), in a format
        # that doesn't match section 11's documented empty states. So these are generated
        # inline here (identical to init.sh), matching section 11 verbatim.
        #
        # NOTE on encoding: build the section-sign/em-dash characters via [char] codepoints,
        # not literal Unicode in this source file. Windows PowerShell 5.1 parses a no-BOM
        # .ps1 as the system codepage (not UTF-8), so a literal '#167;'/em-dash typed here
        # would parse back corrupted (confirmed by hand: '#194;#167;' mojibake on disk) even
        # though Write-FileNoBom itself writes correct UTF-8 -- the corruption happens at
        # parse time, before the string ever reaches Write-FileNoBom.
        $sectionMark = [string]([char]0x00A7)
        $emDash = [string]([char]0x2014)

        $wikiHealthPath = Join-Path $scriptRoot 'wiki/HEALTH.md'
        if (-not (Test-Path $wikiHealthPath)) {
            $healthContent = "# Wiki Health`n`nNot yet generated. ``/compile`` overwrites this file entirely on its first run $emDash see ``docs/wiki-schema.md`` $sectionMark 9.`n"
            Write-FileNoBom -Path $wikiHealthPath -Content $healthContent
            Info "created (empty state): wiki/HEALTH.md"
        }

        $wikiReviewPath = Join-Path $scriptRoot 'wiki/REVIEW.md'
        if (-not (Test-Path $wikiReviewPath)) {
            $reviewContent = "# Wiki Review Queue`n"
            Write-FileNoBom -Path $wikiReviewPath -Content $reviewContent
            Info "created (empty state): wiki/REVIEW.md"
        }

        $wikiIndexPath = Join-Path $scriptRoot 'wiki/INDEX.md'
        if (-not (Test-Path $wikiIndexPath)) {
            $indexContent = "# Wiki Index`n`n**What lives here:** Top-level index across all wiki domains for $($py.project_name). Points to each domain's own INDEX.md and summarizes its current state. Populated once wiki domains are declared and ``/compile`` has run.`n`n## Known Gaps`n`n- No wiki domains declared yet $emDash populate ``project.yaml.wiki_domains`` during the INIT.md walkthrough ($sectionMark 2f) before the first ``/compile`` run.`n"
            Write-FileNoBom -Path $wikiIndexPath -Content $indexContent
            Info "created (empty state): wiki/INDEX.md"
        }

        $sessionBriefingPath = Join-Path $scriptRoot 'SESSION-BRIEFING.md'
        if (-not (Test-Path $sessionBriefingPath)) {
            $notYetPopulated = "(Not yet populated $emDash run /compile.)"
            $briefingLines = @(
                '# Session Briefing',
                '',
                '**Last compiled:** (not yet compiled)',
                "**Governing documents:** CLAUDE.md (governance), core/methodology/ (methodology kernel), this wiki's INDEX",
                '',
                '## Architectural Context',
                '',
                $notYetPopulated,
                '',
                '## Active Workstreams',
                '',
                $notYetPopulated,
                '',
                '## Hold Points',
                '',
                $notYetPopulated,
                '',
                '## Governance Reminders',
                '',
                $notYetPopulated,
                '',
                '## Quick Reference',
                '',
                '| Resource | Location | What it is |',
                '|----------|----------|------------|',
                '| CLAUDE.md | CLAUDE.md | Project governance |',
                '| Wiki Schema | docs/wiki-schema.md | This file |',
                '| REVIEW.md | wiki/REVIEW.md | Open issues and action items |',
                '| HEALTH.md | wiki/HEALTH.md | Wiki coverage and staleness stats |',
                '| Handoffs Index | handoffs/INDEX.md | Substrate-separation inquiries |'
            )
            Write-FileNoBom -Path $sessionBriefingPath -Content (($briefingLines -join "`n") + "`n")
            Info "created (empty state): SESSION-BRIEFING.md"
        }
    }

    if ($migrationMap.ContainsKey($cap)) {
        foreach ($step in $migrationMap[$cap]) {
            $srcPath = Join-Path $scriptRoot $step.src
            $dstPath = Join-Path $scriptRoot $step.dst
            if (-not (Test-Path $srcPath)) {
                Info "skip (source absent): $($step.src)"
                continue
            }
            $dstParent = Split-Path $dstPath -Parent
            if ($dstParent -and -not (Test-Path $dstParent)) {
                New-Item -ItemType Directory -Path $dstParent -Force | Out-Null
            }
            if (Test-Path $srcPath -PathType Container) {
                if (Test-Path $dstPath) { Remove-Item $dstPath -Recurse -Force }
                Copy-Item -Path $srcPath -Destination $dstPath -Recurse -Force
            } else {
                Copy-Item -Path $srcPath -Destination $dstPath -Force
            }
            Info "wired: $($step.src) -> $($step.dst)"
        }
    }

    # Live trigger register (v3.0-101(b), operator-ratified 2026-08-17): the register
    # ships as .example only, so check-triggers degraded with "no trigger register" on
    # every instance that never hand-adopted it. Instantiate the live file from the
    # example ONCE (never overwrite an existing register: its rows may carry instance
    # history). Rows are propose-only by contract -- a live register arms nothing; it
    # only lets sensors report. Identical to init.sh; runs AFTER the deploy copy above
    # so the example exists on a fresh instance.
    if ($cap -eq 'knowledge-os') {
        $trigRegPath = Join-Path $scriptRoot 'deploy/trigger-register.yaml'
        $trigRegExample = Join-Path $scriptRoot 'deploy/trigger-register.yaml.example'
        if ((-not (Test-Path $trigRegPath)) -and (Test-Path $trigRegExample)) {
            Copy-Item $trigRegExample $trigRegPath
            Info "created (live from example): deploy/trigger-register.yaml"
        }
    }

    # Capability-internal deferred recipes
    $deferredDir = Join-Path $scriptRoot "capabilities/$cap/deferred"
    if (Test-Path $deferredDir) {
        $recipesDir = Join-Path $scriptRoot "docs/recipes/$cap"
        New-Item -ItemType Directory -Path $recipesDir -Force | Out-Null
        Get-ChildItem -Path $deferredDir -Filter '*.RECIPE.md' -File | ForEach-Object {
            Copy-Item -Path $_.FullName -Destination (Join-Path $recipesDir $_.Name) -Force
            Info "wired recipe: docs/recipes/$cap/$($_.Name)"
        }
    }
}

# Part B: documentation-only capabilities, unconditional (not toggled).
# These ship their RECIPE.md + deferred recipes into every instantiated project
# regardless of capability toggles; the catalog source is deleted with
# capabilities/ at step 7. To add one, extend the list — no other wiring.
$docsOnlyCaps = @('kickoff-orchestration', 'operate-sentinel', 'decorrelated-review')
foreach ($docsCap in $docsOnlyCaps) {
    $docsSrc = Join-Path $scriptRoot "capabilities/$docsCap"
    if (Test-Path $docsSrc) {
        $docsDst = Join-Path $scriptRoot "docs/recipes/$docsCap"
        New-Item -ItemType Directory -Path $docsDst -Force | Out-Null
        $docsRecipe = Join-Path $docsSrc 'RECIPE.md'
        if (Test-Path $docsRecipe) {
            Copy-Item -Path $docsRecipe -Destination (Join-Path $docsDst 'RECIPE.md') -Force
            Info "wired (unconditional): docs/recipes/$docsCap/RECIPE.md"
        }
        $docsDeferredDir = Join-Path $docsSrc 'deferred'
        if (Test-Path $docsDeferredDir) {
            Get-ChildItem -Path $docsDeferredDir -Filter '*.RECIPE.md' -File | ForEach-Object {
                Copy-Item -Path $_.FullName -Destination (Join-Path $docsDst $_.Name) -Force
                Info "wired (unconditional): docs/recipes/$docsCap/$($_.Name)"
            }
        }
    }
}

# Part C: core skills (unconditional — always wired regardless of capability toggles).
# Core skills live at core/skills/<name>/ (substituted in place by step 5). They are
# copied to .claude/skills/<name>/ and the core/skills/ source is then consumed, so the
# only post-init copy is the runtime one (installed-skills-as-source-of-truth, per ADR-4).
$coreSkillsDir = Join-Path $scriptRoot 'core/skills'
if (Test-Path $coreSkillsDir) {
    Get-ChildItem -Path $coreSkillsDir -Directory | ForEach-Object {
        $skillName = $_.Name
        $dst = Join-Path $scriptRoot ".claude/skills/$skillName"
        $dstParent = Split-Path $dst -Parent
        if ($dstParent -and -not (Test-Path $dstParent)) {
            New-Item -ItemType Directory -Path $dstParent -Force | Out-Null
        }
        if (Test-Path $dst) { Remove-Item $dst -Recurse -Force }
        Copy-Item -Path $_.FullName -Destination $dst -Recurse -Force
        Info "wired (core skill): core/skills/$skillName -> .claude/skills/$skillName"
    }
    Remove-Item $coreSkillsDir -Recurse -Force
    Info "deleted: core/skills/ (consumed into .claude/skills/)"
}

# Part D: core scaffold files (unconditional -- references/ is core per TEMPLATE-README.md's
# file layout table, not capability-gated. INIT.md Step 2e says "Update references/README.md
# to catalog each entry," but nothing shipped or created that file, so a fresh instance had
# no references/ slot to update. Empty-state generated inline here, same convention as the
# knowledge-os wiki/ empty-state files above, but unconditional since references/ carries no
# capability gate.
$referencesReadmePath = Join-Path $scriptRoot 'references/README.md'
if (-not (Test-Path $referencesReadmePath)) {
    $referencesEmDash = [string]([char]0x2014)
    $referencesContent = "# References`n`n**What lives here:** Source material for this project $referencesEmDash existing research, papers, external documentation. For each entry: source URL (or path), date added, why it matters.`n`n(No references catalogued yet $referencesEmDash populate during the INIT.md walkthrough, Step 2e.)`n"
    Write-FileNoBom -Path $referencesReadmePath -Content $referencesContent
    Info "created (empty state): references/README.md"
}

# deliverables/ scaffold (backlog v3.0-54a: declared artifact home for operator-facing
# synthesized outputs; same empty-state convention as references/ above).
$deliverablesReadmePath = Join-Path $scriptRoot 'deliverables/README.md'
if (-not (Test-Path $deliverablesReadmePath)) {
    $delivEmDash = [string]([char]0x2014)
    $deliverablesContent = "# Deliverables`n`n**What lives here:** Operator-facing synthesized outputs $delivEmDash briefs, checklists, filled forms, runbooks, small committed binaries. Every deliverable names its sources (a `"Derived from:`" line, or a sibling ``<name>.provenance.md`` for binaries). Not knowledge intake: new knowledge goes through ``raw/``. See ``docs/wiki-schema.md`` $delivEmDash Artifact homes.`n`n(No deliverables yet.)`n"
    Write-FileNoBom -Path $deliverablesReadmePath -Content $deliverablesContent
    Info "created (empty state): deliverables/README.md"
}

# template_source / template_release stamping (v3.0.12, backlog v3.0-59): give the
# instance the public template's address and its own patch level, so "check for updates"
# is self-serve (core/governance/check-template-updates.py). Append-if-absent -- an
# operator-authored value is never overwritten.
$templateSourceDefault = 'https://github.com/rysidor-stack/rheoscope'
$releaseTagPath = Join-Path $scriptRoot 'RELEASE'
$releaseTag = if (Test-Path $releaseTagPath) { (Get-Content $releaseTagPath -Raw).Trim() } else { '' }
if (-not $releaseTag) { $releaseTag = 'v' + (Get-Content (Join-Path $scriptRoot 'VERSION') -Raw).Trim() }
$projectYamlPath = Join-Path $scriptRoot 'project.yaml'
$projectYamlText = Get-Content $projectYamlPath -Raw
$stampChanged = $false
if ($projectYamlText -notmatch '(?m)^template_source:\s*".+"') {
    $stampChanged = $true
    if ($projectYamlText -match '(?m)^template_source:') {
        $projectYamlText = $projectYamlText -replace '(?m)^template_source:.*$', ('template_source: "' + $templateSourceDefault + '"')
    } else {
        $projectYamlText = $projectYamlText.TrimEnd() + "`ntemplate_source: `"$templateSourceDefault`"`n"
    }
    Info ("stamped: template_source = " + $templateSourceDefault)
}
if ($projectYamlText -notmatch '(?m)^template_release:\s*".+"') {
    $stampChanged = $true
    if ($projectYamlText -match '(?m)^template_release:') {
        $projectYamlText = $projectYamlText -replace '(?m)^template_release:.*$', ('template_release: "' + $releaseTag + '"')
    } else {
        $projectYamlText = $projectYamlText.TrimEnd() + "`ntemplate_release: `"$releaseTag`"`n"
    }
    Info ("stamped: template_release = " + $releaseTag)
}
if ($stampChanged) { Write-FileNoBom -Path $projectYamlPath -Content $projectYamlText }

# ---------- 6b. Wire security hooks (real mode only) ----------
# Fixes the silent-security gap (backlog v3.0-25): until this step, nothing wired the
# PreToolUse hooks (dangerous-bash / env-writes guards) into .claude/settings.local.json --
# a project could sit unprotected with no signal that anything was missing. core/security/
# survives init (init-validate.ps1 Check 6 requires it), so the wired file's
# $CLAUDE_PROJECT_DIR/core/security/hooks/*.sh references stay valid at runtime.
# Consent: -Hooks / -NoHooks decide without asking. Absent both, an interactive terminal
# is prompted (default YES on empty input); a non-interactive terminal wires by default
# with a loud notice, because silently NOT wiring would recreate the exact defect this
# step exists to fix, and the copy is trivially reversible (delete the file, or re-run
# with -NoHooks next time).

$hooksSrc = Join-Path $scriptRoot 'core/security/settings.local.json.example'
$hooksDst = Join-Path $scriptRoot '.claude/settings.local.json'

if (Test-Path $hooksDst) {
    # Detect the specific defect from stranger-test RUN 2 (2026-07-24), Finding 4: a
    # pre-existing settings.local.json (e.g. carried into a scratch copy by a plain
    # file copy instead of a real `git clone`, which would correctly have excluded a
    # gitignored file) silently shadows the -Hooks/-NoHooks consent decision below --
    # the operator can pass -Hooks (or accept the interactive default Yes) and still
    # end up with no security hooks wired, with only this INFO line as the tell. Warn
    # LOUDLY when the existing file doesn't already reference the hooks this step would
    # have wired; never modify the file automatically -- consent-flow behavior below is
    # otherwise unchanged. (Provenance -- the silent-shadowing defect and its confirmation
    # by stranger-test RUN 2, Finding 4 -- lives in this comment; the operator-facing
    # warning below stays plain.)
    $existingSettingsContent = [System.IO.File]::ReadAllText($hooksDst)
    if ($existingSettingsContent -notmatch 'block-dangerous-bash' -or $existingSettingsContent -notmatch 'block-env-writes') {
        Write-Warning "your security protections were NOT switched on -- a settings file already exists without them. If you didn't set that file up on purpose: delete .claude/settings.local.json and run init again, or ask a Claude session to merge the protections in (from core/security/settings.local.json.example). If it's yours on purpose, ignore this."
    }
    Info "settings.local.json already exists -- left untouched; hooks example at core/security/settings.local.json.example"
} elseif (-not (Test-Path $hooksSrc)) {
    Info "skip (source absent): core/security/settings.local.json.example"
} else {
    $wireHooks = $false
    if ($Hooks) {
        $wireHooks = $true
    } elseif ($NoHooks) {
        $wireHooks = $false
    } elseif (-not [Console]::IsInputRedirected) {
        $reply = Read-Host "Wire security hooks (PreToolUse guards for dangerous bash + .env writes) into .claude/settings.local.json? [Y/n]"
        $wireHooks = -not ($reply -match '^[nN]')
    } else {
        $wireHooks = $true
        Write-Warning "wired by default in non-interactive mode; remove .claude/settings.local.json or re-run with -NoHooks to opt out."
    }

    if ($wireHooks) {
        $hooksDstParent = Split-Path $hooksDst -Parent
        if ($hooksDstParent -and -not (Test-Path $hooksDstParent)) {
            New-Item -ItemType Directory -Path $hooksDstParent -Force | Out-Null
        }
        Copy-Item -Path $hooksSrc -Destination $hooksDst -Force
        Info "wired: core/security/settings.local.json.example -> .claude/settings.local.json"
        # jq is a runtime dependency of the wired hooks (block-dangerous-bash.sh,
        # block-env-writes.sh both parse the PreToolUse JSON payload with it). This is a
        # non-fatal presence note, not an init dependency: init itself never invokes jq.
        $jqCmd = Get-Command jq -ErrorAction SilentlyContinue
        if ($jqCmd) {
            Info "jq: found (hooks runtime dependency satisfied)"
        } else {
            Info "NOTE: jq not found on PATH -- the wired hooks require jq at runtime. Install jq before they will run correctly; this does not affect init."
        }
        # Pre-commit secret scanner (v3.0.36, backlog v3.0-12): installed under the SAME
        # consent as the PreToolUse hooks -- one hooks decision, one perimeter. Copies into
        # the repo's own pre-commit slot; an EXISTING pre-commit hook is never overwritten
        # (warn instead -- composing with someone's hook is their call, not init's).
        $scannerSrc = Join-Path $scriptRoot 'core/security/hooks/scan-staged-secrets.sh'
        $precommitDst = Join-Path $scriptRoot '.git/hooks/pre-commit'
        if (-not (Test-Path $scannerSrc)) {
            Info "skip (source absent): core/security/hooks/scan-staged-secrets.sh"
        } elseif (-not (Test-Path (Join-Path $scriptRoot '.git'))) {
            Write-Warning "commit scanning NOT installed -- this folder is not a git repository yet. After 'git init', copy core/security/hooks/scan-staged-secrets.sh to .git/hooks/pre-commit to turn it on."
        } elseif (Test-Path $precommitDst) {
            Write-Warning "commit scanning NOT installed -- a pre-commit hook already exists at .git/hooks/pre-commit. To add secret scanning, chain core/security/hooks/scan-staged-secrets.sh from your existing hook, or replace it if it's not yours on purpose."
        } else {
            $precommitParent = Split-Path $precommitDst -Parent
            if (-not (Test-Path $precommitParent)) { New-Item -ItemType Directory -Path $precommitParent -Force | Out-Null }
            Copy-Item -Path $scannerSrc -Destination $precommitDst -Force
            Info "installed: core/security/hooks/scan-staged-secrets.sh -> .git/hooks/pre-commit (every commit is scanned for secret-shaped content; operator bypass: git commit --no-verify)"
        }
    } else {
        Info "skipped: security hooks not wired (.claude/settings.local.json not created)"
    }
}

# ---------- 7. Delete capabilities/ ----------

$capabilitiesDir = Join-Path $scriptRoot 'capabilities'
if (Test-Path $capabilitiesDir) {
    Remove-Item -Path $capabilitiesDir -Recurse -Force
    Info "deleted: capabilities/"
}

# ---------- 8. Stamp metadata back to project.yaml ----------

$py.instantiated_date = $dict['instantiated_date']
$py.instantiated_capabilities = $instantiatedCaps
$yamlOut = ConvertTo-Yaml $py
Write-FileNoBom -Path $projectYamlPath -Content $yamlOut

# Consume VERSION: from here on, the project's single version source is
# project.yaml.template_version (validated against VERSION above). Leaving a second
# version-bearing file in the project invites exactly the drift class the
# single-source design exists to kill.
try {
    Remove-Item $versionFile -Force -ErrorAction Stop
    Info "deleted: VERSION (consumed; project.yaml template_version is the project's version source)"
} catch {
    Fail "ERROR: init completed but could not delete VERSION (file in use?). Delete it manually, then run init-validate.ps1."
}

# Consume RELEASE the same way (backlog v3.0-76): it was read above to stamp
# project.yaml.template_release; leaving it makes a second version-bearing file that
# nothing reads again and no recipe updates -- it goes permanently stale post-adoption.
if (Test-Path $releaseTagPath) {
    try {
        Remove-Item $releaseTagPath -Force -ErrorAction Stop
        Info "deleted: RELEASE (consumed; project.yaml template_release is the project's release source)"
    } catch {
        Fail "ERROR: init completed but could not delete RELEASE (file in use?). Delete it manually, then run init-validate.ps1."
    }
}

# ---------- 9. Print next-steps ----------

$capsList = if ($instantiatedCaps.Count -gt 0) { $instantiatedCaps -join ', ' } else { '(none)' }
Write-Output ""
Write-Output "Harness instantiated for project: $($py.project_name)"
Write-Output "Capabilities wired: $capsList"
Write-Output ""

# cross-vendor-verify READINESS CHECK: report the /cross-check skills' RUNTIME prerequisites, explicitly,
# so the operator knows their state at instantiation. init only PLACES files; the skills need
# node>=18 + the codex CLI (a ChatGPT/GPT subscription) at runtime. Presence-only and NEVER fatal —
# a missing prereq leaves the skills inert (they fail loud if invoked), it does not fail init.
$bridgeDir = Join-Path $scriptRoot '.claude/skills/bridge'
if (Test-Path $bridgeDir) {
    $nodeCmd = Get-Command node -ErrorAction SilentlyContinue
    $codexCmd = Get-Command codex -ErrorAction SilentlyContinue
    $nodeMsg = if ($nodeCmd) { "found ($(& node --version 2>$null))" } else { "NOT FOUND on PATH (need >=18)" }
    $codexMsg = if ($codexCmd) { "found ($($codexCmd.Source))" } else { "NOT FOUND on PATH" }
    Write-Output "cross-vendor-verify readiness check (runtime prerequisites for the /cross-check skills):"
    Write-Output "  node:  $nodeMsg"
    Write-Output "  codex: $codexMsg"
    if ($nodeCmd -and $codexCmd) {
        Write-Output "  => prerequisites present. Ensure 'codex login' is done (a GPT subscription), then smoke-test:"
        Write-Output "       node .claude/skills/bridge/verify-cli.js --help"
    } else {
        Write-Output "  => the /cross-check + /cross-check-loop skills are INSTALLED but INERT until node>=18 AND the"
        Write-Output "     codex CLI (a ChatGPT/GPT subscription, then 'codex login') are present. They fail loud if"
        Write-Output "     invoked before then; nothing else in this project is affected."
    }
    Write-Output ""
}

# /doctor: post-init sanity check. Degrades gracefully -- the doctor.py file is being
# authored in a parallel work leg, so this invocation silently skips if it hasn't landed
# yet. A doctor failure NEVER fails init; it only surfaces issues to fix before first use.
$doctorPy = Join-Path $scriptRoot '.claude/skills/doctor/doctor.py'
if (Test-Path $doctorPy -PathType Leaf) {
    $pyCmd = Get-Command python3 -ErrorAction SilentlyContinue
    if (-not $pyCmd) { $pyCmd = Get-Command python -ErrorAction SilentlyContinue }
    if ($pyCmd) {
        Write-Output "Running /doctor ($($pyCmd.Name) .claude/skills/doctor/doctor.py)..."
        # Pass the project root explicitly: init may be invoked from outside the project
        # root, and doctor.py defaults to cwd -- --root pins it to the right directory.
        & $pyCmd.Source $doctorPy --root $scriptRoot
        if ($LASTEXITCODE -ne 0) {
            Write-Output "doctor reported issues above -- fix before your first working session."
        }
        Write-Output ""
    } else {
        Write-Output "python not found -- install it, then run: python .claude/skills/doctor/doctor.py (the /doctor skill)"
        Write-Output ""
    }
}

Write-Output "New to this harness? core/onboarding/TOUR.md is a staged walkthrough (WHY / WHAT /"
Write-Output "FIRST-WEEK / WHEN-X-HAPPENS); core/onboarding/SYSTEM-MAP.html is an interactive map"
Write-Output "(double-click it to open). Ask /orient in a Claude session for ad-hoc questions --"
Write-Output "answers cite the installed docs."
Write-Output ""

Write-Output "Recommended next steps:"
Write-Output "  git init"
Write-Output "  git add -A"
Write-Output "  git commit -m `"instantiated rheoscope-harness v$($py.template_version)`""
Write-Output ""
Write-Output "Then open INIT.md and run through the manual kickoff interview in a fresh Claude session."

exit 0
