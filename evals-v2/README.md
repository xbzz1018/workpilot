# WorkPilot Evaluation Dataset v2

This is a synthetic dataset for checking Fact extraction, ReportClaim rendering, and evidence-linked work reports. It preserves the v1 source cases and split; the v1 snapshot remains unchanged.

| Split | Cases | Purpose |
| --- | ---: | --- |
| `dev/` | 10 | Tune and compare the baseline, single-model WorkPilot, heterogeneous WorkPilot, and Dev-only challenger. |
| `test/` | 20 | Evaluate the frozen configuration after Dev work. The [SHA-256 manifest](test-manifest.json) locks the Test inputs. |

Each case has a task definition, Markdown/TXT/CSV source records, and a `gold.json` reference. See the [main README](../README.md#评测) for the evaluation commands and the [recorded status](../README.md#已记录的验证状态) before interpreting any result. Real model outputs are kept in ignored `results/`; this repository does not contain a completed frozen Test result.
