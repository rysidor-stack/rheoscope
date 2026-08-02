# descriptors/ — golden task descriptors (ECO-1 / GOLD family)

This directory is where an instance's `golden-descriptors.yaml` lives once it has one.

**What a golden descriptor is.** A hand-authored "golden task" — a short natural-language
build/fix/query prompt plus an answer key (`required_views`, `expected_outcome`, and for
negative cases the expected refusal reason) — used as a regression fixture for the memory
engine's routing and packet-assembly layer (`assemble.py`). ECO-1 (PACKET-RECALL) requires
100% required-view recall across every descriptor, plus exact behavior on the negative
cases (stale-T1 refusal, unrouted input). See `assemble.py --self-test` and the GOLD family
discipline in `capabilities/knowledge-os/extracted/engine/memory-engine-v3-test-plan.md`.

**Why one doesn't ship generically.** A golden descriptor is only meaningful against a real,
populated wiki: its text must contain a literal alias from the instance's own `entities.yaml`,
and its `required_views` must name views that actually exist in that instance's live catalog
at authoring time. A descriptor written against one instance's corpus answers a question about
that corpus — it cannot be verified, and would silently rot, against any other instance's wiki.

**What to do here.** Once your instance's wiki has real views and a populated `entities.yaml`,
author your own `descriptors/golden-descriptors.yaml` following the ECO-1/GOLD-2 discipline:
answer keys hand-verified at authoring time, updated in the same commit as any change to the
views or entities they depend on.
