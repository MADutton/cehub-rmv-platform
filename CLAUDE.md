# CEHub RMV Platform — Claude Code Context

## What this is

FastAPI backend + vanilla HTML/CSS/JS frontend for the CEHub RMV (Remote Mastery Verification) assessment platform. Runs AI-driven structured oral exams for veterinary CE fellowships. Deployed as a single iframe embedded in Thinkific LMS lessons.

## Stack

- **Backend:** Python FastAPI, async SQLAlchemy, SQLite (dev) / Postgres (prod)
- **Frontend:** Vanilla HTML/CSS/JS (`app/static/`), single-page view switching
- **AI:** OpenAI for dynamic prompt generation and follow-up decisions; exam questions can also be pre-scripted via `prompts.json`
- **No framework:** React/Vue/Next.js not used

## Repo layout

```
app/
  main.py                  # All API routes (sessions, scoring, submissions)
  examiner.py              # Follow-up decision logic for all product types
  scorer.py                # Scoring pipeline (calls OpenAI with rubric + turns)
  models.py                # SQLAlchemy models + Pydantic schemas
  config.py                # Settings (API keys, data dirs)
  database.py              # AsyncSession setup
  loaders/
    assigned_case.py       # Loads data/assigned-case-rmv/
    case_based.py          # Loads data/case-based-rmv/
    mastery_module.py      # Loads data/mastery-module-rmv/
  generators/
    prompt_generator.py    # Dynamic prompt generation via OpenAI
  static/
    index.html             # Single-page app entry point
    app.js / app.css
data/
  assigned-case-rmv/
    cases/                 # One dir per case; each has case_detail.json, prompts.json, scoring_anchors.json
    product/
    prompts/
    templates/
  mastery-module-rmv/
    modules/               # One dir per module; active modules appear in GET /modules
    product/
    prompts/
    templates/
```

## Three product types

| Product | Prompts source | Follow-ups |
|---|---|---|
| `assigned_case` | Pre-scripted `prompts.json` | Trigger-based (pre-scripted) |
| `case_based` | AI-generated from submitted case text | AI-generated |
| `mastery_module` | Pre-scripted `prompts.json` if present, else AI-generated from transcript | Pre-scripted if available, else AI-generated |

## Adding a new mastery module

Create a directory under `data/mastery-module-rmv/modules/<module_id>/` with:

```
module_record.json       # Required: module_id, module_title, status ("active" to appear in picker)
module_objectives.json   # Required for dynamic generation; optional for pre-scripted
scoring_anchors.json     # Required: anchors.{domain}.high_value_elements / major_concerns
prompts.json             # Optional: if present, used instead of AI generation
transcript.txt           # Required for dynamic generation; not needed if prompts.json present
```

### prompts.json format (pre-scripted modules)

```json
[
  {
    "prompt_id": "p1",
    "phase_id": "concept_check",
    "prompt_type": "primary",
    "text": "...",
    "followups": [
      { "followup_id": "p1_f1", "trigger": "vague_generic_answer", "text": "..." }
    ]
  }
]
```

### scoring_anchors.json format

```json
{
  "module_id": "my_module",
  "anchors": {
    "core_concept_understanding": {
      "high_value_elements": ["..."],
      "major_concerns": ["..."]
    }
  }
}
```

Six required domains: `core_concept_understanding`, `clinical_application`, `prioritization_decision_making`, `justification`, `boundaries_uncertainty`, `mastery_depth`.

## Key API endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/modules` | List active modules |
| GET | `/cases` | List active assigned cases |
| POST | `/sessions` | Start a session (requires product_type + module_id or case_id) |
| POST | `/sessions/{id}/respond` | Submit a response, get next prompt |
| GET | `/sessions/{id}/result` | Poll for scoring result |

## Scoring

- 6 domains, 0–5 scale each, max 30 per session
- Outcomes: mastered (24–30), borderline_review (20–23), not_yet_mastered (0–19)
- Scoring runs as a background task after session completes; poll `/result` every 3s

## CAPM Final Exam modules (current)

Five pre-scripted exam sections are active:

| Module ID | Section | Weight |
|---|---|---|
| `capm_final_01_techniques` | Techniques | 25.27% |
| `capm_final_02_neuro` | Neuro | 14.40% |
| `capm_final_03_pharmacology` | Pharmacology | 18.48% |
| `capm_final_04_non_pharm` | Non-pharm | 14.95% |
| `capm_final_05_recognition_assessment` | Recognition/Assessment | 26.90% |

Exam content (prompts, anchors) lives in `MADutton/RMV_Assessment`.

## Development conventions

- Feature branches: `claude/[feature-name]`
- Do not push directly to main without PR
- `lru_cache` is used on all data loaders — restart the server after changing data files in dev
- Keep push_files calls to 2–3 files max to avoid stream timeouts

## Related repos

- `MADutton/RMV_Assessment` — exam content (prompts, anchors, rubrics)
- `MADutton/CEHub-Mastery-Hub` — source transcripts for fellowship modules
- `MADutton/assigned_case_rmv` — assigned case content
