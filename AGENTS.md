# Repository Guidelines

## Project Structure

VocaFSRS is a self-hosted vocabulary review application. `backend/app/` contains FastAPI routers, services, SQLAlchemy models, and shared domain constants. Database migrations live in `backend/migrations/`; import utilities are in `backend/scripts/`; backend tests are in `backend/tests/`. `frontend/src/` contains React pages, components, hooks, API services, IndexedDB code, and styles. Browser tests are in `frontend/tests/`. Installer scripts are at the repository root; sample import files are under `docs/`.

## Build, Test, and Development

- `cd backend && VOCAB_ENV=test uv run pytest -q` runs backend tests. The test fixture uses in-memory SQLite; never point test or seed commands at personal `backend/data/vocab.db`.
- `cd frontend && npm ci` installs locked dependencies.
- `npm run lint` checks TypeScript/React; `npm run check:css` validates CSS selectors; `npm run build` type-checks and creates the production frontend.
- `npm run test:e2e` runs Playwright flows with isolated test services. `npm run test:release` checks the production build served with the API by one process.
- For local development, run `PYTHONPATH=. uv run uvicorn main:app --host 127.0.0.1 --port 8000` from `backend/`, then `npm run dev` from `frontend/`.

## Code and Test Conventions

Use four spaces in Python and follow the surrounding TypeScript formatting. Use `snake_case` for Python functions and variables, `PascalCase` for React components, and `use*` names for hooks. Keep API contracts and shared domain rules in their existing modules; avoid duplicating scheduling or import logic. Name backend tests `test_*.py` and Playwright tests `*.spec.ts`. Add focused tests for behavior changes and run the relevant checks above.

## Commits and Pull Requests

Recent commits use short imperative subjects such as `Fix installer dataset path resolution` and `Remove unfinished confusion analysis UI`; keep that style. Pull requests should explain the user-visible change, list verification commands, link related issues when available, and include screenshots for UI changes.

## Configuration and Data

Keep API keys and webhook URLs in the local `backend/.env`; never commit secrets. Personal vocabulary files and runtime databases are ignored by Git. Use `docs/sample-vocabulary.txt` or `.csv` for examples, and do not add personal datasets or database contents to tests or commits.
