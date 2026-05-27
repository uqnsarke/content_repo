# n_neighbors Sensitivity Check for Single-Cell Clustering

A lightweight script to empirically justify your `n_neighbors` choice before committing to a Leiden clustering in Scanpy.

## The problem

Most scRNA-seq tutorials set `n_neighbors=15` and move on. But this default comes from the original UMAP paper — it was never validated for your dataset. For large datasets (>500k cells), the wrong `k` can fragment real populations or merge distinct ones.

## What this script does

Runs `sc.pp.neighbors` + `sc.tl.leiden` across a range of `k` values at a fixed resolution, then computes:

- **Number of clusters** at each k — checks whether topology has stabilised
- **Adjusted Rand Index (ARI)** vs your reference clustering — measures stability
- **Cluster size statistics** — flags suspiciously small or large clusters

## Output

```
results/neighbor_check/
├── neighbor_k_comparison.csv   # summary table
├── neighbor_k_comparison.pdf   # publication-quality figure
└── neighbor_k_comparison.png   # preview
```

Example output on a 1.5M cell dataset:

| k  | n_clusters | ARI vs ref | min_cluster |
|----|-----------|------------|-------------|
| 10 | 24        | 0.5987     | 2,828       |
| 15 | 24        | 0.9499     | 5,222       |
| 20 | 25        | 0.8105     | 6,808       |
| 30 | 25        | 0.7120     | 5,357       |
| 50 | 25        | 0.6572     | 5,033       |

**Interpretation:** k=10 is unstable (ARI=0.60). Cluster count stabilises at k=15. k=15 sits at the inflection point of highest ARI and stable topology — well-justified for this dataset.

## Usage

```python
# Edit these three lines for your dataset
H5AD_PATH            = "your_adata.h5ad"
USE_REP              = "X_pca_harmony_50pc"   # or X_scVI, X_pca, etc.
REFERENCE_CLUSTERING = "leiden_ref"            # existing clustering in adata.obs
K_VALUES             = [10, 15, 20, 30, 50]
RESOLUTION           = 0.8
```

Then run:

```bash
python check_neighbors.py
```

**Requirements:** `scanpy`, `scikit-learn`, `matplotlib`, `pandas`, `numpy`

Your AnnData object must have:
- An embedding in `obsm` (e.g. `X_pca_harmony_50pc`, `X_scVI`, `X_pca`)
- An existing Leiden clustering column in `adata.obs` to use as reference

## Part of the Multiome Academy tutorial series

→ [multiomeacademy.com](https://multiomeacademy.com)

Full tutorials on single-cell and multiome bioinformatics, including step-by-step guides on integration, clustering, and differential expression.

## License

MIT
