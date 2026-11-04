# Contributing

Use the locked checkout installation described in README. Run pytest and the frontend checks before submitting a change. Regenerate contracts and TypeScript types when changing the API. Include a behaviour test for a calculation change, with its physical assumptions and comparison tolerance.

Python is formatted with Ruff 0.17.3 using `ruff format src tests deploy`. Keep the existing import order. Build outputs and property tables are generated locally and excluded from Git.

Report a model equation or reference-data issue to the BoreTHM project. Application issues can be reported in this repository with a small reproducible input and the installed versions.
