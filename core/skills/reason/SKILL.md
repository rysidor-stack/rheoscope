---
name: reason
description: Run a Five-Pass reasoning run (or a deliberate subset) on a named question, fix, artifact, or decision — landscape, break template, unconstrained ideal, constrained decision, totality — with per-pass exit tests, rejects shown, and every finding landed as a record. Use when the operator says "reason on this", "run a reasoning pass", "step back", or before building any non-trivial fix.
---

# /reason

The manual trigger for the Five-Pass Method, as a protocol. The doctrine lives at `core/methodology/five-pass-method.md` — this skill never restates it (single-home rule); it governs the *run*: what gets asked, what each pass must survive, and where the findings land. The ambient wirings (kickoff Step 2j, `/preflight` § 4) fire the passes at fixed protocol points; `/reason` is for everywhere else — the mid-stream "wait, step back" that fixed protocol points can't anticipate.

Why a protocol at all: the depth of the response is set by the depth of the ask. An unstructured "think about this" gets the default pass. This skill makes the deep ask repeatable.

## Protocol

### 1. Name the subject and pick the passes

State the subject as one sentence. Classify it, and declare the pass plan **with skips named and reasoned** — a skip is recorded, never silent (doctrine § When to skip):

- **Single open decision** → passes 1–4; pass 5 only if related decisions already exist to synthesize against.
- **A proposed fix or design about to be built** → passes 2–4 heavy (the landscape usually already exists in the proposal); pass 2 is the point — it must attack the fix's framing, not summarize it.
- **Step-back / "is there a bigger lesson"** → pass 5 is the destination; passes 1–2 run over the *set* of recent decisions/artifacts as the field.

### 2. Ground before reasoning

Read the artifacts the subject touches before any pass runs — reasoning over unread evidence is the default pass wearing a costume. Read-only evidence legs may fan out per `/preflight` step-2 conventions (cheapest adequate tier, self-contained prompts, agents never spawn agents, judgment stays in this session).

### 3. Run the passes

In whatever order the subject demands — the passes are tools, not a liturgy. Each pass closes only when its **Done-when** from the doctrine holds:

| Pass | Closes when |
|---|---|
| 1 Landscape | Every serious option named and described, unranked |
| 2 Break Template | At least one Pass-1 assumption named and tested — **if nothing uncomfortable surfaced, the pass has not run yet**; either surface it or state explicitly why the framing survived attack |
| 3 Unconstrained | The ideal stated + the specific gap to what will actually be built |
| 4 Constrained | A decision recorded with the constraint that drove it |
| 5 Totality | At least one property of the combined system named that no single pass could surface — or the explicit attestation that none was found |

A later pass that contradicts an earlier one wins, and the kill is recorded in the output ("pass 2 killed X from the proposal because Y") — killed conclusions are findings, not noise.

### 4. Output discipline

One section per pass that ran, in run order. Rejects and killed alternatives shown with reasons (the reject pile is evidence — an empty one on a generative subject means step 3 of this protocol was skipped). RTDT governs throughout: passes 3 and 5 inform the map, never move the destination.

### 5. Land the findings

A run that ends in chat prose is half a run. Route every finding to its record, and close the run by stating where each landed:

- **Harness/template defect or enhancement** → `/log-backlog`, per the backlog discipline.
- **Revision of an existing backlog entry or plan** → amend that entry/artifact in place, provenance-noted ("revised by five-pass run YYYY-MM-DD").
- **Project knowledge** (when knowledge-os is enabled) → a raw intake file per `docs/wiki-schema.md`; the run itself is a process record only if its findings landed elsewhere.
- **A T1 / hard-to-reverse decision that pass 4 wants to lock** → never lock it here; capture what the run learned and hand to `/handoff` (the decision-lock firewall applies to reasoning runs like any session).

## Boundaries

- **Read-only** except the records named in step 5.
- **Never build the fix it just reasoned about in the same breath** — reasoning and building are separate moves; the run ends with its findings landed and, at most, a proposal.
- The verifier backstop is available, not implied: a load-bearing conclusion can ride `/cross-check` as one falsifiable claim; the run's prose is never its own evidence.

---

*Doctrine: `core/methodology/five-pass-method.md` (per-pass definitions, Done-when criteria, skip table, refusability principles). This skill is the invocation contract only.*
