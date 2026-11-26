# WorkPilot Evaluation Dataset v2

This is a synthetic dataset for checking Fact extraction, ReportClaim rendering, and evidence-linked work reports. It preserves the v1 source cases and split; the v1 snapshot remains unchanged.

| Split | Cases | Purpose |
| --- | ---: | --- |
| `dev/` | 10 | Tune and compare the baseline, single-model WorkPilot, heterogeneous WorkPilot, and Dev-only challenger. |
| `test/` | 20 | Evaluate the frozen configuration after Dev work. The [SHA-256 manifest](test-manifest.json) locks the Test inputs. |

Each case has a task definition, Markdown/TXT/CSV source records, and a `gold.json` reference. See the [main README](../README.md#评测) for evaluation commands. Real model outputs are written to `results/` by default.

## Resume a Dev run

Start a run from the repository root and give it an explicit ID:

```powershell
workpilot eval --split dev --system dev_matrix --run-id dev-first --max-model-calls 400
```

If it stops before all cases finish, rerun with `--resume`:

```powershell
workpilot eval --split dev --system dev_matrix --run-id dev-first --max-model-calls 400 --resume
```

Reuse the original `--output` directory and `--run-id` so WorkPilot can find `results/dev-first/run.json` (shown for the default output directory). Keep the split, system selection, `--case` filters, and evaluation configuration the same. Use a new run ID when changing the inputs or configuration.
