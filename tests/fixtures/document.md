---
title: "Flow, Limits, and Specifications"
subtitle: "Kanban and its neighbors"
author: Ada Lovelace
date: 2026-09-02
bibliography: refs.bib
---

## Little's Law

Little's Law relates the three [@little1961]. Inline $L = \lambda W$, and
display:

$$W = \frac{L}{\lambda}$$

```{.mermaid caption="Requests flow from the client through the API to the database"}
flowchart LR
  client --> api --> db
```

![Cumulative flow](images/flow.svg)

::: {.hidden}

Answer: the WIP limit forces the queue to drain before new work enters.

The presenter-only source is @reinertsen2009. It is cited nowhere else, so a
build without WITH=hidden must not list it -- that is the leak this fixture
exists to catch.

:::

## Fields on the flow record

The header row is backticked on purpose. Inline code in a `thead` cell lands on
the darkest fill in the palette, and until `stn-1y7` nothing in this suite
rendered that pairing -- so `make check-access` measured every surface except
the one that failed.

| `field`      | Meaning                           | Example    |
| ------------ | --------------------------------- | ---------- |
| `arrival_ts` | When the item entered the queue   | `08:15:00` |
| `wip_limit`  | Items allowed in progress at once | `4`        |
| `lead_time`  | `departure_ts` minus `arrival_ts` | `36m`      |

: Flow record fields. The caption is backticked too -- `caption` sits on
`--surface-accent`, a second fill body prose never reaches.

## References

::: {#refs}
:::
