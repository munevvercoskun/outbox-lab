"""Two charts from results/.

Chart 1 counts wrong outcomes per scenario -- an email a customer should have
received and didn't, plus every extra email they shouldn't have. Both are
failures, so both are counted; the chart is "how many customers were affected",
not "how many bugs fired".

Chart 2 is the relay comparison, on a log axis because the two numbers are two
orders of magnitude apart.

Palette is the validated categorical pair; separation holds under deuteranopia
and tritanopia. Two series, so there is a legend, and the bars are directly
labelled so identity never depends on colour alone.
"""
import csv
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(os.path.dirname(HERE), "results")

BLUE, ORANGE = "#2a78d6", "#eb6834"
INK, MUTED, GRID = "#1a1a19", "#6b6b68", "#e4e4e1"

plt.rcParams.update({
    "figure.dpi": 140, "savefig.dpi": 140, "font.size": 10,
    "axes.edgecolor": GRID, "axes.labelcolor": MUTED, "text.color": INK,
    "xtick.color": MUTED, "ytick.color": MUTED,
    "axes.spines.top": False, "axes.spines.right": False,
})

PRETTY = {
    "none": "no failures",
    "crash_after_commit": "service dies\nafter commit",
    "crash_before_ack": "worker dies\nbefore ack",
    "relay_crash": "relay dies\nbefore marking",
    "duplicate_delivery": "broker delivers\ntwice",
    "everything": "all of them\nat once",
}


def read(name):
    path = os.path.join(RES, name)
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def chart_scenarios():
    rows = read("chaos.csv")
    if not rows:
        return
    order, by = [], {}
    for r in rows:
        if r["scenario"] not in by:
            by[r["scenario"]] = {}
            order.append(r["scenario"])
        by[r["scenario"]][r["mode"]] = int(r["missing"]) + int(r["duplicated"])

    x = range(len(order))
    naive = [by[s].get("naive", 0) for s in order]
    guarded = [by[s].get("guarded", 0) for s in order]
    w = 0.38

    fig, ax = plt.subplots(figsize=(9.2, 4.6))
    ax.bar([i - w / 2 for i in x], naive, w, color=ORANGE,
           label="naive", zorder=3)
    ax.bar([i + w / 2 for i in x], guarded, w, color=BLUE,
           label="outbox + idempotent consumer", zorder=3)

    for i, (n, g) in enumerate(zip(naive, guarded)):
        if n:
            ax.text(i - w / 2, n, f" {n}", ha="center", va="bottom",
                    fontsize=9, color=MUTED)
        ax.text(i + w / 2, g, f" {g}", ha="center", va="bottom",
                fontsize=9, color=MUTED)

    ax.set_xticks(list(x))
    ax.set_xticklabels([PRETTY.get(s, s) for s in order], fontsize=8.5)
    ax.set_ylabel("customers affected\n(missing + duplicate emails)")
    ax.grid(axis="y", color=GRID, zorder=0)
    ax.set_axisbelow(True)
    ax.legend(frameon=False, fontsize=9)
    n = int(rows[0]["orders"])
    ax.set_title(f"same failures, same {n} orders", loc="left",
                 fontsize=9, color=MUTED, pad=10)
    fig.suptitle("What each failure costs", x=0.0, ha="left",
                 fontsize=13, fontweight="600", color=INK)

    fig.tight_layout()
    fig.savefig(os.path.join(RES, "1_scenarios.png"), bbox_inches="tight")
    plt.close(fig)
    print("wrote results/1_scenarios.png")


def chart_relay():
    rows = read("relay.csv")
    if not rows:
        return
    labels = [r["relay"] for r in rows]
    pub = [int(r["published"]) for r in rows]
    ok = [r["ok"] == "True" for r in rows]

    fig, ax = plt.subplots(figsize=(8.0, 2.9))
    colors = [BLUE if o else ORANGE for o in ok]
    ax.barh(range(len(labels)), pub, height=0.5, color=colors, zorder=3)
    ax.set_yticks(range(len(labels)))
    ax.set_yticklabels(labels)
    ax.invert_yaxis()
    ax.set_xscale("log")
    ax.set_xlabel("events published for 200 orders  (log scale)")
    ax.grid(axis="x", color=GRID, zorder=0)
    ax.set_axisbelow(True)

    for i, (p, o) in enumerate(zip(pub, ok)):
        ax.text(p * 1.1, i, f"{p:,}" + ("" if o else "  — and still broken"),
                va="center", fontsize=9, color=MUTED)
    ax.set_xlim(right=max(pub) * 6)

    ax.set_title("a crash rolled back the whole batch, so it never advanced",
                 loc="left", fontsize=9, color=MUTED, pad=10)
    fig.suptitle("Why the relay commits one row at a time", x=0.0, ha="left",
                 fontsize=13, fontweight="600", color=INK)

    fig.tight_layout()
    fig.savefig(os.path.join(RES, "2_relay.png"), bbox_inches="tight")
    plt.close(fig)
    print("wrote results/2_relay.png")


if __name__ == "__main__":
    os.makedirs(RES, exist_ok=True)
    chart_scenarios()
    chart_relay()
