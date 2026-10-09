#!/usr/bin/env python3
# Forwarding stub (v3.0-236): the generator lives in core -- core/skills/doctor/ in the template, .claude/skills/doctor/ in a project. This path stays so `deploy/gen-skill-adapters.py` (and /doctor's skill-adapters check) keep resolving.
import os, runpy, sys; _d = os.path.dirname(os.path.abspath(__file__))
while True:
    for _rel in (".claude/skills/doctor/gen-skill-adapters.py", "core/skills/doctor/gen-skill-adapters.py"):
        _p = os.path.join(_d, _rel)
        if os.path.isfile(_p): sys.argv[0] = _p; runpy.run_path(_p, run_name="__main__"); raise SystemExit(0)
    _up = os.path.dirname(_d)
    if _up == _d: sys.exit("gen-skill-adapters stub: core generator not found (.claude/skills/doctor/ or core/skills/doctor/)")
    _d = _up
