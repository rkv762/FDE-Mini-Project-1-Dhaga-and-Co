# Discovery Note — Dhaga & Co.

_Dated before the first commit of application code. One page. Agreed by the whole group._

## The problem, in one sentence

> "We acquire well and retain badly — we keep buying the same customer twice instead of
> growing organically."

## Who owns it today

**Ritu, co-founder and CEO**, names this as the company's core problem. **Sameer, Head of
Growth**, owns the downstream symptom (rising acquisition cost) and today the only lever he
has is spending more on ads — there is no retention or re-engagement effort in place.
Merchandising is manual and generic: two merchandisers set the same homepage for every
visitor, every morning, by hand. Marketing (Meta/Google ads, CleverTap push/WhatsApp) targets
broad segments, not individual customers.

## The evidence

- "Repeat purchase rate has been stuck at twenty-two percent for six quarters. Every quarter
  we buy the same customer twice." — Ritu, CEO
- "Customer acquisition cost is up forty percent year on year. We cannot outspend our way out
  of this one." — Sameer, Head of Growth
- 3.1 million app installs against roughly 700,000 monthly active users — the majority of
  acquired users go dormant with no systematic effort to bring them back.
- 410,000 product reviews (18 months deep) and 18 months of Mixpanel app-event data (search
  queries, taps, filters, scroll depth) exist and are complete, but are "never analysed" —
  an unused signal for exactly this problem.
- The delivery infrastructure for personalization already exists (CleverTap for push and
  WhatsApp campaigns) — only the targeting and content logic is missing.
- The order history needed to measure this is already "clean and trustworthy": 11 million rows
  in Postgres with line items, payment mode, address and status history.

## What it costs them

CAC is rising 40% year on year because the business substitutes paid re-acquisition for
retention. At an average order value of ₹840 and a ₹310 crore GMV run-rate, even a few points
of improvement in repeat purchase rate converts directly into incremental GMV with no
additional ad spend — the reverse of what is happening today.

## What success looks like

Repeat purchase rate, computed per customer cohort directly from the orders table (already
held, already trusted). Success is a measurable lift in repeat purchase rate for customers who
receive personalized re-engagement versus a held-out control group, tracked monthly.

## Ranked shortlist

1. **Stuck repeat purchase rate / rising CAC (chosen)** — the CEO's own #1 stated problem,
   directly revenue-increasing rather than cost-saving, and the data needed to both build and
   measure it (orders, reviews, app events, an existing push/WhatsApp channel) already exists
   and is already trusted or complete.
2. **RTO on COD (26%, ~₹120/order logistics cost)** — real and named by Faizan, Head of Supply
   Chain, but it is a cost-avoidance problem, not a revenue-growth one, and COD is a customer
   expectation the client is unlikely to want to restrict.
3. **Returns landing in "Other" (44%, unread by Neha)** — a genuine analytics gap, but the
   value is indirect (better fit data, eventually fewer returns) and slower to show up as
   revenue.
4. **Support ticket load ("where is my order," 58% of tickets, Arpita)** — clearly costly in
   agent hours, but it improves cost and CX, not the top line, and is a narrower, more
   mechanical routing/deflection problem than a growth one.

## Biggest assumption

That personalized, data-driven re-engagement (the right product, at the right moment, in the
right language) moves repeat purchase rate more than the blanket campaigns already running
today. The evidence that would disprove this: an A/B test showing no statistically significant
difference in repeat purchase rate between customers who receive personalized nudges and a
control group over a full measurement window.
