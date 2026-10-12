# Selected research code

Code samples from my PhD research at Virginia Tech on indoor air quality forecasting and building digital twins.
Each file can be read on its own. No data is included.

## Indoor CO2 forecasting with LLMs and time-series foundation models (`co2-forecasting/`)

- `llm_serialize.py` turns a multichannel sensor window into an LLM prompt and parses the model's text answer back into a
  forecast in ppm. It uses LLMTime-style scaling fit on the lookback only, fixed-point digits and row-stacked multivariate
  tables, and its parser never raises. Run `python llm_serialize.py` for the built-in self-test (needs only numpy).
- `target_substitution.py` holds the stand-ins for CO2 when a model must forecast from the other sensors alone (constant,
  room-by-hour climatology, zeros) and the sensor-failure corruptions used in robustness tests (random masks, contiguous
  blocks, a stuck sensor).
- `metrics.py` is a missing-value-aware metric panel: error, skill and bias metrics, per-horizon, per-room and per-hour
  breakdowns, CO2 threshold detection and bootstrap intervals.

The forecasting work uses the public MIT JCI IAQ-HVAC dataset (https://github.com/acoh64/MIT-JCI-IAQ-HVAC).

## Statistics (`stats/`)

- `paired_tests.py` compares forecasting models on the same test windows, written from scratch: a moving-block bootstrap,
  the Diebold-Mariano test with a Newey-West variance, exact McNemar, DeLong's AUROC test and Holm correction
  (numpy, pandas, scipy).

## Publication figures (`figure-tools/`)

- `style_b.py` gives every figure of a paper one visual identity: a colour-blind-checked palette, line styles and markers
  that survive grayscale printing, embedded fonts and reproducible PDFs.
- `figcheck_b.py` runs automated checks on every render: text overlapping text or lines, clipping, the smallest font and
  line width, banned words and grayscale levels.

## HPC (`hpc/`)

- `compute_energy.py` estimates the energy and carbon cost of GPU jobs from Slurm accounting (GPU-hours x TDP x PUE x grid
  intensity) and splits date ranges the accounting database refuses. Standard library only.

## Building digital twin (`digital-twin/`)

- `decision_logic.py` is the decision layer of a deployed human-building-interaction platform: a veto, a seven-signal
  confidence score, tiered routing to an LLM that retrieves similar past decisions or to a rule-based fallback, and action
  inhibition. It reads its configuration from environment variables (see `.env.example`) and needs the platform's MySQL
  database, which is not included.

## Web (`f1-tracker/`)

- A React + Vite app for Formula 1 standings, calendar, results and live timing from two free public APIs, with request
  limiting, retries and caching. Run `npm install` then `npm run dev`.

Parts of this code were written with an AI coding assistant (Claude Code) under my direction.
