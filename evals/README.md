# WorkPilot Evaluation Dataset

The benchmark contains 30 synthetic work packages: 10 development cases and a frozen set of 20 test cases. Each case has three UTF-8 source files, a `task.json`, and a `gold.json` with deterministic evidence mappings.

Scenario totals are fixed at 8 regular summaries, 6 cross-file deduplication cases, 6 numeric conflicts, 5 missing-evidence cases, and 5 long/noisy-material cases. Report types alternate so that the complete dataset contains 15 work summaries and 15 performance reviews.

`test-manifest.json` records SHA-256 for every frozen Test file. Prompt, Skill, or code iteration may use Dev only. Any manifest change creates a new benchmark version and invalidates earlier Test claims.

The source materials and identities are synthetic. No private work record is included.
