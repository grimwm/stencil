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

## References

::: {#refs}
:::
