---
trigger: manual
---

This rule applies only for Python development.

# Python Feature Layout Principles & Rules

When building applications using a Feature-Based Layout (Vertical Slicing), adhere to the following industry-standard principles to ensure the codebase remains highly maintainable, testable, and easy to refactor.

## 1. High Cohesion, Low Coupling
**Rule:** Code that changes together, stays together.
- When adding or modifying a feature (e.g., "Invoicing"), you should ideally only need to work within that specific feature's folder. 
- Avoid "god folders" like a top-level `models/` directory where a change to a single feature requires modifying files across the entire application structure.

## 2. Strict Feature Boundaries
**Rule:** Features must communicate through explicit public contracts (Services), never through internal implementation details.
- **NEVER** import a `Repository` or a database `Model` from one feature into another.
- If the `Audit` feature needs user data, it should call a method on the `IdentityService`, not query the `users` table directly.
- **Why?** This allows you to completely rewrite the internals of the Identity feature (e.g., moving it to a microservice) without breaking the Audit feature.

## 3. Core is for Plumbing, Not Business Logic
**Rule:** The `core/` directory is strictly for global infrastructure.
- **Allowed:** Database session factories, CORS configuration, global exception handlers, security dependencies (JWT parsing), environment variable loading.
- **Forbidden:** Any logic related to specific business rules (e.g., "calculating compliance risk").
- **Why?** Keeping `core/` thin prevents it from becoming a tangled mess of unrelated utilities.

## 4. Protect the API Contract with DTOs
**Rule:** Never leak internal representations (like SQLAlchemy models) to the API responses.
- Always use Data Transfer Objects (DTOs) defined via Pydantic (`schemas.py`) for input validation and output serialization in your `router.py`.
- **Why?** This decouples your database schema from your public API. You can rename a database column without forcing frontend clients to update their integrations.

## 5. Conscious use of `shared/`
**Rule:** The `shared/` folder is for application-agnostic primitives, not a dumping ground.
- **Allowed:** Pagination helpers, generic date formatters, base classes (e.g., SQLAlchemy `Base`), custom generic exceptions.
- **Forbidden:** Logic shared by only two specific features. If Feature A and Feature B share code, evaluate if that code belongs solely to A, solely to B, or if it represents a distinct new Feature C.

## 6. Dependency Injection
**Rule:** Services and Routers should not instantiate their own dependencies.
- Leverage FastAPI's `Depends()` heavily.
- Pass database sessions, configuration objects, or other services into your feature's `service.py` functions rather than importing them directly inside the function.
- **Why?** This makes unit testing exponentially easier, as you can easily pass mock objects during testing.

## 7. Model Segregation
**Rule:** Prefer keeping database models within the feature they belong to.
- Instead of a massive `app/infrastructure/adapters/database/models.py`, define `User` in `identity/models.py` and `AuditRecord` in `audit/models.py`.
- If you have complex SQLAlchemy relationships (Foreign Keys) crossing feature boundaries, document them clearly. In strict modular monoliths, prefer soft links (storing IDs instead of creating ORM relationships) to allow future extraction into microservices.