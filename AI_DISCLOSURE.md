Tools used:
  Antigravity IDE (Google DeepMind), Claude Sonnet 4.6

Used for:
  - Code drafting (all modules)
  - Test generation
  - Design note drafting
  - README drafting
  - Failure test script generation

Not used for:
  - Final architecture decisions
  - Running judge-facing tests on Pi
  - Filling in measured resource values

Human review:
  Reviewed processing.py logic against spec §4 line-by-line.
  Ran pytest locally — 38 tests pass.
  Ran end-to-end test on Windows with fake_mock.py.
  Verified heart_rate output matches expected 60.0 bpm.

Date: 14 October 2026
