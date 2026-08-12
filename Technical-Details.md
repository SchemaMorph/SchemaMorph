# SCHEMAMORPH: Missing Algorithms and Parameter Sensitivity

This document supplements Section IV with the three procedures requested by Reviewer #2: tuple-based mutation, observation generation, and deduplication. It then reports a sensitivity study of the main mutation parameters.

## 1. Tuple-Based Mutation

Tuple-based mutation constructs an original state and expands it using insertion only. The original rows and their primary keys are never deleted or modified, so the mutated state preserves tuple containment by construction.

```text
Algorithm 1: TupleBasedMutation
Input: generated schema containing a main table and auxiliary tables
Output: related states (S_orig, S_mut), or SKIP

1  Create the tables and selected indexes.
2  Choose predicate columns and prepare hot, random, and boundary values.
3  Populate S_orig with a small mixture of these values.
4  Populate auxiliary tables, which remain unchanged afterward.
5  Generate nonempty observations on S_orig; SKIP if too few are valid.
6  Save the original observation outcomes.
7  Append N rows to the main table without modifying existing rows.
8  Bias the appended rows toward hot values to shift the distribution.
9  Verify that the main table grew; otherwise SKIP.
10 Refresh table statistics and return (S_orig, S_mut).
```

Steps 7--10 increase the likelihood of changing optimizer statistics and execution paths while preserving the state relation required by the oracle.

## 2. Observation Generation

Observation generation first collects valid, nonempty baselines on the original state and then verifies the same observations on the mutated state.

```text
Algorithm 2: GenerateAndVerifyObservations
Input: a validated state pair (S_orig, S_mut)
Output: verified observations and raw violations

1  Repeatedly generate a DQL or DML candidate from the supported grammar.
2  Reject duplicate SQL, unsupported constructs, and irrelevant-table queries.
3  Execute the candidate on S_orig.
4  Reject execution errors and empty or otherwise unobservable outcomes.
5  Store the candidate and its original outcome.
6  Inspect its plan on S_mut and prioritize candidates exhibiting plan changes.
7  Execute the same observation on S_mut.
8  Check result containment for DQL or affected-PK containment for DML.
9  Record the observation and report any relation violation.
```

DQL candidates include deterministic selections/projections, inner/self joins, derived tables, positive `IN/EXISTS`, and `UNION ALL`. Every nested query passes the same AST check. DML candidates are single-table `UPDATE/DELETE` observations with supported predicates and stable primary keys. Plan feedback affects prioritization only; it is not part of the oracle.

## 3. Deduplication

SCHEMAMORPH removes repeated candidates during generation and consolidates raw violations before reporting.

```text
Algorithm 3: DeduplicateViolations
Input: generated candidates and raw violations
Output: unique bug reports

1  During generation, remove candidates with identical SQL text.
2  Re-execute every raw violation to confirm reproducibility.
3  Remove unsupported-value, numeric-comparison, and driver artifacts.
4  Minimize each reproducible violation with SQLess.
5  Normalize the reduced state transformation, observation, and symptom.
6  Group violations with the same reduced reproducer/symptom.
7  Manually inspect root causes and submit one report per unique bug.
```

In our experiments, 106 raw violations yielded 10 false positives, 75 duplicates, and 21 unique bug reports after this process.

## 4. Sensitivity Study

### 4.1 Experimental Setup

We evaluate three parameters on MySQL, varying one parameter at a time while keeping the remaining configuration unchanged:

- `adm_growth`: the minimum number of additional rows required in the relaxed state before an admissibility-based pair is accepted;
- `subset_growth`: the base number of rows appended by tuple-based mutation; and
- `hot_value`: the probability of selecting a hot value when generating appended rows.

For every setting, SCHEMAMORPH repeatedly performs complete testing rounds under the same time budget. The evaluated settings are:

- `adm_growth`: 8, 12, 16, 32, 64, 128, and 256;
- `subset_growth`: 250, 500, 1,000, 1,500, 2,000, and 3,000; and
- `hot_value`: 0.20, 0.35, 0.40, 0.60, 0.80, and 0.92.

The evaluated implementation uses defaults of `adm_growth = 12`, `subset_growth = 500` plus a small randomized offset, and `hot_value = 0.92` for appended rows.

### 4.2 Metrics

We report four metrics:

- **Completed rounds:** state pairs that passed validation and completed observation checking;
- **Skipped rounds:** pairs rejected because they did not satisfy the required state difference;
- **Verified observations:** observations executed and checked on both states; and
- **Plan-change rate:** the fraction of verified observations whose plans differ across the two states.

Plan-change rate measures how often a configuration exercises different execution paths and is therefore used as a proxy for bug-triggering effectiveness. It is not a direct measurement of unique-bug coverage. Completed/skipped rounds and verified observations reflect testing throughput.

### 4.3 Results

#### Admissibility-growth threshold

| `adm_growth` | Completed | Skipped | Verified | Plan changes | Rate |
|---:|---:|---:|---:|---:|---:|
| 8 | 192 | 0 | 1,423 | 1,309 | 91.99% |
| **12 (default)** | 96 | 0 | 744 | 689 | 92.61% |
| 16 | 186 | 0 | 1,416 | 1,313 | 92.73% |
| 32 | 174 | 0 | 1,314 | 1,227 | 93.38% |
| 64 | 178 | 0 | 1,364 | 1,266 | 92.82% |
| 128 | 175 | 2 | 1,324 | 1,223 | 92.37% |
| 256 | 184 | 51 | 1,044 | 979 | 93.77% |

The plan-change rate is stable at 91.99--93.77%. Increasing the threshold to 256 does not materially improve this rate but causes 51 skipped rounds. Thus, a moderate threshold preserves triggering effectiveness while avoiding the loss of usable state pairs.

#### Tuple-expansion size

| `subset_growth` | Completed | Verified | Plan changes | Rate |
|---:|---:|---:|---:|---:|
| 250 | 592 | 2,754 | 2,197 | 79.77% |
| **500 (default)** | 490 | 2,246 | 1,801 | 80.19% |
| 1,000 | 346 | 1,602 | 1,282 | 80.03% |
| 1,500 | 250 | 1,243 | 1,004 | 80.77% |
| 2,000 | 249 | 1,156 | 935 | 80.88% |
| 3,000 | 142 | 676 | 555 | 82.10% |

Larger expansions slightly increase the plan-change rate, from 79.77% to 82.10%, but sharply reduce completed rounds and verified observations. The default 500-row expansion provides a better balance between plan-change yield and throughput.

#### Expansion hot-value probability

| `hot_value` | Completed | Verified | Plan changes | Rate |
|---:|---:|---:|---:|---:|
| 0.20 | 278 | 1,268 | 992 | 78.23% |
| 0.35 | 286 | 1,256 | 988 | 78.66% |
| 0.40 | 276 | 1,222 | 967 | 79.13% |
| 0.60 | 281 | 1,269 | 988 | 77.86% |
| 0.80 | 273 | 1,210 | 953 | 78.76% |
| **0.92 (default)** | 269 | 1,233 | 1,037 | 84.10% |

Settings from 0.20 to 0.80 produce similar rates of 77.86--79.13%. Raising the probability to 0.92 increases the rate to 84.10%, showing that a strongly skewed expansion is more likely to trigger plan changes.

### 4.4 Conclusion

SCHEMAMORPH is not highly sensitive to moderate changes in these parameters. The main trade-off is between throughput and triggering intensity: overly strict admissibility thresholds discard useful state pairs, and very large expansions reduce the number of completed rounds for only a small plan-change gain. A strong hot-value bias improves plan-change yield. These results justify the chosen defaults as practical trade-offs, rather than claiming that they globally maximize unique-bug discovery.
