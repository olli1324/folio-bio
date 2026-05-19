# BioLitAgent — Strategy Brief

_Prepared for Oliver · AI Agent Olympics Hackathon (Milan AI Week) · 15 May 2026_

## The project

BioLitAgent is a literature intelligence tool for drug discovery. A researcher
describes a target protein or disease area, and the system searches PubMed,
pulls the relevant papers, extracts compound candidates and mechanisms of
action, and writes a structured research briefing the team can act on. The
value pitch is concrete: it replaces roughly a day of manual literature review
per query.

dummetts already has a basic backend and frontend running. The idea is
effectively locked, so the open questions are about team coordination and who
owns what.

## The team's plan, and whether it holds up

dummetts proposed that each person builds against a different sponsor challenge
under one shared topic, then everyone integrates:

- cambeni → Speechmatics (real-time speech-to-text)
- Oliver → Featherless (domain-specialised open-source agent)
- dummetts → Vultr (web-based enterprise agent, deployment)

This is a reasonable instinct, but two things need checking before the team
commits.

**One submission usually carries one challenge tag.** On lablab hackathons a
project is normally submitted under a single partner track and judged in that
category. Building with three sponsors' tech in one app does not automatically
make the project eligible for three separate prizes. Before the team splits
work on that assumption, someone should ask in the hackathon Discord whether a
single project can be tagged for multiple challenges. If it cannot, the team
still benefits from a multi-sponsor build (it makes for a stronger single
submission), but they should pick the strongest track for the formal entry
rather than spreading thin.

**"Build separately, merge later" is the classic way hackathon teams lose
Sunday.** cambeni already flagged this in the channel ("we could also focus and
help each other"). The fix is not to abandon the split — it is to make the
split happen inside one repository from the start. One app, one deploy, each
person owning a module with a clear interface. Integration then becomes a
merge of small pieces instead of a fusion of three apps the night before the
deadline.

So the recommendation to bring back to the team: keep the per-person ownership,
but build into a single shared repo today, and confirm the multi-track question
with the organisers before betting the strategy on it.

## How BioLitAgent maps to each sponsor

- **Featherless** wants a domain-specialised, async-first, fully open-source
  agent. The BioLitAgent research engine — the search-to-briefing pipeline — is
  exactly that. It runs as a background job, not a chatbot, and it is
  specialised to one domain. This is Oliver's piece and it is the core of the
  product, not a side feature.
- **Speechmatics** wants voice-first interaction. Natural fit: let a researcher
  describe the target by voice, or have the agent read the briefing back. This
  is cambeni's layer on top of the engine.
- **Vultr** wants a web-based enterprise agent deployed on their infrastructure,
  ideally used as the system of record. dummetts owns deployment and the web
  app shell; the pipeline state, job history and briefings live on Vultr.

The pieces stack cleanly because they are layers of one product (engine →
voice → web app + deploy), not three separate products.

## Oliver's scope

The Featherless-powered literature engine: the async pipeline that takes a
query and produces a structured briefing, with every model call going through
Featherless open-source models. Technical detail is in
`biolitagent_spec_v1.md`. In short, the work is:

1. Wire Featherless into the agent loop (OpenAI-compatible API, drop-in).
2. Build the pipeline nodes: search PubMed → retrieve → extract → synthesise →
   format briefing.
3. Make it run as a background job with visible progress, not a blocking call.
4. License the module cleanly (MIT or Apache) and document it so it is
   reproducible — Featherless explicitly rewards this.

## A realistic estimate for dummetts

Given dummetts asked for time estimates and today is Friday 15 May with a
Monday 19 May 17:00 deadline:

- **Functional end-to-end pipeline on Featherless** — by Sunday 17 May evening.
  Query in, structured briefing out, running as a background job.
- **Production-shaped and open-sourced** — by Monday 18 May midday. Error
  handling, the licence, the README, reproducible setup.

That leaves Monday afternoon as buffer for integration with cambeni's voice
layer and dummetts' deployment. Worth saying explicitly to the team that the
estimate assumes the shared repo exists by end of Friday — if everyone is still
on separate codebases Saturday, the Sunday target slips.

## Things to raise with the team

- Confirm with organisers: can one project be tagged for multiple challenges?
- Agree on the shared repo and module boundaries today, before anyone writes
  more code.
- Decide the single strongest track for the formal submission as a fallback.
- Who is the fourth teammate, and which layer do they own? The channel only
  shows three names with assignments.
