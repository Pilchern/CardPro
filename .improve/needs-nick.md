# Needs Nick

Answer inline under any item (write "Nick:" and your answer). The PM reads this at the start of each run.

## N1 (2026-10-20) RESOLVED: eBay API license vs. what CardPro does. Decision: accept the risk, or change course?
**Decision request:** Reply "accept", "ask eBay", or "stop Browse".

eBay's API License Agreement (edp.ebay.com/join/api-license-agreement, footer dated Sept 3 2025, verified by the PM 2026-10-20):
- §9.5(e): you may not "use eBay Content, either alone or in combination with third-party information, to suggest or model prices".
- §8.1.4.2(f): deriving "conversion, completion or success rates" needs eBay's prior written permission.
- §8.1.3: displayed listing info may not be more than 6 h older than eBay's site.
- There is no carve-out for personal or non-commercial use.

CardPro uses Browse API listings to model a market price for each card (the asking-price comp corpus). On a plain reading that is §9.5(e). Not legal advice.
- Likely consequence (inferred): eBay revokes the developer keyset. Browse supplies ~90% of listings, so the app falls back to alert emails only. Enforcement against a single-user, ~50-calls/day tool is unlikely (hypothesis), but the keyset may be tied to the eBay account you sell on.
- **PM recommendation: "accept" for the current use**, at today's volume, and don't expand eBay API use. The PM has frozen the one expansion that was on the table (a daily getItem re-check to infer sales, which is also §8.1.4.2 territory). The option the PM would not take is "ask eBay": asking for permission puts the app on their radar with low odds of a yes.
- Until you answer: no new eBay API calls get built. Nothing else changes.

Nick (2026-10-20, chat): "Ignore the ebay stuff, they're not worried about me." -> accept. eBay API expansion unfrozen.

## N2 (2026-10-20): Sold data. The automated route is closed; the hand-entry list has sat unused for 4 days.
**Decision request:** Reply "drop it" (the app stays ASKING-only, Maybe-tier flags only) or "keep the list" (leave the 10 commands in the email footer).

Findings this run (fact): the corpus can't infer sales (listings fall off the 200-item search page whether or not they sell). Browse never returns sold items or prices. Finding/Shopping APIs were decommissioned 2025-02-05. Marketplace Insights is closed to new apps. So real SOLD comps come only from your hand entry or your own flips (`data/flips.csv`).
- **PM recommendation: keep the list**, but the PM will stop treating sold data as the next build and work on flag yield and email quality inside the ASKING world.

## N3 (2026-10-20): The daily email is ~40 browse cards and 0 deals. Trim it?
**Decision request:** Reply "trim" (on no-deal days: subject "No deals" + auctions with bidding room, then 5 cards max per browse section; CHEAP FINDS dropped), or "keep" (as is).

Fact (Scout, 10-17..10-19): every post-merge email was "10 auctions to review", 0 deals, 40 cards (the focus cap), ~4,000 words in an offline rehearsal. The config records that you collect /99-and-under and "cool cards", so the PM won't cut browse sections without your say.
- **PM recommendation: "trim".** CHEAP FINDS are sub-$10 cards that can't net $15 under the fee model (a $1 card has to sell for ~$33). The collecting sections stay, just shorter.
