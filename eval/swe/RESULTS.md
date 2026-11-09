# SWE-bench handoff experiment

3 instances. Source: Codex (`gpt-5.6-sol`), rewound to just before its first edit (simulated out of usage). Takeover: a fresh Codex, plain prompt vs. the same prompt plus a `--brief` handoff written by `qwen3.5` via Ollama. Tokens are model input tokens.

| instance | source: solved uncut / calls before cut / ctx at cut | condition | resolved | handoff chars | ctx at start | peak ctx | total input | output | turns | tool calls | seconds |
|---|---|---|---|---|---|---|---|---|---|---|---|
| pytest-dev__pytest-10051 | yes / 3 of 7 / 14.3k | plain | yes (F2P 1/1) | 0 | 11.9k | 15.7k | 99.1k | 1.2k | 1 | 6 | 21.4 |
|  |  | smart | yes (F2P 1/1) | 3,862 | 12.9k | 18.3k | 129.2k | 1.3k | 1 | 7 | 19.4 |
| pytest-dev__pytest-10356 | yes / 4 of 27 / 17.2k | plain | no (F2P 0/1) | 0 | 12.7k | 33.7k | 503.8k | 4.0k | 1 | 16 | 144.6 |
|  |  | smart | no (F2P 0/1) | 4,159 | 13.7k | 22.4k | 323.6k | 3.1k | 1 | 13 | 58.7 |
| pytest-dev__pytest-7490 | yes / 3 of 10 / 22.1k | plain | yes (F2P 2/2) | 0 | 15.7k | 24.6k | 281.8k | 1.9k | 1 | 9 | 32.6 |
|  |  | smart | yes (F2P 2/2) | 3,921 | 16.6k | 28.2k | 262.2k | 2.1k | 1 | 9 | 34.9 |

| condition | resolved | mean ctx at start | mean peak ctx | mean total input | mean tool calls |
|---|---|---|---|---|---|
| plain | 2/3 | 13.5k | 24.7k | 294.9k | 10 |
| smart | 2/3 | 14.4k | 23.0k | 238.3k | 10 |

## Reading it (n = 1 per cell: a sanity check, not a benchmark)

- **Completion: no difference.** Both conditions resolved the same two tasks. On the third
  (10356), the source solves it when left alone (27 tool calls), but cut after 4, both takeovers
  wrote nearly the same fix and merged base-class marks in reverse MRO order, which the hidden test
  rejects. The source had not reached that decision by the cut, so no handoff could have carried
  it.
- **Context at start: the handoff costs about 1k tokens** (13.5k → 14.4k mean), on top of the
  ~12k-token baseline Codex spends before reading anything. Small next to the 14k-22k the source had
  used by the cut.
- **Total work: mixed, slightly in the handoff's favour.** On the hard task the smart takeover used
  36% fewer input tokens (324k vs 504k), a smaller peak context (22k vs 34k), 13 vs 16 tool calls
  and 59 vs 145 s. On the easiest task it used more (129k vs 99k: it ran the full test suite at the
  end). Mean total input 238k vs 295k (−19%). With one run each, this is within run-to-run noise.
- **What the handoff carried:** the source's diagnosis, its plan and the repo's conventions
  (changelog format, tests with every fix), with "no files edited yet" stated, not the false "fixed"
  an ungrounded model wrote in a first try.
- **What it would take to see an effect:** tasks the recipient can't redo cheaply from scratch.
  These three are solved from scratch in 6-16 tool calls, so the investigation a handoff saves is
  small. Larger repos, longer source runs before the cut, and 3+ runs per cell.

Grading note: the first grading pass scored 7490 and 10356 as failures for every run, because the
agents had added their own tests to the files SWE-bench's test patch touches and the patch no longer
applied. SWE-bench resets those files to the base commit first; the grader now does too, and
`swe.py regrade` re-scored the saved diffs without rerunning any agent.
