# Additional Comparative Study: ValScope and DQP

We extend Section V-C with two closely related baselines, ValScope [21] and Differential Query Plan testing (DQP) [42]. We compare them with SCHEMAMORPH at both the tool level and the oracle level.

## 1 Baselines

- **ValScope [21].** ValScope is a value-semantics-aware metamorphic testing approach. It transforms query expressions and checks whether the resulting values preserve expected approximation relations over a fixed database state. We include it because its approximation-based oracle directly inspired SCHEMAMORPH, while the two approaches induce their relations from different dimensions: query/value transformations versus database-state transformations.

- **DQP [42].** Differential Query Plan testing executes the same query using different enforced query plans and checks result equivalence. We include it as a representative plan-oriented oracle to distinguish state-induced approximation from cross-plan differential testing.

We do not include Query Plan Guidance (QPG) [49] in the quantitative table because its evaluated targets--SQLite, TiDB, and CockroachDB--do not overlap with our evaluated DBMSs. QPG uses plan diversity to guide test generation, whereas SCHEMAMORPH uses plan changes only to prioritize observations; its correctness oracle remains state-induced approximation.

## 2 Tool-level Study

### 2.1 Setup

We use the same setup as Section V-A. For each supported DBMS, every tool is run for 24 hours using its default configuration. We report the number of unique logical bugs remaining after reproduction and deduplication. Because the tools support different DBMS sets and exercise different oracle spaces, each head-to-head comparison is restricted to commonly supported DBMSs. A dash indicates that a quantitative result is unavailable because the tool does not support.

### 2.2 Results

​                                                 **Extended Table IV. Number of logical bugs triggered in 24 hours.**

| DBMS          | NoREC |   TLP |  Radar | DDLCheck | Pinolo |   EET | ValScope |   DQP |   Ours |
| ------------- | ----: | ----: | -----: | -------: | -----: | ----: | -------: | ----: | -----: |
| MySQL         |    -- |     4 |      0 |        2 |      2 |     3 |        2 |     1 |      8 |
| MariaDB       |     0 |    -- |      0 |        0 |      1 |    -- |       -- |     0 |      2 |
| Percona       |    -- |    -- |     -- |       -- |     -- |    -- |        2 |    -- |      7 |
| PolarDB       |    -- |    -- |     -- |       -- |     -- |    -- |        2 |    -- |      3 |
| OceanBase     |     0 |    -- |     -- |       -- |      0 |    -- |       -- |    -- |      1 |
| **Total**     | **0** | **4** |  **0** |    **2** |  **3** | **3** |    **6** | **1** | **21** |
| **Increment** | **3** | **4** | **10** |    **8** |  **8** | **5** |   **12** | **9** |     -- |

`Increment` is the number of additional bugs found by SCHEMAMORPH over a baseline on their commonly evaluated DBMSs. For ValScope, the common targets are MySQL, Percona, and PolarDB: SCHEMAMORPH finds 18 bugs versus 6, giving an increment of 12. For DQP, the common target is MySQL and MariaDB: SCHEMAMORPH finds 10 bugs versus 1, giving an increment of 9. The tool-level results show that SCHEMAMORPH detects substantially more bugs under the same 24-hour budget.



## 3 Oracle-Level Reproduction Study

### 3.1 Setup

Tool-level counts alone do not show whether the baseline oracles could detect SCHEMAMORPH's bugs if given their minimized test cases. We therefore conduct an oracle-level reproduction study over all 21 SCHEMAMORPH bugs. For each minimized reproducer, we retain the same configuration required to trigger the failure. We then apply the baseline's native oracle workflow:

- **ValScope:** generate its supported value/query transformations on the relevant database state and check the expected value-level approximation relations;
- **DQP:** request the alternative plans supported by its implementation for the failing query and check cross-plan result equivalence on the same database state.

A bug is considered reproduced only if the baseline independently reports an oracle violation.

### 3.2 Results

| Oracle   | SCHEMAMORPH bugs examined | Reproduced | Not reproduced |
| -------- | ------------------------: | ---------: | -------------: |
| ValScope |                        21 |          0 |             21 |
| DQP      |                        21 |          0 |             21 |

Neither baseline reproduced any of the 21 SCHEMAMORPH bugs under its implemented oracle workflow.

ValScope keeps the database state fixed and derives approximation relations by transforming query expressions or value computation. In contrast, SCHEMAMORPH's violations require a relation between two database states produced by tuple growth or constraint relaxation. The relevant failure appears when the same observation is compared across these states; no corresponding ValScope query transformation produces this cross-state containment or DML-effect obligation. Consequently, even when ValScope exercises related expressions, its oracle does not expose the missing result or state effect induced by the state transition.

DQP requires multiple enforceable plans for the same query on a fixed state and uses equality between their results as the oracle. SCHEMAMORPH instead relies on a directional relation across non-equivalent states. Several bugs concern constraint admissibility or DML effects and therefore have no cross-plan equality oracle. For the plan-dependent cases, the faulty behavior depends jointly on state growth, data distribution, refreshed statistics, or changed constraints; DQP's available plan-enforcement mechanisms on a single fixed state did not recreate the required state-dependent execution condition. Thus, a plan-related root cause does not by itself make the bug reproducible by DQP.



## References

[21] L. Lin, L. Chen, and R. Wu, “ValScope: Value-semantics-aware metamorphic testing for detecting logical bugs in DBMSs,” in *Proceedings of the 20th USENIX Symposium on Operating Systems Design and Implementation (OSDI)*, 2026.

[42] J. Ba and M. Rigger, “Keep It Simple: Testing Databases via Differential Query Plans,” *Proceedings of the ACM on Management of Data*, vol. 2, no. 3, pp. 1–26, 2024.

[49] J. Ba and M. Rigger, “Testing Database Engines via Query Plan Guidance,” in *Proceedings of the 45th IEEE/ACM International Conference on Software Engineering (ICSE)*, pp. 2060–2071, 2023.