# init-validate.ps1 -- Post-substitution & post-wiring validator (Windows).
#
# Runs AFTER init.ps1. Verifies the instantiated harness is clean:
#   - No {{...}} placeholders remain anywhere (Decision V2-2: no allow-list).
#   - No *.template files remain.
#   - project.yaml.instantiated_date is populated.
#   - capabilities/ has been removed from the target.
#   - Each enabled capability's runtime files exist at expected locations.
#
# Exits 0 on PASS, 1 on FAIL.

[CmdletBinding(SupportsShouldProcess = $true)]
param()

$ErrorActionPreference = 'Stop'
$scriptRoot = $PSScriptRoot
if (-not $scriptRoot) { $scriptRoot = (Get-Location).Path }

if ($WhatIfPreference) {
    Write-Output "init-validate.ps1: -WhatIf set; parse-only check passed."
    exit 0
}

$failures = New-Object System.Collections.Generic.List[string]
function AddFailure([string]$msg) { [void]$failures.Add($msg) }

# Load placeholder-scan exempt list (META docs that legitimately contain literal {{...}}).
# Config-driven via project.yaml.placeholder_scan_exempt (v1.1: was hardcoded).
$exempt = @()
$projectYamlForExempt = Join-Path $scriptRoot 'project.yaml'
if ((Test-Path $projectYamlForExempt) -and (Get-Module -ListAvailable powershell-yaml)) {
    Import-Module powershell-yaml -ErrorAction SilentlyContinue
    try {
        $pyExempt = ConvertFrom-Yaml (Get-Content $projectYamlForExempt -Raw -Encoding UTF8)
        if ($pyExempt -and $pyExempt.placeholder_scan_exempt) {
            $exempt = @($pyExempt.placeholder_scan_exempt | ForEach-Object { ([string]$_) -replace '\\','/' })
        }
    } catch { }
}
$gitDir = Join-Path $scriptRoot '.git'
function IsScanExempt([string]$fullPath) {
    $rel = ($fullPath.Substring($scriptRoot.Length).TrimStart([char]'\', [char]'/')) -replace '\\','/'
    foreach ($e in $exempt) {
        if ($rel -eq $e -or $rel.StartsWith($e)) { return $true }
    }
    return $false
}

# Dependency / generated-dir skip (v3.0-14): the {{...}} scan must not walk vendored/generated
# dirs (node_modules, .next, build, dist, ...) — third-party {{...}} would yield false failures.
# Single-sourced from project.yaml.dependency_scan_skip; matched on any path SEGMENT (these dir
# names appear at any depth), distinct from the prefix-matched exempt list. (.git is skipped below.)
# Read INDEPENDENTLY of the exempt block (mirrors init-validate.sh's own yq guard) and require an
# actual list (a scalar mis-author is ignored, like yq's '[]' on a scalar) so the two validators
# cannot diverge on malformed config. Match is CASE-SENSITIVE (-ccontains) to match bash's [[ == ]].
$depSkip = @()
if ((Test-Path $projectYamlForExempt) -and (Get-Module -ListAvailable powershell-yaml)) {
    try {
        $pyForDep = ConvertFrom-Yaml (Get-Content $projectYamlForExempt -Raw -Encoding UTF8)
        if ($pyForDep -and ($pyForDep.dependency_scan_skip -is [System.Collections.IList])) {
            $depSkip = @($pyForDep.dependency_scan_skip | ForEach-Object { [string]$_ })
        }
    } catch { }
}
function IsDepSkipped([string]$fullPath) {
    if ($depSkip.Count -eq 0) { return $false }
    $rel = ($fullPath.Substring($scriptRoot.Length).TrimStart([char]'\', [char]'/')) -replace '\\','/'
    foreach ($seg in $rel.Split('/')) {
        if ($depSkip -ccontains $seg) { return $true }
    }
    return $false
}

# 1. No remaining {{...}} placeholders (skipping .git/ and configured exemptions)
$worktreesDir = Join-Path $scriptRoot '.claude/worktrees'
$binaryExts = @('.png','.jpg','.jpeg','.gif','.ico','.pdf','.zip','.woff','.woff2','.ttf','.eot','.mp3','.mp4','.docx','.xlsx','.pptx')
$placeholderHits = Get-ChildItem -Path $scriptRoot -Recurse -File -ErrorAction SilentlyContinue |
    Where-Object { -not $_.FullName.StartsWith($gitDir) -and -not $_.FullName.StartsWith($worktreesDir) -and ($binaryExts -notcontains $_.Extension.ToLower()) -and -not (IsScanExempt $_.FullName) -and -not (IsDepSkipped $_.FullName) } |
    ForEach-Object {
        # .claude/worktrees/ + binary extensions excluded (v3.0-61): tool-managed scratch
        # worktrees are git-excluded debris (92 false failures on a live instance); binary
        # payloads cannot carry a resolvable placeholder.
        $file = $_.FullName
        $content = [System.IO.File]::ReadAllText($file)
        $matches = [regex]::Matches($content, '\{\{[a-zA-Z0-9_.\-]+\}\}')
        foreach ($m in $matches) {
            [PSCustomObject]@{ File = $file; Placeholder = $m.Value }
        }
    }
foreach ($hit in $placeholderHits) {
    AddFailure "Unresolved placeholder $($hit.Placeholder) in $($hit.File)"
}

# 2. No *.template files remain
$leftoverTemplates = Get-ChildItem -Path $scriptRoot -Recurse -File -Filter '*.template' -ErrorAction SilentlyContinue
foreach ($t in $leftoverTemplates) {
    AddFailure "Leftover .template file: $($t.FullName)"
}

# 3. project.yaml exists and instantiated_date populated
$projectYaml = Join-Path $scriptRoot 'project.yaml'
if (-not (Test-Path $projectYaml)) {
    AddFailure "project.yaml not found at $projectYaml"
} else {
    if (-not (Get-Module -ListAvailable powershell-yaml)) {
        AddFailure "powershell-yaml module required but not installed"
    } else {
        Import-Module powershell-yaml -ErrorAction Stop
        $py = ConvertFrom-Yaml (Get-Content $projectYaml -Raw -Encoding UTF8)
        if (-not $py.instantiated_date -or ([string]$py.instantiated_date).Length -eq 0) {
            AddFailure "project.yaml.instantiated_date is empty - init did not stamp metadata"
        }

        # 5. Per-capability runtime checks
        # stress-testing: retired 2026-07-10 -- /preflight ships as a core skill
        # (superseded /grill). The key is still accepted (schema parity with existing
        # project.yaml files) but wires nothing, so no runtime paths are required.
        $expected = @{
            'knowledge-os'        = @('.claude/skills/compile', '.claude/skills/audit', 'docs/wiki-schema.md', 'deploy/check-frontmatter.py', 'deploy/check-derivation.py', '.claude/skills/discover', 'docs/engine/memory-engine-v3-spec.md', 'docs/engine/OPERATIONS.md', 'deploy/compile-v2.py', 'deploy/staleness.py', 'deploy/register-intake.py', 'deploy/entities.yaml', 'deploy/origin-config.yaml', 'deploy/check-manifest.py', 'deploy/manifest-layers.yaml', 'wiki/HEALTH.md', 'wiki/REVIEW.md', 'wiki/INDEX.md', 'SESSION-BRIEFING.md')
            'stress-testing'      = @()
            'code-conventions'    = @('methodology/code-conventions.examples')
        }
        foreach ($cap in $expected.Keys) {
            if (-not $py.capabilities -or -not $py.capabilities.ContainsKey($cap)) { continue }
            if ([bool]$py.capabilities[$cap]) {
                foreach ($p in $expected[$cap]) {
                    $full = Join-Path $scriptRoot $p
                    if (-not (Test-Path $full)) {
                        AddFailure "Capability $cap is enabled but runtime path missing: $p"
                    }
                }
            }
        }
    }
}

# 4. capabilities/ directory MUST NOT exist post-init
$capDir = Join-Path $scriptRoot 'capabilities'
if (Test-Path $capDir) {
    AddFailure "capabilities/ directory still exists at $capDir - init did not delete it"
}

# 6. Required directories exist
$requiredDirs = @('core/methodology', 'core/governance', 'core/handoffs', 'core/security/hooks', 'core/onboarding')
foreach ($d in $requiredDirs) {
    $full = Join-Path $scriptRoot $d
    if (-not (Test-Path $full)) {
        AddFailure "Required directory missing: $d"
    }
}

# 7. Core skills MUST exist post-init (always wired, independent of capability toggles).
#    core/skills/ is consumed at wiring time, so it must NOT remain either.
#    This list mirrors the shipped core/skills/ set (init wires every entry unconditionally);
#    v3.0-78 collapsed handoff-author + handoff-receive into the single /handoff skill.
$coreSkills = @('flight-plan', 'handoff', 'handoff-close', 'log-backlog', 'cross-check', 'cross-check-loop', 'preflight', 'doctor', 'orient', 'reason', 'conformance', 'sweep', 'standing-loop')
foreach ($s in $coreSkills) {
    $skillFile = Join-Path $scriptRoot ".claude/skills/$s/SKILL.md"
    if (-not (Test-Path $skillFile)) {
        AddFailure "Core skill missing: .claude/skills/$s/SKILL.md"
    }
}
# bridge is the transport library the cross-check skills call into -- it has no SKILL.md
# of its own (it's not invoked directly as a skill), so it's checked by its entry script.
$bridgeEntry = Join-Path $scriptRoot '.claude/skills/bridge/verify-cli.js'
if (-not (Test-Path $bridgeEntry)) {
    AddFailure "Core skill missing: .claude/skills/bridge/verify-cli.js"
}
$coreSkillsLeftover = Join-Path $scriptRoot 'core/skills'
if (Test-Path $coreSkillsLeftover) {
    AddFailure "core/skills/ still exists - init did not consume it into .claude/skills/"
}

# 8. VERSION must NOT exist post-init (consumed at instantiation; the project's single
#    version source is project.yaml.template_version, per v2.0 #10a).
$versionLeftover = Join-Path $scriptRoot 'VERSION'
if (Test-Path $versionLeftover -PathType Leaf) {
    AddFailure "VERSION file still exists - init did not consume it (project.yaml.template_version is the project's version source)"
}

# Result
if ($failures.Count -gt 0) {
    Write-Output "init-validate: FAIL ($($failures.Count) issue(s))"
    Write-Output "Setup didn't finish cleanly. Easiest fix: paste this whole output into a Claude session opened in this folder and ask it to finish the setup, then run this check again."
    foreach ($f in $failures) { Write-Output "  - $f" }
    exit 1
} else {
    Write-Output "init-validate: PASS"
    exit 0
}
