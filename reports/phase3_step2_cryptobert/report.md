| label set | scenario | model | scorer | trades | mean net R [95% CI] | net bps | gross bps | random: net R (median) | vol filter: net R (median) | folds won vs random | vs vol filter |
|---|---|---|---|---|---|---|---|---|---|---|---|
| A | taker | lgbm r3 | finbert | 3,946 | -0.127 [-0.158, -0.096] | -9.2 | +4.7 | -0.225 | -0.160 | 16/18 | 12/18 |
| A | taker | lgbm r3 | cryptobert | 4,916 | -0.151 [-0.180, -0.121] | -10.6 | +3.3 | -0.225 | -0.179 | 16/18 | 11/18 |
| A | maker_entry_tp | lgbm r3 | finbert | 3,946 | -0.055 [-0.086, -0.024] | -3.5 | +4.7 | -0.137 | -0.102 | 16/18 | 15/18 |
| A | maker_entry_tp | lgbm r3 | cryptobert | 5,021 | -0.077 [-0.107, -0.047] | -5.2 | +3.1 | -0.134 | -0.116 | 16/18 | 12/18 |
| B | taker | lgbm r3 | finbert | 3,793 | -0.077 [-0.097, -0.056] | -11.3 | +2.6 | -0.092 | -0.078 | 11/18 | 11/18 |
| B | taker | lgbm r3 | cryptobert | 4,184 | -0.093 [-0.113, -0.073] | -14.1 | -0.1 | -0.099 | -0.086 | 9/18 | 9/18 |
| B | maker_entry_tp | lgbm r3 | finbert | 4,220 | -0.051 [-0.071, -0.032] | -7.1 | +1.8 | -0.060 | -0.057 | 11/18 | 12/18 |
| B | maker_entry_tp | lgbm r3 | cryptobert | 4,211 | -0.060 [-0.080, -0.040] | -9.0 | -0.2 | -0.063 | -0.058 | 10/18 | 9/18 |
| A | taker | lgbm_tuned r3 | finbert | 2,760 | -0.122 [-0.160, -0.085] | -8.8 | +5.1 | -0.211 | -0.137 | 15/18 | 10/18 |
| A | taker | lgbm_tuned r3 | cryptobert | 2,698 | -0.140 [-0.178, -0.099] | -11.1 | +2.8 | -0.233 | -0.145 | 14/18 | 10/18 |
