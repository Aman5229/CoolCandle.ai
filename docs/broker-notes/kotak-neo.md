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
- API orders are accepted only from the whitelisted static IP.
- Never retry a failed `place_order`: check the order book first, or risk a duplicate order.
- Every number arrives as text or a float, so convert with `Decimal(str(x))`.
- Confirm with Kotak that automatic exits and trailing are allowed before enabling them live.
