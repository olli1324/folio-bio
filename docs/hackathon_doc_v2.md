# AI Agent Olympics — Milan AI Week Hackathon

_Reference summary · v2, 15 May 2026 · supersedes v1 (built from the full
hackathon page content, which v1 didn't have)._

## Snapshot

- **Event:** AI Agent Olympics — the official hackathon of Milan AI Week 2026,
  run by lablab.ai.
- **Dates:** 13–20 May 2026. Online build 13–19 May. On-site build day 19 May
  (Fiera Milano, for selected participants). Awards 20 May.
- **Submission deadline:** 19 May, **16:00 CEST** ("End of Submissions" on the
  official schedule). Note: the Luma listing said 17:00 — the lablab page says
  16:00, so treat 16:00 as the real cutoff and confirm in Discord. Either way,
  our earlier build docs assumed 17:00 and should be pulled an hour earlier.
- **Venue:** Fiera Milano, Rho, Milan (on-site 19–20 May).
- **Prize pool:** $32,000+.
- **Format:** solo or team. Free Milan AI Week conference ticket included.
  Runs on the lablab.ai platform plus the lablab Discord — register for both.

## What it is

AI Week is Europe's largest AI event: 700+ speakers, 250+ exhibitors, 25,000+
attendees. The hackathon is its builder track. The brief is autonomous agents
that make real decisions and create enterprise value, not copilots or chat
wrappers.

## The five challenge themes

Framing for what to build, not the prize tracks:

- **Intelligent Reasoning** — agents that analyse inputs, decide independently,
  and replan around roadblocks without a human.
- **Agentic Workflows** — the agent plans its own steps, calls tools (APIs,
  databases, browsers), and manages multi-step tasks over time.
- **Enterprise Utility** — solve a real friction point for the managers and
  founders at AI Week.
- **Multimodal Intelligence** — process images, documents, audio, or video.
- **Collaborative Systems** — multiple specialised agents coordinating on a
  goal a single LLM couldn't handle.

## Sponsor challenges

Five partners, each with its own challenge and prizes. A project is submitted
under a challenge tag, and one team can build a single app that addresses
several challenges.

**Vultr — web-based enterprise agent deployed on Vultr.** A production-style
web app for real workflows (operations, sales, marketing, support, HR). Vultr
powers the backend infrastructure. Required deliverables: a GitHub repo with
docs, a Vultr VM backend deployment, a public demo URL, a recorded demo video,
and a clear architecture write-up. Vultr Serverless Inference is available;
Vultr GPUs are not. $200 in credits per participant (first 300).

**Google — agents built on Gemini.** Use Gemini via Google AI Studio or the
Gemini API for reasoning, chat, or multimodal understanding, in an agent-driven
workflow with a working prototype. Gemini Flash for speed, Gemini Pro for
heavier reasoning. Free Gemini API allowance, plus $300 Google Cloud credits
for new accounts.

**Kraken — autonomous trading agent.** Build an agent that trades xStocks
(tokenised US equities) using the Kraken CLI as the execution layer. Ranked in
two independent categories: Trading Performance (pure net PnL over the window,
with a Kraken audit of top agents) and Social Engagement (a 30-day "build in
public" score from public-platform metrics). One submission per participant or
team for this challenge. Submission uses a read-only Kraken API key.

**Featherless — domain-specialised, open-source agent.** Pick one real domain
(legal, medical, logistics, finance, research, code review, and so on) and do
that one task exceptionally well. They want an async-first architecture
(background jobs, document pipelines, monitoring), a fully open-source release
under MIT or Apache 2.0 with reproducible prompts and orchestration, and
something production-shaped. Feather Premium is free for the hackathon (the
$25/month plan at 100% off), valid one month. _This is Oliver's track._

**Speechmatics — voice and real-time speech.** Conversational AI, voice agents,
or real-time systems built on Speechmatics speech-to-text. They highlight
real-time transcription inside agent workflows, speaker diarization, batch
transcription, meeting/call summarisation, and accessibility tools. $200 in API
credits per participant (first 200). The coupon code is only shared during the
kick-off stream.

## Prizes

$32,000+ total, split per sponsor:

- **Vultr** — 1st: $5,000 cash + $1,000 credits; 2nd: $3,000 + $1,000; 3rd:
  $1,000 + $1,000.
- **Google** — 1st: $5,000; 2nd: $3,000; 3rd: $2,000.
- **Kraken** — Trading Performance: $1,800 / $750 / $450. Social Engagement:
  $1,200 / $500 / $300.
- **Featherless** — 1st: 500 inference credits + Claw Pro ($200); 2nd: 300 +
  Claw Pro; 3rd: 100 + Claw Pro. (Non-cash.)
- **Speechmatics** — 1st: $1,000 cash + $1,000 credits; 2nd: $500 + $1,000;
  3rd: $500 credits.

The cash sits in the Vultr and Google tracks. Featherless is credits-only, so
if the team wants cash, Vultr and Google are the ones to aim the single app at
while still tagging Featherless and Speechmatics.

## Judging criteria

Four equal-looking criteria:

- **Application of Technology** — how effectively the chosen models are
  integrated.
- **Presentation** — clarity and effectiveness of the pitch.
- **Business Value** — real-world impact and fit to business use cases.
- **Originality** — uniqueness and creativity of the approach.

Presentation is a named criterion, so the 20 May pitch is scored work, not an
afterthought.

## What to submit

- Basic info: project title, short description, long description, technology
  and category tags.
- Media: cover image, video presentation, slide presentation.
- Code and hosting: public GitHub repo, demo application platform, application
  URL.
- Submissions must be original and **MIT-compliant** — the whole project needs
  a permissive licence, not just the Featherless module.

## Schedule (all times CEST)

**13 May** — kick-off at 17:00: lablab and sponsor opening words (Featherless:
Isaac Gemal; Speechmatics: Edgars Adamovics), intro to the challenge, hackathon
guide, Discord Q&A at 18:00.

**19 May (on-site, Milan)** — doors 10:00; opening words 10:30; Project
Submission Workshop 11:10; **End of Submissions 16:00**; hackathon area closes
16:30; DJ set and party 17:00; venue closes 20:00.

**20 May** — doors 10:00; **Winners Ceremony 13:30**.

## Competitive landscape

Projects are already being submitted, and several are close to BioLitAgent's
space — worth knowing what the room looks like:

- _Manthan_ — an open-source agentic business analyst for enterprise data,
  on the Vultr track. Closest in shape: agentic, enterprise, open-source.
- _TELMED AI Doctor_ — medical diagnosis via text, voice, and image.
- _NEXUS_ and _Vela_ — enterprise "command centre" multi-agent dashboards
  using Featherless and Gemini.
- _Memory Core_ — an MIT-licensed role-aware memory layer for agent teams.
- Several Kraken trading agents (_VORTEX_, _Project Doomsday_, _AutoResearch_).

Takeaway for us: enterprise-analyst and medical agents are already present, so
BioLitAgent's edge has to be the depth of the drug-discovery domain
specialisation and the quality of the briefing output, not just "an agent that
reads documents." The judges will have seen several document-reading agents.

## Workshops, speakers and judges

Recorded workshops cover Vultr (Serverless Inference, Supabase, Coolify, credit
redemption), Featherless (Isaac Gemal — Quick Start and Live Demo), and
Speechmatics (credit redemption). The judge and mentor panel is large and
includes sponsor DevRel leads — Isaac Gemal (Featherless) and Edgars Adamovics
(Speechmatics) among them — alongside lablab founder Pawel Czech and a range of
senior engineers and product leads. Media and community partners include AI
Week and NativelyAI.

## Action items for our team

- **Move the build plan an hour earlier.** Our brief and spec assumed a 17:00
  deadline; the page says 16:00 on 19 May. Confirm in Discord, but plan for 16:00.
- **The whole repo must be MIT-compliant**, not just the engine module — this
  is a submission requirement, not only a Featherless preference.
- **The Vultr track has hard deliverables**: a Vultr VM deployment, a public
  demo URL, and a recorded demo video. That is dummetts' responsibility and it
  needs to exist well before the 16:00 cutoff.
- **Budget pitch time.** Presentation is judged, and we owe a cover image, a
  video, and a slide deck at submission.
- **Confirm the multi-track question** still — the "one submission per team"
  line is written under the Kraken challenge specifically, which suggests it
  may be Kraken-only, but it is worth a direct question in Discord.
