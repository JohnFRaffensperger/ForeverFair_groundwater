# Contributing to Forever Fair

Thank you for contributing.
This project is a research prototype; changes should prioritize clarity, reproducibility, and correctness.

## Development setup

1. Create and activate a Python 3.11+ virtual environment.
2. Install project dependencies:

```bash
pip install -e .[dev]
```

3. Create a `.env` file at repository root (or copy from `.env.example`) and set:

```env
FOREVER_FAIR_CATCHMENT=Tianqiao
```

4. Start the app locally (optional for UI changes):

```bash
python -m uvicorn src.web.ForeverFairPages:app --reload
```

## Running tests

Run the test suite before opening a pull request:

```bash
pytest -q
```

If tests fail, include the root cause and fix in the same pull request.

## Pull request expectations

1. Keep pull requests focused and small enough to review.
2. Include a clear description of:
   - what changed,
   - why it changed,
   - how it was validated.
3. Update documentation when behavior, configuration, or workflow changes.
4. Avoid committing generated artifacts, local databases, secrets, or local environment files.
5. Ensure CI and tests are green before requesting review.

## Maintainer control and merge policy

- Contributions are welcome via pull requests.
- The project maintainer controls what is merged.
- Direct pushes to the protected main branch should be disabled in GitHub settings.
- Merging requires maintainer approval and required status checks.

Recommended GitHub branch protection settings for `main`:

1. Require a pull request before merging.
2. Require approvals.
3. Require review from Code Owners.
4. Require conversation resolution before merging.
5. Require status checks to pass.
6. Disable force pushes and branch deletion.

## Coding and review guidance

- Prefer explicit, readable logic over clever shortcuts.
- Keep schema and documentation synchronized.
- For UI text, keep demo-only limitations explicit where relevant.
- Add or update tests for behavior changes.

## Licensing reminder

By contributing, you agree that your contributions are provided under the repository license in [LICENSE](LICENSE).
