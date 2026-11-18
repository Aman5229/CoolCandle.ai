# Kotak Neo notes

SDK: PyPI `kotakneoapi`, imported as `neo_api_client`. Synchronous, returns errors as dicts, and
never retries orders. All adapter calls run in a thread.

## Status of the adapter

The adapter has been tested against a stand-in for the SDK (`tests/contract/`) and has made one
real read-only connection. **It has never placed, modified or cancelled an order at Kotak.** Check
each item below on the first supervised live session, at one lot.

### Seen on this project's own account (7 November 2026, market closed, no orders placed)

- Login through the app's broker connect works.
- An empty order book or position list is not an empty list but a refusal with the message
  `No Data`. The adapter reads it as empty. Which field carries the message (`emsg` or `errMsg`)
  was not recorded, so both are accepted.

### Seen on this project's own account (16 November 2026, market open)

- The live feed, the option chain, funds and the order book all worked from a laptop on a home
  connection.
- Every `place_order` from that laptop was refused with HTTP 401 and
  `{"stCode": 100008, "errMsg": "unauthorized", "stat": "Not_Ok"}`. No order number came back, and
  nothing reached the exchange. The laptop's IPv4 address was the registered one, but Kotak's
  hosts also have IPv6 addresses and macOS preferred IPv6, so the orders came from an IPv6
  address Kotak did not know. The SDK's REST client now gets an httpx transport bound to
  `0.0.0.0` (`kotak_neo/client.py`), which keeps every call on IPv4. A server with IPv6 needs the
  same fix.
- Over IPv4, `place_order` was accepted and returned an `nOrdNo` (e.g. `261116000442620`). Kotak's
  margin check then rejected the order a moment later; the rejection came through the order book
  sync with the text `RMS:Margin Exceeds,Cash Available:…,Additional margin required:…`. The
  app's own funds figure did not foresee it, so the `limits()` field names are still unverified.

### The first live trade (16 November 2026): no stop reached Kotak

- The buy (NIFTY 25250 CE, 65 at 83.65) was accepted and filled. The SL sell that followed was
  refused twice at the API with `error from core` (no order number), and so was every exit order
  after it: **1,456 exit orders in 15 minutes**, all refused, because exits retried with no
  backoff or cap. The position was sold at 15:24:23 at 79.75 outside the app. The app's trade
  row was left in `NEEDS_ATTENTION` with no P&L.
- Every order on the trade carried the same `ig` tag (the trade id). The buy, the first to use
  it, was accepted; every later order with that tag was refused. **Likely cause: Kotak wants a
  unique tag per order.** Not yet proven: confirm with one supervised test before trading live.

### From the previous project, verified there (October 2026)

- Login: `totp_login` then `totp_validate`.
- NIFTY scrip master columns, the 1980 expiry epoch, strikes and tick size in paise.
- Order book fields: `nOrdNo`, `ordSt`, `fldQty`, `avgPrc`, `trnsTp`, `prcTp`, `trdSym`, `tok`.
- Position fields: `flBuyQty`, `buyAmt`, `multiplier`, `genNum`, `prcNum` and the rest.
- The live feed for NIFTY, and history with its strict rate limit.

### Not yet verified against Kotak

- **Modifying an SL order into a LIMIT order.** Every exit depends on this. If Kotak refuses it,
  exits fall through to the 10-second `NEEDS_ATTENTION` flag, so test it first, far from the market.
- **Funds:** the `Net` and `MarginUsed` field names from `limits()` are assumptions.
- **SENSEX:** the `bse_fo` scrip file and the index feed name have not been seen in a recording.
- **The `tag` field:** a 36-character trade id is sent. Check Kotak keeps it whole in `GuiOrdId`,
  because finding an order after a timeout relies on it.
- **The order feed:** any message wakes the order sync, with a 2-second poll as the fallback.
- **Error shapes:** a failed `place_order` is treated as an unknown outcome unless Kotak clearly
  refuses it, so a refusal in an unexpected shape is flagged instead of rejected.

## Rules that never bend

- One session per account: a login from anywhere ends the others. Never log in from a dev machine
  while the server is trading.
- API orders are accepted only from the whitelisted static IP, and only over IPv4.
- Never retry a failed `place_order`: check the order book first, or risk a duplicate order.
- Every number arrives as text or a float, so convert with `Decimal(str(x))`.
- Confirm with Kotak that automatic exits and trailing are allowed before enabling them live.
