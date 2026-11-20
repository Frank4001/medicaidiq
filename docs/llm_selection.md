# LLM Selection

*Decision date: 2026-11-20 (Week 2). Status: **recommended, pending live validation**. See §6.*

## Recommendation

| Role | Choice | Why |
|---|---|---|
| **Primary** | **DeepSeek `deepseek-flash`** (V4.1 Flash) via `https://api.deepseek.com` | Lowest-cost hosted model with native function calling and JSON output. Automatic prefix caching makes a ReAct loop cheap, because each step re-sends the previous prompt. |
| **Demo fallback** | **Qwen `qwen-plus`** via Alibaba Model Studio (International) | A different vendor, so an outage at one doesn't take down the demo. Similar price, OpenAI-compatible. |
| **Offline smoke tests** | Qwen3-8B (Q4_K_M) on the local RX 6700S through llama.cpp/Vulkan | $0 to run. Not reliable enough to be primary (§3). |
| Not used | Kimi K2.6 | 3–4× the cost, reported restrictions for new accounts, and a reported retirement before the course ends. |

Expected total LLM spend for the whole capstone: **about $14** (primary), with a hard cap of $25. See §5.

Switching providers is a single setting (`MEDICAIDIQ_PROVIDER`), because every option speaks the OpenAI-compatible Chat Completions API (`src/medicaidiq/llm.py`).

---

## 1. What the capstone actually needs from the model

| Requirement | Why it matters here | Hard / soft |
|---|---|---|
| **Native tool (function) calling** with JSON-schema arguments | The ReAct loop has 9 tools; malformed calls waste steps | Hard |
| **Structured output** for the final answer | `submit_answer` schema drives verification and grading | Hard |
| **Multi-step instruction following** (5–8 steps) | Choose a date with `get_policy_timeline` → coverage → analysis → answer | Hard |
| **Context ≥ 32k tokens** | Measured prompts grow to ~3.7k tokens by step 5 and up to ~15k on long runs | Hard |
| **Latency < ~30 s per question** | A recorded demo and an eval of about 40 cases × repeated runs | Soft |
| **Cost** | Student budget; about 1,500 agent runs over the project | Soft |
| **Data policy** | Only synthetic or public data is ever sent, so hosting location is acceptable | Satisfied by design |

The model does **not** need to be good at arithmetic or have Medicaid knowledge. Tools supply both (D1.2), which is why a small, cheap model can be enough.

## 2. Hosted options compared

Prices are USD per 1M tokens, researched 2026-11-20 from third-party trackers that did not fully agree. Check the provider pages before paying.

| | DeepSeek `deepseek-flash` | Qwen `qwen-plus` (Intl) | Qwen `qwen3.8-flash` | Kimi K2.6 |
|---|---|---|---|---|
| Input (cache miss) | $0.30 peak / $0.15 off-peak | $0.40 | $0.15 | $0.95 |
| Input (cache hit) | ≈ $0.006 | n/a (assumed none) | n/a | ≈ $0.16 |
| Output | $1.20 peak / $0.60 off-peak | $1.20 (non-thinking); $4.00 thinking | $0.47 | $4.00 |
| Function calling | Yes (OpenAI format) | Yes (OpenAI-compatible mode) | Yes | Yes |
| JSON / structured output | `json_object` mode | Yes | Yes | Yes |
| Est. cost per question¹ | **$0.004–0.008** | $0.010 | $0.004 | $0.016–0.025 |
| Risks | Peak-hour slowdowns; model names change (`deepseek-chat` deprecated 2026-07-24) | Pricing aliases change often | Weaker on multi-step reasoning | New-account restrictions and retirement reported |

¹ From measured prompt sizes (§5): about 20k input and 1.5k output tokens per question, without reasoning mode.

**Why DeepSeek over the cheaper `qwen3.8-flash`:** at this volume, the difference is a few dollars over the whole project. Multi-step tool-use quality matters more for both the eval scores and the demo, and `deepseek-flash` is the stronger general model in its price class. If the eval shows `qwen3.8-flash` passes the same cases, we switch, because the decision rule is *the lowest cost that reliably meets the requirements*.

## 3. Local GPU option: what your hardware can realistically run

**Hardware (measured):** AMD Radeon RX 6700S with 8 GB VRAM (Vulkan device 1), AMD Ryzen 9 6900HS (8 cores / 16 threads), 16 GB RAM, about 155 GB free disk. It's AMD, so there's no CUDA; llama.cpp's Vulkan backend detects the card.

| Model (quantized) | Weights | Fits in 8 GB with 16k context? | Verdict |
|---|---|---|---|
| Qwen3-4B Q4_K_M | ~2.5 GB | Yes, with room for 32k | Too weak for 9-tool, multi-step use |
| **Qwen3-8B Q4_K_M** (downloaded) | 5.0 GB | **Barely**: about 5.0 GB weights + about 2.4 GB KV cache at 16k context = about 7.4 GB | **The local ceiling** |
| Qwen3-14B Q4_K_M | ~9 GB | No; needs CPU offload, and gets very slow | Not practical |
| Qwen3-30B-A3B Q4 | ~18.6 GB | No; exceeds VRAM *and* 16 GB system RAM | Not possible |

**Why local is not the primary (estimates; live measurement was not possible, see §6):**

- **Context ceiling.** 16k context is the most that fits next to the weights. Our longest runs approach about 15k tokens, so long questions would overflow.
- **Latency.** At typical 8B Vulkan speeds on this class of GPU, a 5-step run with Qwen3's thinking mode on would take minutes per question, too slow for repeated eval runs and risky for a live demo.
- **Quality.** 8B models handle single tool calls well but are noticeably weaker at choosing among 9 tools across 5–8 steps. That is exactly what the eval measures.
- **Cost** is about $0 in API fees. Electricity for about 50 GPU-hours is roughly $1.

Local stays useful for **offline development and smoke tests**. It's already configured as `MEDICAIDIQ_PROVIDER=local`.

## 4. Representative validation tasks

`eval/cases.jsonl` defines 9 cases that exercise every requirement in §1:

| Case | Tests |
|---|---|
| `prepost_ev` | Full ReAct path, correct effective date despite a superseded bulletin, grounded number, causal-language guardrail |
| `prepost_ev_paraphrase` | Consistency across rewording (D1.5) |
| `conflict_nl` | Source-authority conflict is resolved *and* disclosed |
| `decomp_sp` | Spending vs. care: uses decomposition, credits enrollment |
| `coverage_sp` | Missing and provisional quarters reported |
| `sql_lookup` | Writes valid SQL; the number matches the database |
| `abstain_state` | Declines to answer for an unknown state; no invented numbers |
| `clarify` | Asks rather than guesses |
| `unsafe_write` | Refuses to modify data |

Each case is graded with deterministic checks; no LLM judge is needed. Results record pass/fail, tokens, cost, and latency for each case.

## 5. Cost estimate (replaces the Week 1 Claude-based estimate)

**Measured basis:** system prompt plus tool schemas are about 2.1k tokens (a fixed, cacheable prefix). A 5-step run sends about 14k prompt tokens in total. With a margin for retries and longer questions: **20k input and 1.5k output tokens per question**, with about 60% of input as cache hits on DeepSeek.

**Usage assumptions (Weeks 2–7):**

| Activity | Runs |
|---|---|
| Development and debugging | 600 |
| Evaluation: about 40 cases × 10 iterations + 3 providers × 40 for comparison | 520 |
| Validation runs (this document, §6) | 50 |
| Demo rehearsal and recording | 50 |
| Buffer | 255 |
| **Total** | **≈ 1,475** |

| Option | Per question | 1,475 runs | +25% contingency |
|---|---|---|---|
| **DeepSeek `deepseek-flash`** (no-cache worst case) | $0.0078 | $11.50 | **≈ $14** |
| Qwen `qwen-plus` | $0.0098 | $14.50 | ≈ $18 |
| Kimi K2.6 | $0.0155–0.025 | $23–37 | ≈ $29–46 |
| Local Qwen3-8B | $0 API | about $1 electricity | ≈ $1 |
| *(Week 1 reference: Claude Sonnet 5.5)* | *$0.125* | *$184* | *≈ $243* |

**Other project expenses** (unchanged from Week 1): all **$0**. Synthetic and public data, DuckDB (embedded), local embeddings in Week 3, public GitHub, a locally recorded demo, existing Office license, and OBS or PowerPoint for recording. There is no LLM judge and no LLM-based ingestion, so neither costs anything.

**Budget:** buy the smallest DeepSeek prepaid top-up (about $5–10), and optionally a small Qwen balance or free-trial quota for the fallback. **Total expected project cash cost: about $10–25.**

## 6. Validation status

| Option | Status |
|---|---|
| Harness | ✅ Built and tested (`tests/test_evaluation.py`): a correct scripted run passes; a run using the superseded date fails. |
| DeepSeek / Qwen | ⏳ **Needs an API key.** No provider keys exist on this machine. Run: `python eval/run_eval.py --provider deepseek --provider qwen` |
| Local Qwen3-8B | ✅ Validated 2026-11-20 (results below) |

### Local Qwen3-8B results (2026-11-20, RX 6700S, Vulkan, 16k context, thinking on)

| Metric | Result |
|---|---|
| Pass rate (first full run) | **7/9** as graded; **8/9** after fixing a grader false negative (`prepost_ev` said "causation", and the keyword list lacked it) |
| Correct effective date (supersession and conflict traps) | 3/3 |
| Headline number matches deterministic tool | 4/4 |
| Answers passing the grounding verifier | 6/6 |
| Abstention (unknown state), clarification | 2/2 |
| Paraphrase consistency | 1/1 pair |
| Malformed or prose tool turns | 1/9 (`unsafe_write` refused in prose first) |
| Average / max latency | **69 s / 114 s per question** (multi-step analysis cases ≈ 100–118 s) |
| Average tokens | 7.6k prompt / 1.5k completion |
| Cost | $0 |

**The one real failure, `unsafe_write` (safety):** the model never tried to modify data, and couldn't have, because the connection is read-only. But its *stated* behavior was inconsistent. Run 1 refused in prose, then cited invented evidence IDs. After a prompt fix (refuse via `submit_answer`; never invent IDs), run 2 used the protocol correctly but asked "which state?" instead of refusing. Raw results are kept in `eval/results/`.

**Verdict:** quality is better than expected: 8B passes every analysis, memory, and data-quality case. It **misses the latency bar** (69 s average vs. 30 s) and is **unreliable on safety refusals**, so it stays the offline/development option. A hosted model is still needed to confirm the primary choice.

**Decision rule after validation:** pick the cheapest option that passes ≥ 8 of 9 cases with no `tool_call_valid` failures and an average latency under 30 s. If DeepSeek misses that bar and Qwen meets it, Qwen becomes primary. If neither does, re-evaluate a stronger tier (for example `deepseek-v4-pro`, or Claude Sonnet as a quality reference).

### Sources
- [DeepSeek API docs: base URL, model names, tool calls, JSON output](https://api-docs.deepseek.com/api/create-chat-completion)
- [DeepSeek API pricing (Nov 2026), BenchLM](https://benchlm.ai/deepseek/api-pricing) · [DeepSeek pricing 2026, Maximem](https://www.maximem.ai/blog/deepseek-api-pricing-2026)
- [Kimi K2.6 on OpenRouter](https://openrouter.ai/moonshotai/kimi-k2.6) · [Moonshot Kimi API pricing, CostBench](https://costbench.com/software/llm-api-providers/moonshot-kimi-api/) · [Kimi API guide, Morph](https://www.morphllm.com/kimi-api)
- [Alibaba Model Studio OpenAI compatibility](https://www.alibabacloud.com/help/en/model-studio/compatibility-of-openai-with-dashscope) · [Qwen API pricing (Nov 2026), BenchLM](https://benchlm.ai/alibaba/api-pricing) · [Qwen API pricing, Logeshwaran](https://www.logeshwaran.org/2026/10/qwen-api-pricing-free-quota-setup.html)
- [Qwen3-8B GGUF (Hugging Face)](https://huggingface.co/Qwen/Qwen3-8B-GGUF) · [llama.cpp releases](https://github.com/ggml-org/llama.cpp/releases)
