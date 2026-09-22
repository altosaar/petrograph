---
title: ACT re-entry read — <short label>
date: 2026-01-01
slug: <slug>-<yymmdd>
stage: analysis
model:                          # the model that produced the analysis
session:                        # claude session id — resume it to write the compaction
sources:
  - obsidian: hunking_obsidian.py --since 7 --net
  - calendar: <view used>
prompts:
  analysis: prompts/act-analysis.md
related:
  compaction: compactions/<year>/<slug>-<yymmdd>.md   # the letter this fed, if written
tags: [analysis, act, re-entry]
---

<paste the ACT analysis output from Claude here — stances for re-entry, psychometrics,
open loops>
