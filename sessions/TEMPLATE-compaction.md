---
title: <Interlocutor> Compaction — <short label>
interlocutor: Bobby
date: 2026-01-01
slug: <slug>-<yymmdd>
stage: compaction
model:                          # the model that wrote the letter
session:                        # claude session id — the same one that produced the analysis
voice:                          # voice id (eleven) or name (cartesia) used to narrate
audio: share/audio/<slug>-<yymmdd>.mp3
sources:
  - obsidian: hunking_obsidian.py --since 7 --net
  - calendar: <view used>
prompts:
  analysis: prompts/act-analysis.md
  compaction: prompts/narrative-compaction.md
related:
  analysis: analyses/<year>/<slug>-<yymmdd>.md   # the ACT read this followed, if archived
tags: [compaction, re-entry]
---

# <Title>

<the letter — addressed to the interlocutor, signed off. Body only: the TTS tools strip this
front-matter before narrating, so the same file is both the durable record and the input.>
