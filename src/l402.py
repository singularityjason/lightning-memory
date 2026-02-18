"""L402 payment layer (Phase 3 stub).

This module will implement:
- L402 challenge/response for the hosted query service
- Lightning invoice generation and verification
- Macaroon-scoped budget controls
- Pay-per-query pricing (1-5 sats per operation)

For Phase 1, all operations are local and free.
"""

from __future__ import annotations

# Phase 3: L402 gateway integration
# - Aperture reverse proxy or custom L402 handler
# - Invoice generation via LND/CLN
# - Macaroon minting with spending caps
# - Budget tracking per agent pubkey
