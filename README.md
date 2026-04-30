# Forever Fair 3.0

This repository is the release-1 scaffold for a jurisdiction-neutral groundwater smart-market application.

Release 1 focus:
- real website front end,
- multipart bid entry,
- transparent auction dashboard,
- linearized groundwater constraints via a response matrix,
- lean documentation that can grow with the code.

## Current status

The initial scaffold includes:
- a FastAPI application with server-rendered pages,
- a file-backed SQLite seed dataset,
- a minimal market-clearing service using PuLP,
- dashboard, bid-entry, and results views,
- a small unit test for the clearing flow.

## Run locally

1. Create and activate a Python 3.11+ environment.
2. Install the package in editable mode:

```bash
pip install -e .[dev]
```

## License

Copyright 2026 John F. Raffensperger. All rights reserved. Unauthorised copying or redistribution is prohibited.

3. Start the app:

```bash
uvicorn main:app --reload
```

4. Open `http://127.0.0.1:8000`.

## Release-1 scope note

The public framing is jurisdiction neutral. Texas and Utah remain documentation examples for rights conversion rather than hard-coded legal logic.
