---
# Backticks in both, deliberately: every title-slide field is built as
# pandoc.Inlines by frontmatter-filter.lua, so these render as <code> on the
# accent gradient. That pairing is stn-7i8, and it is what keeps
# `.slide--title code { color: inherit }` honest -- revert the rule and this
# fixture is the page that shows it at 1.06:1.
#
# It also exposes stn-myk, which is a DIFFERENT open defect and not a
# regression from this file: the <title> element cannot hold markup, so the
# browser tab shows the literal <code> tags until that ticket lands.
title: "Flow, Limits, and `WIP` Specifications"
subtitle: "Kanban and its `pull`-based neighbors"
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

::: {.revealable label="Presenter notes"}

Ask what the class expects $W$ to do as utilization approaches one before
showing the next slide. The toggle is on the page for everyone, so
check-access measures it in both themes; the body it opens is built from the
tokens the takeaway above already proves.

:::

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
