---
title: "Flow, Limits, and Specifications"
subtitle: "Kanban and its neighbors"
author:
  - Ada Lovelace
  - Grace Hopper
date: 2026-09-02
bibliography: refs.bib
---

## Little's Law

::: {.lead-in}

Queues are the hidden cost of high utilization.

:::

Little's Law relates the three [@little1961].

## Flow

::: {.columns .wide-right}

Kanban limits work in progress, so the queue has to drain before anything new
is pulled.

![Cumulative flow](images/flow.svg)

:::

## Diagram

```{.mermaid caption="Requests flow from the client through the API to the database"}
flowchart LR
  client --> api --> db
```

::: {.takeaway}

Limit work in progress before optimizing anything else.

:::

---

::: {.hidden}

## Presenter Notes

Ask the class where they think the knee is before revealing it.

:::
