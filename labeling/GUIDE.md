# Headline labeling guide

Label each headline in `sample.csv` **positive**, **negative** or **neutral** with the labeling tool (`uv run python -m mbpe.labeling.label`), which saves to `labels.csv`.

Headlines listed in `excluded.csv` are skipped and left out of the model comparison (see that file for why).

## What you're labeling
The **tone of the headline as financial news**: is it reporting something good, bad or neither for the subject it's about?

**Not the expected Bitcoin price impact.** Don't ask "would this make BTC go up?". Ask "is this headline good or bad news, as written?". For example, "Inflation falls more than expected" is positive news. You don't need to judge what it means for crypto.

## Rules
- **Judge the headline only.** Don't look up the article, the date or what happened next. The sample deliberately leaves out dates and sources.
- **Use the subject's point of view.** "Exchange X fined $50m" is negative even though a regulator "won".
- **Mixed or hedged headlines:** pick the dominant tone. If good and bad are balanced, use neutral.
- **Questions, explainers, how-tos, listings, price tickers without direction, and announcements without a judgment** are neutral.
- **Price moves:** rises, records and inflows are positive; falls, crashes and outflows are negative. A plain level with no direction ("Bitcoin trades at $67,000") is neutral.
- **Unsure after ~10 seconds:** choose neutral. Speed and consistency matter more than precision on borderline cases.

## Checklist for unclear headlines
1. **Does the headline report a clear outcome?** A court ruling, a win or loss, money gained or lost, a price move. A description with no event ("X is shaping Y") has no outcome to judge.
2. **Is the tone coming from facts or from wording?** Upbeat or alarming adjectives ("unprecedented", "massive", "shocking") are framing, not news. Opinion and promotional pieces often read this way. Judge the news, not the framing.
3. **Still unsure after 1 and 2?** Use the tie-breaker: neutral.

## Examples (invented; not from the sample)

**positive**
- "Bitcoin climbs to record high as ETF inflows accelerate"
- "US inflation cools more than expected in March"
- "Crypto lender completes repayment of all customer funds"

**negative**
- "Exchange halts withdrawals after $200 million hack"
- "Fed warns rates may stay higher for longer as prices rise"
- "Stablecoin loses its dollar peg, falling to 92 cents"

**neutral**
- "What is a spot bitcoin ETF? A beginner's guide"
- "Federal Reserve to announce rate decision on Wednesday"
- "Coinbase to report third-quarter earnings next week"
