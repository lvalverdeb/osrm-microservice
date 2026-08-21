# Claude Configuration Override
- Never append co-author credits, attribution lines, or footers to git commits.
- Force `gitAttribution` and `includeCoAuthoredBy` to false.

## Build and Run Commands
- **Local Application (FastAPI)**: Run directly using `uvicorn src.app.main:app --reload --host 0.0.0.0 --port 8000`
- **Deployment**: two options only, both under `deploy/` — see `docs/deployment.md`.
  - **Docker** (`deploy/docker/`): `make compose-up` / `compose-down` / `compose-logs` / `compose-health`.
    The compose file is not at the repo root, so a bare `docker compose up` will not
    find it; use the make targets, or
    `docker compose -f deploy/docker/docker-compose.yml -p osrm-microservice ...`.
  - **FreeBSD jail** (`deploy/freebsd/`): `make jail-up` / `jail-down` / `jail-logs` / `jail-health`.
- **Dependency Installation**: Always install within a virtual environment using `uv`:
  - Install dev dependencies: `uv pip install -e ".[dev]"`

## Test Commands
- **Run pytest suite**: `make test` or `uv run python -m pytest tests/ -q --tb=short`
- **Run specific test file**: `uv run pytest tests/test_vrp.py`
- **Run single test**: `uv run pytest tests/test_vrp.py -k "test_vrp_allocation_success"`

## Lint and Quality Commands
- **Lint check**: `make lint` or `uv run ruff check .`
- **Auto-fix lint issues**: `uv run ruff check . --fix`
- **Complexity/Architecture scan**: `make spaghetti` or `uv run spaghetti --package osrm-api-gateway=src/app --severity warning`
- **Vulnerability scan**: `make fenceline` or `fenceline --package osrm-api-gateway=src/app`

## Code Style & Conventions
- **Language & Framework**: Python 3.13+ and FastAPI.
- **Indentation**: 4 spaces (do not use tabs).
- **Naming Conventions**:
  - Variables, functions, methods: `snake_case`
  - Classes, custom exceptions: `PascalCase`
  - Constants/Globals: `SCREAMING_SNAKE_CASE`
- **Imports**: Formatted using `ruff`. Absolute imports from the `app` namespace (e.g., `from app.models.schemas import ...`).
- **Typing**: Use explicit and modern PEP 585/604 type annotations on all function signatures, return types, and fields.
- **Docstrings**: Adhere strictly to **Google Style Docstrings** for all new/modified public interfaces (includes `Args:`, `Returns:`, and `Raises:`).
- **Complexity**: Keep functions short and single-purpose (SRP). If a function body exceeds 20 lines, refactor it into smaller helper methods.
