# Dedup review (threshold 0.85, window 24 h, clusters capped at 24 h from their first member)

- Rows: 1,886,217; clusters: 979,912 (1.92 per cluster)
- Largest cluster: 644 rows; longest span (first to last member): 24.00 h; clusters spanning > 12 h: 44,286

| cluster size | clusters |
|---|---|
| 1 | 759,867 |
| 2 | 121,506 |
| 3-5 | 66,342 |
| 6-10 | 16,969 |
| 11-50 | 12,149 |
| >50 | 3,079 |

## 20 random clusters with a join at similarity 0.85–0.88
First line = the cluster's earliest headline; indented lines joined it (similarity, batch time).

- **Producer Price Index leaps to 6.7% in April, signalling mounting inflation pressures** (20210527191500)
  - 0.862 · 20210527201500 · Producer Price Index leaps to 6.7% in April, signalling...
- **Fed's Powell expects Inflation to stay hot for months** (20210714134500)
  - 0.858 · 20210714150000 · Powell: US inflation likely to run hot in coming months
- **China proposes adding cryptocurrency mining to 'negative list' of industries** (20211008153000)
  - 1.000 · 20211008160000 · China proposes adding cryptocurrency mining to 'negative list' of industries
  - 1.000 · 20211008173000 · China proposes adding cryptocurrency mining to 'negative list' of industries
  - 1.000 · 20211009044500 · China proposes adding cryptocurrency mining to 'negative list' of industries
  - 0.889 · 20211009060000 · China proposes adding cryptocurrency mining to 'negativ
  - 1.000 · 20211009060000 · China proposes adding cryptocurrency mining to 'negative list' of industries
  - 1.000 · 20211009060000 · China proposes adding cryptocurrency mining to 'negative list' of industries
- **Citi says US inflation is transitory, will peak in February** (20211109050000)
  - 0.874 · 20211109071500 · US inflation is transitory, to peak in February
- **Oct retail inflation inches-up on high food, input prices** (20211113164500)
  - 0.875 · 20211113221500 · India's Oct retail inflation inches-up on high food, input prices
  - 0.957 · 20211114111500 · India's October retail inflation inches up on high food, input prices
- **Inflation Worries Endanger Biden's Build Back Better Agenda** (20211116003000)
  - 1.000 · 20211116021500 · Inflation Worries Endanger Biden's Build Back Better Agenda
  - 1.000 · 20211116023000 · Inflation Worries Endanger Biden's Build Back Better Agenda
  - 1.000 · 20211116024500 · Inflation Worries Endanger Biden's Build Back Better Agenda
  - 1.000 · 20211116041500 · Inflation Worries Endanger Biden's Build Back Better Agenda
  - 1.000 · 20211116120000 · Inflation Worries Endanger Biden's Build Back Better Agenda
  - 0.860 · 20211116210000 · Can Biden's Agenda Survive Inflation?
- **Russia-Ukraine war: Putin to take crypto route to evade West's economic sanctions** (20220225183000)
  - 0.874 · 20220225223000 · Russia-Ukraine crisis: Crypto eyed as sanctions pressure mounts on Putin
  - 1.000 · 20220225234500 · Russia-Ukraine crisis: Crypto eyed as sanctions pressure mounts on Putin
- **Bank of England set to raise interest rates TODAY amid inflation fears** (20220317093000)
  - 0.859 · 20220317101500 · Bank of England likely to raise key interest rate
  - 1.000 · 20220317101500 · Bank of England likely to raise key interest rate
  - 1.000 · 20220317101500 · Bank of England likely to raise key interest rate
  - 1.000 · 20220317101500 · Bank of England likely to raise key interest rate
  - 0.852 · 20220317101500 · Bank of England set to lift UK interest rates to pre-pandemic level to fight inflation
  - 1.000 · 20220317103000 · Bank of England likely to raise key interest rate
- **Bank of England raises UK interest rates to 0.75% and condemns Russia's invasion of Ukraine** (20220317124500)
  - 1.000 · 20220317124500 · Bank of England raises UK interest rates to 0.75% and condemns Russia's invasion of Ukraine
  - 1.000 · 20220317124500 · Bank of England raises UK interest rates to 0.75% and condemns Russia's invasion of Ukraine
  - 1.000 · 20220317130000 · Bank of England raises UK interest rates to 0.75% and condemns Russia's invasion of Ukraine
  - 1.000 · 20220317130000 · Bank of England raises UK interest rates to 0.75% and condemns Russia's invasion of Ukraine
  - 1.000 · 20220317134500 · Bank of England raises UK interest rates to 0.75% and condemns Russia's invasion of Ukraine
  - 0.861 · 20220317150000 · Bank of England warns inflation could breach 8%, raises rates, condemns Russia's invasion of Ukraine
- **Inflation Is Manageable. Fastenal Earnings Hold Good News for Stocks.** (20220413121500)
  - 0.872 · 20220413130000 · Fastenal Earnings Show Inflation Is Manageable
  - 1.000 · 20220413164500 · Fastenal Earnings Show Inflation Is Manageable
- **Bitcoin barrels towards historic January as crypto mark...** (20230131051500)
  - 0.871 · 20230131054500 · Bitcoin barrels toward historic January as crypto market jumps by $280 billion
- **Wage increase not solution to rising inflation** (20230627183000)
  - 0.878 · 20230628044500 · Wage increase not solution to rising inflation, says Shaibu
- **Inflation plummets to 6.8% as price rises continue to slow in fresh CPI figures** (20230816063000)
  - 0.856 · 20230817000000 · Inflation falls to 6.8% but prices remain high
- **Bank of England pauses interest rates at 5.25% - what it means for your money** (20230921114500)
  - 0.902 · 20230921133000 · Bank of England pauses; interest rates unchanged at 5.25%
  - 0.853 · 20230921143000 · Bank of England pauses tightening cycle to keep main interest rate at 5.25%
  - 1.000 · 20230921144500 · Bank of England pauses tightening cycle to keep main interest rate at 5.25%
  - 0.987 · 20230921151500 · What it means for your money as Bank of England pauses interest rates at 5.25%
- **Trump Musk Interview: Inflation, Economy, Etc. (Live Coverage)** (20240813030000)
  - 1.000 · 20240813030000 · Trump Musk Interview: Inflation, Economy, Etc. (Live Coverage)
  - 0.879 · 20240813033000 · Trump Musk Interview Rambles Through Inflation, Taxes, Trade, Education And Nuclear Power (Live Coverage)
- **AUD/USD holds below 0.6800 amid risk-off sentiment, eyes on Australian CPI data** (20240828043000)
  - 0.861 · 20240828043000 · AUD/USD off seven-month highs, near 0.6800 amid hot Australian CPI data
- **New details of Trump family crypto project released, including who can buy in** (20240917033000)
  - 1.000 · 20240917034500 · New details of Trump family crypto project released, including who can buy in
  - 0.852 · 20240917043000 · Trump Crypto Project Details: When, How, And Where To Buy
  - 1.000 · 20240917044500 · New details of Trump family crypto project released, including who can buy in
  - 0.972 · 20240917161500 · Donald Trump's Crypto Project Details: When, How, And Where To Buy
  - 0.851 · 20240917184500 · New Details Emerge from Trump Family Crypto Launch
- **Trump-Backed Memecoin Surges Past $12B Amid Renewed Crypto Optimism** (20250119173000)
  - 0.859 · 20250120004500 · Trump-backed memecoin value tops US$13 billion as crypto industry hopes rise
  - 0.946 · 20250120014500 · Trump-Backed Memecoin Surges to $12 Billion Valuation Amid Crypto Optimism
  - 0.867 · 20250120150000 · Donald Trump's crypto holdings, including $TRUMP memecoin, worth $58 billion: Report
- **US Inflation Hits 3.8% as Iran War Drives Gas Prices Higher** (20260513201500)
  - 0.851 · 20260513220000 · The Iran war is hitting home as gasoline prices fuel inflation surge of 3.8% in the US
- **Yellow Card announces $40 million funding round to expand its stablecoin infrastructure** (20260805131500)
  - 0.879 · 20260806034500 · Yellow Card raises $40m to expand stablecoin infrastructure

Runtime: 26.4 min on mps.
