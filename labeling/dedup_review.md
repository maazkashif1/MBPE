# Dedup review (threshold 0.85, window 24 h, clusters capped at 24 h from their first member)

- Rows: 1,885,538; clusters: 979,493 (1.93 per cluster)
- Largest cluster: 644 rows; longest span (first to last member): 24.00 h; clusters spanning > 12 h: 44,276

| cluster size | clusters |
|---|---|
| 1 | 759,512 |
| 2 | 121,474 |
| 3-5 | 66,319 |
| 6-10 | 16,965 |
| 11-50 | 12,145 |
| >50 | 3,078 |

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
- **Spike in inflation presents further challenges for the** (20220413120000)
  - 0.873 · 20220413120000 · Spike in inflation presents further challenges for the Biden administration
  - 1.000 · 20220413120000 · Spike in inflation presents further challenges for the Biden administration
  - 1.000 · 20220413120000 · Spike in inflation presents further challenges for the Biden administration
  - 1.000 · 20220413121500 · Spike in inflation presents further challenges for the Biden administration
  - 1.000 · 20220413121500 · Spike in inflation presents further challenges for the Biden administration
  - 1.000 · 20220413121500 · Spike in inflation presents further challenges for the
- **BSP forecasts 7.5%-8.3% inflation for January** (20230131050000)
  - 0.871 · 20230131141500 · BSP: January inflation to settle within 7.5 to 8.3 %
  - 0.891 · 20230131183000 · BSP: January inflation could still breach 8%
  - 0.920 · 20230131183000 · BSP: Jan inflation likely hit 7.5-8.3%
- **tata group: Rise in interest rates revealed hidden stresses in parts of banking sector: Tata Group Chairman** (20230627181500)
  - 0.857 · 20230627193000 · Press Trust of India: Rise in interest rates revealed hidden stresses in parts of banking sector: Tata Group Chairman
  - 1.000 · 20230627193000 · Press Trust of India: Rise in interest rates revealed hidden stresses in parts of banking sector: Tata Group Chairman
- **Binance.US Asks Court For Protective Order Against the SEC** (20230816063000)
  - 0.879 · 20230816073000 · Binance Seeks Protective Order Against SEC, Reuters Reports
  - 0.855 · 20230816151500 · Binance Requests Protective Order Against SEC in Legal Move
  - 0.913 · 20230816224500 · Binance.US request for protective order against SEC goes to magistrate judge
- **Bank of England ends run of 14 straight interest rate hikes after cooler-than-expected inflation** (20230921113000)
  - 0.862 · 20230921114500 · Bank of England rate hikes: Bank of England holds rates after 14 straight hikes on cooler-than-expected inflation
  - 0.914 · 20230921114500 · Bank of England ends run of 14 straight interest rate hikes
  - 1.000 · 20230921114500 · Bank of England ends run of 14 straight interest rate hikes after cooler-than-expected inflation
  - 1.000 · 20230921120000 · Bank of England ends run of 14 straight interest rate hikes after cooler-than-expected inflation
  - 1.000 · 20230921121500 · Bank of England ends run of 14 straight interest rate hikes after cooler-than-expected inflation
  - 0.980 · 20230921151500 · Bank of England ends 14 straight interest rate hikes
- **Japan stocks lead gains in Asia as Nikkei climbs more than 2% in return from holiday** (20240813021500)
  - 1.000 · 20240813021500 · Japan stocks lead gains in Asia as Nikkei climbs more than 2% in return from holiday
  - 1.000 · 20240813023000 · Japan stocks lead gains in Asia as Nikkei climbs more than 2% in return from holiday
  - 1.000 · 20240813024500 · Japan stocks lead gains in Asia as Nikkei climbs more than 2% in return from holiday
  - 1.000 · 20240813031500 · Japan stocks lead gains in Asia as Nikkei climbs more than 2% in return from holiday
  - 0.871 · 20240813034500 · Japan stocks lead gains in mixed Asia-Pacific markets with Nikkei up more than 2%
- **Australian Inflation Pressures Eased in July** (20240828034500)
  - 0.875 · 20240828051500 · Australian Inflation Pressures Eased in July, Helped by Government Rebates
  - 0.862 · 20240828104500 · Australia Inflation Eases In July
  - 1.000 · 20240828111500 · Australia Inflation Eases In July
  - 1.000 · 20240828121500 · Australia Inflation Eases In July
  - 0.886 · 20240828160000 · Australian Inflation Eases in July Due to Rebates, but Broader Price Pressures Persist
- **Donald Trump doesn't share details about his family's cryptocurrency venture during X launch event** (20240917021500)
  - 1.000 · 20240917021500 · Donald Trump doesn't share details about his family's cryptocurrency venture during X launch event
  - 1.000 · 20240917021500 · Donald Trump doesn't share details about his family's cryptocurrency venture during X launch event
  - 1.000 · 20240917023000 · Donald Trump doesn't share details about his family's cryptocurrency venture during X launch event
  - 1.000 · 20240917024500 · Donald Trump doesn't share details about his family's cryptocurrency venture during X launch event
  - 1.000 · 20240917024500 · Donald Trump doesn't share details about his family's cryptocurrency venture during X launch event
  - 1.000 · 20240917024500 · Donald Trump doesn't share details about his family's cryptocurrency venture during X launch event
- **Trump launches cryptocurrency with price rocketing** (20250119133000)
  - 1.000 · 20250119151500 · Trump launches cryptocurrency with price rocketing
  - 1.000 · 20250120060000 · Trump launches cryptocurrency with price rocketing
  - 0.878 · 20250120080000 · Donald Trump Launches $Trump Cryptocurrency, Igniting Financial Frenzy
- **Coinbase CEO Says CLARITY Is 'Closer Than Ever'** (20260513180000)
  - 0.876 · 20260513223000 · Coinbase CEO Brian Armstrong Says Clarity Act 'Closer Than Ever'
- **India's central bank holds rates** (20260805084500)
  - 0.865 · 20260805131500 · India Central Bank Holds Key Interest Rates Steady
  - 1.000 · 20260805133000 · India Central Bank Holds Key Interest Rates Steady
  - 1.000 · 20260805143000 · India Central Bank Holds Key Interest Rates Steady

Runtime: 8.5 min on mps.
