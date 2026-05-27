#!/usr/bin/env python
# coding: utf-8
"""
n_neighbors Sensitivity Check for Single-Cell Clustering
---------------------------------------------------------
Tests Leiden clustering stability across a range of k values.
Outputs a summary table and figure.

Usage:
    1. Set the three config variables below.
    2. Run: python check_neighbors.py

Part of the Multiome Academy tutorial series.
https://multiomeacademy.com
"""

import numpy as np
import pandas as pd
import scanpy as sc
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from sklearn.metrics import adjusted_rand_score
from pathlib import Path

plt.rcParams["font.family"]  = "Arial"
plt.rcParams["pdf.fonttype"] = 42
plt.rcParams["ps.fonttype"]  = 42

# =============================================================================
# CONFIG — edit these for your dataset
# =============================================================================
H5AD_PATH   = "your_adata.h5ad"          # path to your AnnData .h5ad file
USE_REP     = "X_pca_harmony_50pc"        # obsm embedding to use for neighbors
REFERENCE_CLUSTERING = "leiden_ref"       # existing obs column to compare against
K_VALUES    = [10, 15, 20, 30, 50]        # k values to test
RESOLUTION  = 0.8                         # fixed Leiden resolution for comparison
OUT_DIR     = Path("results/neighbor_check")
# =============================================================================

OUT_DIR.mkdir(parents=True, exist_ok=True)

# ── Load ──────────────────────────────────────────────────────────────────────
adata = sc.read_h5ad(H5AD_PATH)
print(adata)

assert USE_REP in adata.obsm, \
    f"'{USE_REP}' not found in obsm. Available: {list(adata.obsm.keys())}"
assert REFERENCE_CLUSTERING in adata.obs.columns, \
    f"'{REFERENCE_CLUSTERING}' not found in obs. Available: {list(adata.obs.columns)}"

# ── Run neighbors + Leiden at each k ─────────────────────────────────────────
results = []

for k in K_VALUES:
    print(f"\n── k={k} ────────────────────────────────────────────────────")
    nkey = f"neighbors_k{k}"
    lkey = f"leiden_k{k}_res{RESOLUTION}"

    sc.pp.neighbors(
        adata,
        use_rep      = USE_REP,
        n_neighbors  = k,
        key_added    = nkey,
        random_state = 0,
    )
    sc.tl.leiden(
        adata,
        resolution    = RESOLUTION,
        neighbors_key = nkey,
        key_added     = lkey,
        random_state  = 42,
    )

    n_clusters = adata.obs[lkey].nunique()
    ari        = adjusted_rand_score(
        adata.obs[REFERENCE_CLUSTERING].astype(str),
        adata.obs[lkey].astype(str),
    )
    sizes = adata.obs[lkey].value_counts()

    results.append({
        "k"           : k,
        "n_clusters"  : n_clusters,
        "ari_vs_ref"  : round(ari, 4),
        "min_cluster" : int(sizes.min()),
        "max_cluster" : int(sizes.max()),
        "median_size" : int(sizes.median()),
    })
    print(f"  n_clusters={n_clusters}  ARI vs reference: {ari:.4f}  "
          f"min={sizes.min():,}  max={sizes.max():,}  median={int(sizes.median()):,}")

# ── Summary table ─────────────────────────────────────────────────────────────
df = pd.DataFrame(results)
print(f"\n{'─'*60}")
print(df.to_string(index=False))

out_csv = OUT_DIR / "neighbor_k_comparison.csv"
df.to_csv(out_csv, index=False)
print(f"\nSaved → {out_csv}")

# ── Plot ──────────────────────────────────────────────────────────────────────
fig, axes = plt.subplots(1, 2, figsize=(10, 4))

ref_k = K_VALUES[0]   # first k used as visual reference line on plots

axes[0].plot(df["k"], df["n_clusters"], marker="o", color="steelblue", linewidth=2)
axes[0].set_xlabel("n_neighbors (k)", fontsize=11)
axes[0].set_ylabel("Number of clusters", fontsize=11)
axes[0].set_title(f"Cluster count vs k  (res={RESOLUTION})", fontsize=11)
for spine in axes[0].spines.values():
    spine.set_visible(False)

axes[1].plot(df["k"], df["ari_vs_ref"], marker="o", color="crimson", linewidth=2)
axes[1].axhline(1.0, color="lightgray", linestyle=":", linewidth=1)
axes[1].set_xlabel("n_neighbors (k)", fontsize=11)
axes[1].set_ylabel(f"ARI vs {REFERENCE_CLUSTERING}", fontsize=11)
axes[1].set_title("Clustering stability vs reference", fontsize=11)
axes[1].set_ylim(0, 1.05)
for spine in axes[1].spines.values():
    spine.set_visible(False)

plt.suptitle("n_neighbors sensitivity check", fontsize=13, y=1.02)
plt.tight_layout()

fig.savefig(OUT_DIR / "neighbor_k_comparison.pdf", bbox_inches="tight", facecolor="white")
fig.savefig(OUT_DIR / "neighbor_k_comparison.png", dpi=150, bbox_inches="tight", facecolor="white")
plt.close()
print(f"Saved → neighbor_k_comparison.pdf / .png")
