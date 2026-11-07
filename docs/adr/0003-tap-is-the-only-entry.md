# 0003: The buddy has no entry tool

**Status:** accepted

The blueprint gave the buddy a `confirm_trade(confirm_token)` tool. Instead it has no tool that can
enter a trade. `propose_trade` shows a Confirm button, and the tap on that button calls
`POST /signals/{id}/take`, which creates a one-time token and submits through `OrderService`.

`OrderService` refuses any signal or agent entry without a valid token. So "never orders without a
tap" holds in code, whatever a model says, and needs no prompt to stay true. A test pins the tool
list.

Every number in a reply is also checked against tool results. A reply with an unsourced number is
held back and never reaches the trader.
