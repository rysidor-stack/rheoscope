# Verify-leg packet (engine sidecar)

Fixture content only. Proves receipts/verify/packets/*.md is EXCLUDED from
check-loop-state.py's extension check (c) receipts population -- it must NOT be
required to carry a registration record (B-2, steady-state-ops brief 2026-07-08).
This file is DELIBERATELY not listed in _registrations.json; the case expects
exit 0 (PASS) regardless.
