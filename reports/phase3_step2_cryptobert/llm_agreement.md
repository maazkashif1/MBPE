Labels: data/labeling/llm_labels.csv (198 benchmark headlines; judged by: LLM (Claude Opus 5.5) via labeling/GUIDE.md -- NOT human)

| scorer | n | accuracy | Cohen's kappa | macro-F1 | Spearman(score, label) |
|---|---|---|---|---|---|
| finbert | 198 | 59.6% | 0.352 | 0.558 | 0.368 |
| cryptobert | 198 | 38.9% | -0.012 | 0.321 | 0.237 |

Confusion matrix, finbert (rows: labels; columns: scorer argmax):

| label \ scorer | negative | neutral | positive |
|---|---|---|---|
| negative | 36 | 21 | 10 |
| neutral | 13 | 66 | 13 |
| positive | 7 | 16 | 16 |

Confusion matrix, cryptobert (rows: labels; columns: scorer argmax):

| label \ scorer | negative | neutral | positive |
|---|---|---|---|
| negative | 9 | 49 | 9 |
| neutral | 13 | 58 | 21 |
| positive | 1 | 28 | 10 |

