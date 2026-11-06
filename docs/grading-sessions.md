# Grading sessions

How to run a grading pass against a local model without paying for the same
prompt twice. The numbers below come from `qwen3.8-27b-q6` on a llama.cpp router
with `--ctx-size 131072`, quantized KV cache, `--parallel 1`, and the default
medium reasoning effort.

## The cost is prefill, not thinking

Grading reads a large context and then writes a small verdict. Decode is not
the bottleneck: output streams at 78 to 94 pieces per second whether the prompt
holds one paragraph or thirty-five thousand tokens. Time to first token is the
bottleneck, and it scales with prompt length:

| Prompt tokens | Time to first token |
| --- | --- |
| 23 | 0.3 s |
| 3,354 | 1.7 s |
| 16,684 | 6.2 s |
| 41,684 | 13.1 s |

That is roughly 2,000 tokens per second of prefill. A turn that must read
thirty-five thousand tokens waits about seventeen seconds before the first
character appears, which is why a grading pass looks idle while it is working.

## Prompt caching decides whether you pay it again

The server caches the processed prefix and reuses it when the next request
starts with the same bytes. Measured on a 35 K-token prompt:

| Change between turns | Time to first token |
| --- | --- |
| identical prompt | 0.2 s |
| change at the start of the prompt | 17.3 s |
| append at the end of the prompt | 1.1 s |
| return to the original prompt | 0.6 s |

Appending is nearly free. Editing near the start re-prefills everything after
the edit. An interleaved request from another client did not evict the cache,
so concurrent use is not the hazard; a moving prefix is.

## Rules for a pass

1. **Grade from the generated brief, not the corpus.** `skillkit-evals` writes
   a review brief per batch under `reports/`, holding the spec summary, the
   rubric row, and the newest run's evidence per case. Grading from one brief
   section keeps the prompt near four thousand tokens instead of forty
   thousand. Loading `rubric.md`, `cases.md`, and raw `events.jsonl` together
   costs the full prefill for every turn.
2. **Grade one case per session.** A single case keeps the prompt small and the
   prefix short. Switching cases mid-session moves the whole context, which is
   the expensive change rather than an append.
3. **Keep the prefix byte-stable.** Nothing near the start of a grading prompt
   may change between turns: no current timestamp, no run counter, no
   "session started" line. Provisioning scripts are a common offender, because
   `sync-skills.sh` regenerates always-on instruction files such as
   `~/.codex/instructions.md` and relinks the harness instruction links. Any of
   those changing mid-pass invalidates everything after it.
4. **Append, do not rewrite.** Follow-up questions about the same case belong at
   the end of the same conversation. Re-sending a corrected brief is cheaper
   than restarting, and appending is the cheapest of the three.
5. **Keep the run model loaded.** The router runs one instance at a time. A
   request for any other model unloads the current one, and the next grading
   turn pays a reload before it pays prefill.
6. **Lower reasoning effort for mechanical grades.** At medium effort a short
   answer can spend its whole token budget on reasoning and return empty
   content. Reasoning tokens also multiply turns, and every turn pays prefill.

## Reading the trace

A reasoning stream split into blocks labelled "Thought for 3s" is not three
separate thoughts. Investigate when the text joins mid-word across blocks
(`...the student`, `'s own prose`), which is one streamed thought rendered as a
block per delta. It looks like activity without progress, but the wall-clock
time is in the prefill that preceded the first block.

## What this guidance does not fix

A cold prefix on a forty-thousand-token context costs about twenty seconds
however the pass is arranged. Brief-first grading and a stable prefix remove
the repeated charge; they do not make the first read cheaper. Reducing
`--ctx-size` to match the real working set lowers the KV cache and prefill cost
further, at the price of refusing prompts that no longer fit.
