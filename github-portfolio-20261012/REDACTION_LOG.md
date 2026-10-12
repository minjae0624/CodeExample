# Redaction log (for Minjae; do not commit this file, it is in .gitignore)

Built 2026-10-12 from copies. Nothing under D:\minjae was changed. Every change was an exact, counted replacement;
line numbers refer to the ORIGINAL files.

| file in this folder | original line | change |
|---|---|---|
| `co2-forecasting/llm_serialize.py` | 3 | internal design-document reference removed |
| `co2-forecasting/llm_serialize.py` | 32 | internal license line removed |
| `co2-forecasting/llm_serialize.py` | 43 | internal section reference removed |
| `co2-forecasting/llm_serialize.py` | 53 | internal section reference removed |
| `co2-forecasting/llm_serialize.py` | 56 | internal section reference removed |
| `co2-forecasting/llm_serialize.py` | 66 | internal section reference removed |
| `co2-forecasting/llm_serialize.py` | 82 | internal section reference removed |
| `co2-forecasting/llm_serialize.py` | 102 | internal design-document reference removed |
| `co2-forecasting/llm_serialize.py` | 124 | internal section reference removed |
| `co2-forecasting/llm_serialize.py` | 168 | internal section reference removed |
| `co2-forecasting/llm_serialize.py` | 219 | internal section reference removed |
| `co2-forecasting/llm_serialize.py` | 222 | internal section reference removed |
| `co2-forecasting/llm_serialize.py` | 234 | internal section reference removed |
| `co2-forecasting/llm_serialize.py` | 266 | internal section reference removed |
| `co2-forecasting/llm_serialize.py` | 278 | internal section reference removed |
| `co2-forecasting/llm_serialize.py` | 375 | internal section reference removed |
| `co2-forecasting/llm_serialize.py` | 386 | internal design-document reference removed |
| `co2-forecasting/llm_serialize.py` | 501 | internal section reference removed |
| `co2-forecasting/llm_serialize.py` | 514 | internal section reference removed |
| `co2-forecasting/llm_serialize.py` | 769 | hint at an unpublished error level removed |
| `co2-forecasting/llm_serialize.py` | 776 | hint at an unpublished error level removed from the self-test printout |
| `co2-forecasting/llm_serialize.py` | 819 | internal design-document reference removed |
| `co2-forecasting/llm_serialize.py` | 239 | internal dataset-version code replaced |
| `co2-forecasting/llm_serialize.py` | 684 | internal dataset-version code replaced |
| `co2-forecasting/llm_serialize.py` | 811 | internal dataset-version code replaced |
| `co2-forecasting/llm_serialize.py` | 817 | internal dataset-version code replaced |
| `co2-forecasting/llm_serialize.py` | 821 | internal dataset-version code replaced in the self-test printout |
| `co2-forecasting/llm_serialize.py` | 825 | internal dataset-version code replaced in the self-test printout |
| `co2-forecasting/target_substitution.py` | - | copied unchanged (no private information found) |
| `co2-forecasting/metrics.py` | - | copied unchanged (no private information found) |
| `hpc/compute_energy.py` | 4 | cluster project path and allocation name removed from the usage lines |
| `hpc/compute_energy.py` | 38 | cluster node names removed |
| `hpc/compute_energy.py` | 82 | cluster name removed |
| `stats/paired_tests.py` | 3 | dissertation chapter reference replaced by a plain description |
| `figure-tools/style_b.py` | 1 | manuscript names, a dated request note and the old font history removed from the module docstring |
| `figure-tools/style_b.py` | 39 | machine-specific font paths replaced by a font-folder search ($FIG_FONT_DIR first) with a DejaVu Sans fallback |
| `figure-tools/style_b.py` | 52 | rcParams use the resolved font family (needed for the fallback) |
| `figure-tools/style_b.py` | 121 | internal project-history note replaced |
| `figure-tools/style_b.py` | 141 | manuscript name removed |
| `figure-tools/style_b.py` | 198 | manuscript figure name and internal round tags removed |
| `figure-tools/style_b.py` | 224 | manuscript figure labels, file names and internal round tags removed |
| `figure-tools/style_b.py` | 227 | manuscript figure label removed |
| `figure-tools/style_b.py` | 239 | manuscript figure label removed |
| `figure-tools/style_b.py` | 243 | manuscript figure labels removed |
| `figure-tools/style_b.py` | 244 | manuscript figure label and internal round tag removed |
| `figure-tools/style_b.py` | 247 | machine-specific Consolas paths replaced by the resolved font folder; label and round tag removed |
| `figure-tools/style_b.py` | 249 | manuscript figure label removed |
| `figure-tools/style_b.py` | 252 | manuscript figure file name removed |
| `figure-tools/style_b.py` | 254 | manuscript figure file name removed |
| `figure-tools/style_b.py` | 256 | manuscript figure label and internal round tag removed |
| `figure-tools/style_b.py` | 257 | manuscript figure label removed |
| `figure-tools/style_b.py` | 258 | manuscript figure label removed |
| `figure-tools/style_b.py` | 260 | manuscript figure label removed |
| `figure-tools/style_b.py` | 262 | internal round tag removed |
| `figure-tools/style_b.py` | 263 | manuscript figure label removed |
| `figure-tools/figcheck_b.py` | 1 | manuscript name and internal round tag removed from the module docstring |
| `digital-twin/decision_logic.py` | 44 | comment no longer invites hardcoding the key |
| `digital-twin/decision_logic.py` | 1179 | internal spec section reference removed |
| `digital-twin/decision_logic.py` | 35 | database host (a public IP) replaced by $DB_HOST |
| `digital-twin/decision_logic.py` | 36 | database user (personal username) replaced by $DB_USER |
| `digital-twin/decision_logic.py` | 37 | database password replaced by $DB_PASSWORD |
| `digital-twin/decision_logic.py` | 38 | database name replaced by $DB_NAME |
| `digital-twin/decision_logic.py` | 45 | hardcoded Anthropic API key fallback removed |
| `digital-twin/decision_logic.py` | 55 | tunnel hostname replaced by $FEEDBACK_UI_URL |
| `digital-twin/decision_logic.py` | 56 | notification shared secret replaced by $NOTIFY_API_KEY |
| `digital-twin/.env.example` | - | new: the environment variables decision_logic.py now reads, with empty values |
| `f1-tracker/ (all files)` | - | 29 files copied unchanged (node_modules and dist left out; no secrets found) |

## Checks run on this folder
- Every Python file compiles. `llm_serialize.py` passes its self-test. `compute_energy.py` and `paired_tests.py` print `--help`.
- `style_b.py` loads Century Gothic when installed and falls back to DejaVu Sans when it is not (both tested).
- Privacy scan of every file: no IP address, machine path, username, cluster or allocation name, tunnel host, key-shaped
  secret, email address or internal round tag.
- None of the seven original credential values from `decision_logic.py` appears anywhere in this folder.

## Before you publish
1. Rotate the platform credentials. The originals are still hardcoded in the source folders (database password, Anthropic
   key and notify key in decision_logic.py; VAPID, VeSync and Airthings secrets in other platform files). Removing them from
   this copy does not make the old values safe.
2. Add a LICENSE file (the co2-forecasting project settings declare MIT).
3. `metrics.py` has two method issues: `bootstrap_ci` resamples overlapping windows independently, so its intervals are
   too narrow, and `mase` is the test-set MAE ratio against persistence rather than the standard MASE. Fix or document
   them before publishing that file.
4. `target_substitution.py` imports `make_windows` from the full package inside `real_target_history`; that one function
   will not run on its own.
5. `decision_logic.py` needs the platform's MySQL schema and services to run.
6. `figcheck_b.py` keeps the manuscript's banned-word list as an example; edit or empty it.
7. `paired_tests.py` needs the study's prediction files to run; those contain occupant data, never publish them.
8. The README says parts were written with Claude Code; edit it to taste.
