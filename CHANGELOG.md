# Changelog

All notable changes to this project are documented in this file.

## [0.1.0] - 2026-06-07

### Added

- Initial public-release documentation set:
  - `README.md` quick start, data setup, test command, known limitations, and project structure.
  - `.env.example` for local environment bootstrap.
  - Governance docs: `CONTRIBUTING.md`, `SECURITY.md`, and `CODE_OF_CONDUCT.md`.
- Repository governance controls:
  - `.github/CODEOWNERS` to require maintainer review when branch protection is enabled.

### Changed

- Demo setup logic in `src/SetupForeverFairDB.py` updated to avoid hard-coded trader IDs and prevent ID collisions in demonstration database initialization.
- Documentation pages aligned with actual module/file layout and security-hardening guidance links.
- Database documentation expanded to cover all schema tables and corrected field/type notes.

### Fixed

- Test failure related to duplicate trader IDs in demo database setup; pytest suite restored to passing status.
- Stale documentation references to non-existent files/modules.

### Removed

- Tracked generated SQLite artifacts from version control.
- Tracked generated `src/groundwater_smart_market.egg-info/` artifacts from version control.

### Notes

- This is a research/demo release and is not production-hardened.
- Authentication flows are demo-oriented by design; see docs for security hardening recommendations.
