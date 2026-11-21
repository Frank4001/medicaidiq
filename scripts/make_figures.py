"""Generate report and slide figures from the archived eval results (reproducible).

python scripts/make_figures.py  ->  deliverables/figures/*.png
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

OUT = Path(__file__).resolve().parents[1] / "deliverables" / "figures"
OUT.mkdir(parents=True, exist_ok=True)

INK, MUTED, RED = "#1f2937", "#6b7280", "#A6192E"          # CMU red accent
BLUE, GREEN, AMBER, GREY = "#2563eb", "#059669", "#d97706", "#9ca3af"
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11, "axes.edgecolor": "#d1d5db",
                     "axes.labelcolor": INK, "xtick.color": INK, "ytick.color": INK})


def box(ax, x, y, w, h, title, sub="", color=BLUE, fill="#eff6ff", title_size=11.5, sub_size=8.8):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.12",
                                linewidth=1.6, edgecolor=color, facecolor=fill))
    ax.text(x + w / 2, y + h * (0.62 if sub else 0.5), title, ha="center", va="center", fontsize=title_size,
            fontweight="bold", color=INK)
    if sub:
        ax.text(x + w / 2, y + h * 0.3, sub, ha="center", va="center", fontsize=sub_size, color=MUTED)


def arrow(ax, x1, y1, x2, y2, label="", color=INK):
    ax.annotate("", xy=(x2, y2), xytext=(x1, y1), arrowprops=dict(arrowstyle="-|>", lw=1.5, color=color))
    if label:
        ax.text((x1 + x2) / 2, (y1 + y2) / 2 + 0.12, label, ha="center", fontsize=8.5, color=MUTED)


def architecture():
    fig, ax = plt.subplots(figsize=(13, 6.2))
    ax.set_xlim(0, 13)
    ax.set_ylim(0, 6.2)
    ax.axis("off")
    box(ax, 0.2, 4.6, 2.0, 1.1, "Question", "user / analyst", GREY, "#f9fafb")
    box(ax, 2.7, 4.6, 2.3, 1.1, "Input guardrails", "PII · write intent · injection", RED, "#fef2f2")
    box(ax, 5.5, 4.4, 2.8, 1.5, "ReAct agent", "Thought → tool → observation\nworking memory (E1, E2…)", BLUE)
    box(ax, 8.8, 4.6, 2.0, 1.1, "Verifier", "numbers grounded\ncitations · calibration", RED, "#fef2f2")
    box(ax, 11.1, 4.6, 1.75, 1.1, "Oversight", "release /\nwithhold + ticket", AMBER, "#fffbeb")
    arrow(ax, 2.2, 5.15, 2.7, 5.15)
    arrow(ax, 5.0, 5.15, 5.5, 5.15)
    arrow(ax, 8.3, 5.15, 8.8, 5.15)
    arrow(ax, 10.8, 5.15, 11.1, 5.15)
    ax.annotate("", xy=(6.9, 5.9), xytext=(9.8, 5.7),
                arrowprops=dict(arrowstyle="-|>", lw=1.2, color=RED, connectionstyle="arc3,rad=0.35"))
    ax.text(8.35, 6.05, "retry ≤ 2", fontsize=8.5, color=RED, ha="center")
    tools = [("get_policy_timeline", "long-term memory:\nsupersession · authority"),
             ("search_policy", "hybrid RAG: BM25 + bge\nRRF · MMR · adjacency"),
             ("check_data_coverage", "final / provisional /\nmissing quarters"),
             ("pre_post_analysis", "rate · bootstrap CI\nmateriality verdict"),
             ("spending_decomposition", "enrollment · use ·\nprice"),
             ("run_sql · calculate", "read-only, 4-layer\nguard · exact math")]
    for i, (t, s) in enumerate(tools):
        x = 0.25 + i * 2.12
        box(ax, x, 2.15, 1.95, 1.25, t, s, GREEN, "#ecfdf5", title_size=7.1, sub_size=7.9)
        ax.annotate("", xy=(x + 0.98, 3.4), xytext=(6.9, 4.4),
                    arrowprops=dict(arrowstyle="-", lw=0.8, color=GREY))
    box(ax, 0.25, 0.3, 4.1, 1.15, "Data", "synthetic SDUD-shaped DuckDB (read-only)\nfictional states NL · EV · SP", GREY,
        "#f9fafb")
    box(ax, 4.55, 0.3, 4.1, 1.15, "Policy corpus", "real eCFR regulations + data.medicaid.gov\n+ synthetic state documents",
        GREY, "#f9fafb")
    box(ax, 8.85, 0.3, 4.0, 1.15, "Ops", "monitor score · PII-redacted run log\nreview queue (SLA) · eval + release gates",
        AMBER, "#fffbeb")
    ax.text(6.5, 1.75, "Opt-in modes (benchmarked, off by default):  Tree-of-Thought planner  ·  four-agent workflow",
            ha="center", fontsize=9.5, color=MUTED, style="italic")
    fig.savefig(OUT / "architecture.png", dpi=200, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def bars(ax, labels, values, colors, title, fmt, ylim=None):
    b = ax.bar(labels, values, color=colors, width=0.6)
    ax.set_title(title, fontsize=12, fontweight="bold", color=INK, pad=10)
    ax.spines[["top", "right"]].set_visible(False)
    if ylim:
        ax.set_ylim(*ylim)
    for rect, v in zip(b, values):
        ax.text(rect.get_x() + rect.get_width() / 2, rect.get_height(), fmt(v), ha="center", va="bottom",
                fontsize=10.5, fontweight="bold", color=INK)


def modes():
    labels = ["Single agent\n(default)", "+ Tree-of-\nThought", "Four-agent\nworkflow"]
    acc = [100, 6 / 7 * 100, 17 / 18 * 100]
    secs = [9.8, 83.1, 49.4]
    cost = [0.0025, 0.0191, 0.0137]
    colors = [GREEN, AMBER, BLUE]
    fig, axs = plt.subplots(1, 3, figsize=(13, 4.2))
    bars(axs[0], labels, acc, colors, "Accuracy (% of cases passed)", lambda v: f"{v:.0f}%", (0, 115))
    bars(axs[1], labels, secs, colors, "Avg seconds per question", lambda v: f"{v:.0f} s")
    bars(axs[2], labels, cost, colors, "Cost per question (USD)", lambda v: f"${v:.4f}")
    fig.text(0.5, -0.04, "Single & four-agent: 18-case suite. ToT: 7 analysis cases (single agent 7/7 at 14.0 s on the "
             "same 7). DeepSeek deepseek-flash, live runs, Nov 2026.", ha="center", fontsize=9, color=MUTED)
    fig.tight_layout()
    fig.savefig(OUT / "benchmark_modes.png", dpi=200, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def rag_ablation():
    fig, ax = plt.subplots(figsize=(5.2, 3.8))
    bars(ax, ["Retrieval OFF", "Retrieval ON"], [1, 5], [GREY, GREEN], "Retrieval cases passed (of 5)",
         lambda v: f"{v:.0f}/5", (0, 6))
    fig.tight_layout()
    fig.savefig(OUT / "rag_ablation.png", dpi=200, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def evolution():
    weeks = ["W1\nframe", "W2\nReAct+tools", "W3\nRAG", "W4\nToT", "W5\nmulti-agent", "W6\nsafety+HITL"]
    cases = [0, 9, 14, 18, 18, 24]
    passed = [0, 9, 14, 18, 18, 24]
    tests = [0, 49, 76, 87, 97, 118]
    fig, ax = plt.subplots(figsize=(11, 3.8))
    ax.bar(weeks, cases, color="#e5e7eb", width=0.6, label="eval cases")
    ax.bar(weeks, passed, color=GREEN, width=0.35, label="passed (default config, live)")
    ax2 = ax.twinx()
    ax2.plot(weeks, tests, color=RED, marker="o", lw=2, label="offline tests")
    ax.set_ylabel("eval cases")
    ax2.set_ylabel("offline tests", color=RED)
    ax.spines[["top"]].set_visible(False)
    ax2.spines[["top"]].set_visible(False)
    for i, t in enumerate(tests):
        if t:
            ax2.text(i, t + 4, str(t), ha="center", fontsize=9, color=RED)
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, loc="upper left", frameon=False, fontsize=9)
    ax.set_title("System growth across the program", fontsize=12, fontweight="bold", color=INK)
    fig.tight_layout()
    fig.savefig(OUT / "evolution.png", dpi=200, bbox_inches="tight", facecolor="white")
    plt.close(fig)


if __name__ == "__main__":
    architecture()
    modes()
    rag_ablation()
    evolution()
    print("figures written to", OUT)
