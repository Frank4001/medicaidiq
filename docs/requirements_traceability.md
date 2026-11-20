# Final Requirements Traceability

Every graded requirement of *Required Capstone Assignment 7.1*, where it will be met, and its current status. Updated at the end of each week. **Last update: Week 2.**

Status: ⬜ not started · 🟨 in progress · ✅ done (may still be polished)

## A. Final report (filled into the Word template from Activity 7.1)

| # | Template section | Source material | Status |
|---|---|---|---|
| A1 | Project title | `README.md`: *MedicaidIQ* | ✅ |
| A2 | Problem, intended user, why it matters | `docs/checkpoints/week1_*.md` §1, README | ✅ |
| A3 | Goal and scope (success criteria, boundaries) | Week 1 §1; D2.1 scope; eval pass criteria in `docs/llm_selection.md` §6 | 🟨 add a formal success-criteria list |
| A4 | Final architecture (reasoning, memory, tools, retrieval, ToT, multi-agent, guardrails, logging, eval, human intervention) | `docs/architecture.md` | 🟨 reasoning, memory, tools, guardrails, logging, eval, clarification done; retrieval (hybrid), ToT/planning, coordination, human-review workflow pending |
| A5 | Design evolution M1→M6 | `docs/design_log.md` + `docs/checkpoints/` | 🟨 Weeks 1–2 |
| A6 | Implementation overview | README "Repository layout"; `pyproject.toml` | 🟨 |
| A7 | Evaluation methods and results | `eval/`, `src/medicaidiq/evaluation.py` | 🟨 harness + 9 cases built; **live results pending an API key** |
| A8 | Safety, reliability, human oversight | SQL guard, verifier, stop conditions, clarification, knowledge-store human approval | 🟨 human-review workflow and monitoring pending |
| A9 | Limitations and next steps | checkpoint docs §5 | 🟨 running list |
| A10 | Public GitHub link | https://github.com/Frank4001/medicaidiq | ✅ |

## B. Public GitHub repository

| # | Requirement | Where | Status |
|---|---|---|---|
| B1 | README: project, problem, architecture, setup, usage | `README.md` | ✅ (update each week) |
| B2 | Main code | `src/medicaidiq/` | ✅ working agent |
| B3 | Sample inputs, outputs, eval artifacts | `data/synthetic/`, `eval/cases.jsonl` | 🟨 sample outputs + eval results pending a live run |
| B4 | Clear run/review instructions | README Quick start (no key needed for tests) | ✅ |
| B5 | Public/synthetic data only; no secrets | `.gitignore`, `.env.example`, `data/README.md` | ✅ |

## C. Presentation (PowerPoint, recorded 8–10 minutes)

| # | Outline section | Material ready |
|---|---|---|
| C1 | Opening: problem, user, goal | README problem table |
| C2 | Why it matters / why an agent | "prompt-only failure → capability" table (Week 2 §4) |
| C3 | Architecture walkthrough | `docs/architecture.md` diagram + step-by-step trace |
| C4 | Key design decisions | `docs/design_log.md` (D1.2, D1.13, D2.3, D2.4, D2.8 are the strongest) |
| C5 | Evaluation evidence | ⬜ pending live results |
| C6 | Repo tour | README layout |
| C7 | Closing reflection | ⬜ |
| C8 | *Optional:* 90-second pitch | ⬜ |

## D. Quality checks from the planning activity

- [x] Pieces fit together as one system (single agent loop; every component traced to a limitation)
- [ ] Presentation focused on the key ideas (Week 7)
- [x] Repository public, organized, understandable (https://github.com/Frank4001/medicaidiq)
- [ ] Results support the claims (needs live eval results)
- [x] Strengths and limitations stated (checkpoint docs, LLM selection)
