# Audit Log

Tracks open items, resolved items, and known risks.

## Open

### A-001: Licensing decision pending
- **Filed:** 2026-08-04
- **Status:** Awaiting operator decision
- **Description:** MLB Stats API, Open-Meteo free tier, and RotoWire
  all carry non-commercial terms. The operator sells picks. Decision
  needed before Phase 1. See `docs/LICENSING.md`.

### A-002: Historical strikeout prop lines not yet sourced
- **Filed:** 2026-08-04
- **Status:** Open
- **Description:** Phase 3 requires backtesting against real DraftKings
  strikeout prop lines. No historical source identified yet. DK's JSON
  serves current lines only; The Odds API historical endpoint is paid.
  Forward-collecting live lines starting now is the fallback.

### A-003: Park factor CSV returns HTML
- **Filed:** 2026-08-04
- **Status:** Open
- **Description:** Baseball Savant's park-factors leaderboard returns
  HTML (JS-rendered table) when `&csv=true` is appended. Need to parse
  embedded JSON or use the venue-specific endpoint.

## Resolved

(None yet.)
