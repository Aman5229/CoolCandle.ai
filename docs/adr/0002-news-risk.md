# 0002: News risk in the guard

**Status:** default, see D12

Index options move hard on scheduled news: RBI policy, the Budget, Fed decisions. Premiums jump,
implied volatility collapses after the event, and stops can slip past their price.

- A calendar of events lives in `market_events`. Today's calendar cannot be edited, like the plan.
- A high-impact event with a time blocks new entries 15 minutes either side.
- Any high-impact event doubles the slippage buffer for that day, which shrinks the size the guard
  allows.
- New signals are held back inside the window.
- Exits, stops and tighter stops are never blocked: news is when you most need to get out.

Headlines only inform. They are leads, not facts, so nothing automated acts on one.
