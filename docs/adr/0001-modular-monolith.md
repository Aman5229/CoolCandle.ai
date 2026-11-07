# 0001: A modular monolith with ports

**Status:** accepted. Amended by 0004: the buddy's model loop runs as its own service.

One backend process, one web app, one database, in Docker on one server. Modules talk through
small ports, and import-linter enforces the layers: `domain` is pure, `services` never import
`adapters`, and wiring happens only in `main.py`.

Services use SQLAlchemy sessions directly instead of a repository port per table. A port for every
table would be a lot of code for one database that will not change.

`Broker.order_updates()` became `sync()` plus `drain_updates()`. Pulling updates keeps replays and
tests deterministic, and on Kotak the order feed only wakes the sync, so no feed payload is parsed.
