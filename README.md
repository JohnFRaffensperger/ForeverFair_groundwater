# Forever Fair 3.0

This version of Forever Fair is a research prototype for groundwater smart-market operation.
It provides a FastAPI web interface, SQLite-backed state, and auction-clearing logic using linear programming.

## Quick start

1. Create and activate a Python 3.11+ environment.
2. Install dependencies:

```bash
pip install -e .[dev]
```

3. Configure catchment selection (see Data setup below).
4. Start the server:

```bash
python -m uvicorn src.web.ForeverFairPages:app --reload
```

5. Open `http://127.0.0.1:8000/` in your browser.

## Data setup

The server expects a catchment name in an environment variable.

1. Create a `.env` file in the repository root.
2. Add:

```env
FOREVER_FAIR_CATCHMENT=Tianqiao
```

You can copy the included template file `.env.example` to `.env` and adjust the value.

3. Ensure the named catchment folder exists under `Catchment_data/`.

Notes:
- `Tianqiao` is the default demonstration dataset.
- Replace the value with another folder name under `Catchment_data/` if needed.

## Run tests

Use:

```bash
pytest -q
```

## Known limitations

This repository is a research demo, not a production deployment template.

- Authentication is demo-oriented: trader access is selected from a list and super-login paths exist for testing.
- Administrator workflows are not protected with production-grade per-user authentication/authorization.
- Security hardening, secrets management, and deployment controls are intentionally out of scope for this prototype.

For production hardening recommendations, see the Security hardening section in the database docs page served at `/database-documentation#security-hardening`.

## Project structure

- `src/web/ForeverFairPages.py`: FastAPI routes and page wiring.
- `src/ForeverFairData.py`: SQLite data access and persistence layer.
- `src/AuctionController.py`: market-clearing model construction and solve flow.
- `src/BiddingController.py`: bid handling and bid-related helpers.
- `src/SetupForeverFairDB.py`: schema setup and data import/bootstrap utilities.
- `Catchment_data/`: demonstration and catchment-specific input datasets.
- `tests/`: pytest suite for regression checks.

## Current release focus

Release 1 emphasis:
- usable website front end,
- multipart bid entry,
- transparent auction dashboard,
- linearized groundwater constraints via response matrix,
- concise but accurate documentation.

The public framing is jurisdiction neutral. This code could work for any catchment with a valid hydrology model. 

## License

This project is source-available under the Forever Fair Public Interest License v1.0.

- No-charge use is permitted for government entities and non-commercial research/education.
- Any for-profit use requires a separate commercial license from John F. Raffensperger.

See [LICENSE](LICENSE) for full terms.

