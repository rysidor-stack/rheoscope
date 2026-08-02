# The Materialization Doctrine

*Landed 2026-07-28 from the operator + Claude reasoning discussion of 2026-07-27/28
(phone session; five-pass run over the repo's four existing projections). The rule the
harness had been following by instinct, written down so it becomes deliberate.*

Prose is the source of truth; machine layers are earned, not designed.

**The rule.** When a prose stratum's implicit structure has been **queried — not read,
queried —** on two or more distinct occasions, materialize it as a derived projection: a
stdlib-only script that reads the sources and emits a regenerable artifact, never
hand-edited, marked as such. Every projection ships with a paired detect-only drift
sensor, or it does not ship. Projections are disposable — deleting one loses nothing —
and on any disagreement **the sources win**. Do not materialize ahead of demand: an
unqueried stratum's projection is maintenance debt wearing the costume of infrastructure.

**What counts as a query.** Reading a file is not a query. A query is needing an answer
that requires scanning many files to assemble — "which decisions are still open?",
"which raw notes were never absorbed?", "which articles link to this one?" The first
occurrence, do the scan and move on. The second occurrence is the signal: the question
will keep coming back, so it is now worth paying for the script.

**The three paid-for lessons** (each learned once; not to be re-debated):

1. **Materialize on second query-demand, never ahead of it.** Building ahead of demand
   produces infrastructure that must be maintained but isn't earning anything.
2. **Always pair it with a drift sensor.** A derived file can silently disagree with its
   sources, and a stale projection is worse than none because it is believed. A
   projection without its detect-only sensor is a defect, not a convenience.
3. **The sources win.** The projection is disposable and never hand-edited. The moment a
   projection becomes the thing people edit, there are two sources of truth and the
   prose-first design collapses.

**The trigger's measurement rail.** The trigger is judgment, not automation — but
judgment needs evidence. Sessions doing scan-shaped work record one `scan:` telemetry
line per distinct question in their receipt (see the compile and flight-plan skills);
recurrence of a question-shape across receipts is the recorded demand evidence a
projection decision cites. A detect-only sensor may propose materializations from that
evidence; the decision to build stays human.

**Instances in this project's lineage:** the decision inbox (`DECISIONS-PENDING.md`),
the staleness/conservation census, the entity vocabulary + its routing validator, the
derivation blocks + their checker. Each ships with its watchdog; each is regenerable;
none is hand-edited.

**Relation to the five-pass method:** the doctrine is a standing Pass-4 outcome — the
constrained decision that recurs. Its unconstrained ideal (structure written atomically
with prose, drift impossible, the source/projection boundary dissolved) is known and
deliberately not built: plain-markdown-in-git is load-bearing (durable, portable,
diffable, editable by any hand or model, survives every tool change). The doctrine is
the right patch for a substrate that cannot index itself; the ideal names the upgrade
path without commanding the jump.
