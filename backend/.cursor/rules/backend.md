# Backend Rules

## Architecture

* Follow strict layered architecture:

  * `api/` → HTTP layer only
  * `core/` → business logic (NO service imports)
  * `services/` → provider implementations
* Do not break module boundaries
* Prefer composition over tight coupling

---

## API Design

* Follow REST conventions
* Keep endpoints thin (no business logic)
* Use `/api/v1/` for public routes and `/api/internal/` for internal
* Always return structured, consistent responses

---

## Validation & Data Models

* Validate all inputs using Pydantic models
* Never trust raw request data
* Use explicit typing (avoid `Any`)
* Separate API schemas from internal models when needed

---

## Async & Performance

* Use `async/await` for I/O operations
* Avoid blocking calls inside async routes
* Keep functions small and efficient

---

## Services & Providers

* Use service + provider pattern
* Providers must:

  * live under `services/{domain}/providers/`
  * extend a base class
  * be stateless and modular
* Reuse existing providers as templates

---

## Configuration

* Do not hardcode values
* Use centralized Settings
* Store provider configs in `CONFIG_DIR` (JSON)
* Load configs via existing loaders

---

## Code Quality

* Code must pass PyLint
* Prefer readability over cleverness
* Keep functions small and focused
* Avoid deep nesting and duplication

---

## Logging

* Use centralized logging system
* No print statements
* Do not log sensitive data

---

## Security

* Validate JWTs using existing security module
* Never trust client-provided identity
* Protect internal endpoints

---

## Development Workflow

* Reuse existing patterns before creating new ones
* Copy and adapt similar implementations
* Keep changes minimal and consistent

---

## Anti-Patterns

* Business logic in routes
* Cross-layer imports
* Hardcoded configs
* Large, monolithic functions

---

## Principle

Consistency over complexity.
Follow existing patterns in the codebase.

