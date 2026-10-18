# StockEntry Guard project rules

- Read docs/ARCHITECTURE.md before changing module boundaries.
- Keep one modular monolith. No microservices, authentication, payments,
  forecasting, or analytics dashboards in this MVP.
- Domain uses only the standard library: no framework imports, ORM, SQL,
  Ollama, CSV parsing, environment access, or network/file I/O.
- Application depends on domain, schemas, and ports, never concrete adapters.
- Pydantic schemas belong in application, adapters in infrastructure, UI in ui,
  and dependency wiring in bootstrap.py. Never expose ORM objects through ports.
- AI output is untrusted candidate data. Product writes require explicit user
  approval. Revalidate and recheck duplicates after edits.
- Product writes and their events share one unit of work. Repositories never
  commit independently. Stored events are append-only and versioned.
- Default tests must run without Ollama, network, or a persistent database.
- Run python -m ruff check ., python -m ruff format --check .,
  python -m pytest, and python -m mypy in the project environment.
- Keep placeholders clearly marked. Do not implement beyond the requested scope.
- Never commit inventories, images, databases, secrets, or .env files.
- Follow the owner's RTK instructions: prefix shell commands with rtk,
  using rtk proxy for unsupported commands where RTK is installed.
