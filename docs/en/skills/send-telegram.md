---
layout: default
title: "Send Telegram"
grand_parent: English
parent: Skill Guides
nav_order: 46
lang_peer: /ja/skills/send-telegram/
permalink: /en/skills/send-telegram/
generated: true
---

# Send Telegram
{: .no_toc }

Send textual messages or files (documents, images, logs, reports) to a Telegram chat using a Telegram Bot. Trigger this skill whenever the user asks to send something via Telegram, says "Telegram this to me", or requests that outputs or files be forwarded to their Telegram account.
{: .fs-6 .fw-300 }

<span class="badge badge-free">No API</span>

[View Source on GitHub](https://github.com/tradermonty/claude-trading-skills/tree/main/skills/send-telegram){: .btn .fs-5 .mb-4 .mb-md-0 }

<details open markdown="block">
  <summary>Table of Contents</summary>
  {: .text-delta }
- TOC
{:toc}
</details>

---

## 1. Overview

# Send to Telegram Skill

---

## 2. Prerequisites

- Telegram Bot API (TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID env vars)
- Python 3.9+ recommended

---

## 3. Quick Start

Invoke this skill by describing your analysis needs to Claude.

---

## 4. Workflow

See the skill's SKILL.md for the complete workflow.

---

## 5. Resources

**Scripts:**

- `skills/send-telegram/scripts/send_telegram.py`
- `skills/send-telegram/scripts/telegram_interactive.py`
