# Supported Observation Grammar and Soundness

This document supplements Sections III-C and IV-C of the paper with (1) an implementable DQL/DML grammar, (2) a supported/unsupported construct table, and (3) a soundness proof. It instantiates the SIAMT model in Definitions III.1--III.6.

## 1. Supported DQL/DML Grammar

The notation is EBNF: `[X]` is optional and `{X}` denotes zero or more repetitions.

```ebnf
DQLObservation ::= Query
Query          ::= Select { "UNION ALL" Select }

Select         ::= "SELECT" [ "DISTINCT" ] SelectList
                   FromClause
                   [ "WHERE" PositivePredicate ]
                   [ OrderByClause ]

SelectList     ::= StableExpr { "," StableExpr }

FromClause     ::= "FROM" Relation
Relation       ::= RelationAtom { JoinTail }
RelationAtom   ::= TableName [ Alias ]
                 | "(" Query ")" Alias
JoinTail       ::= "INNER JOIN" RelationAtom "ON" RowPredicate
                 | "CROSS JOIN" RelationAtom

PositivePredicate
               ::= RowPredicate
                 | "(" PositivePredicate "AND" PositivePredicate ")"
                 | "(" PositivePredicate "OR" PositivePredicate ")"
                 | "EXISTS" "(" Query ")"
                 | StableExpr "IN" "(" SingleColumnQuery ")"

RowPredicate   ::= AtomicPredicate
                 | "(" RowPredicate "AND" RowPredicate ")"
                 | "(" RowPredicate "OR" RowPredicate ")"

AtomicPredicate
               ::= StableExpr CompareOp StableExpr
                 | StableExpr "BETWEEN" StableExpr "AND" StableExpr
                 | StableExpr "IS NULL"
                 | StableExpr "IS NOT NULL"
                 | StableExpr "LIKE" StableExpr
                 | StableExpr "IN" "(" LiteralList ")"

StableExpr     ::= ColumnRef | Literal
                 | "(" StableExpr ArithmeticOp StableExpr ")"
                 | DeterministicFunction "(" [ StableExpr
                     { "," StableExpr } ] ")"
                 | "COALESCE" "(" StableExpr "," StableExpr ")"
                 | "CASE WHEN" RowPredicate "THEN" StableExpr
                     "ELSE" StableExpr "END"
                 | "CAST" "(" StableExpr "AS" ScalarType ")"

OrderByClause  ::= "ORDER BY" StableExpr [ "ASC" | "DESC" ]
                   { "," StableExpr [ "ASC" | "DESC" ] }

DMLObservation ::= DeleteObservation | UpdateObservation
DeleteObservation
               ::= "DELETE FROM" TableName
                   "WHERE" PositivePredicate
UpdateObservation
               ::= "UPDATE" TableName
                   "SET" NonPKColumn "=" StableExpr
                   "WHERE" PositivePredicate
```

The grammar has the following semantic side conditions:

- `StableExpr` contains only row-local deterministic expressions from a DBMS-specific whitelist. It contains no aggregate, window function, volatile function, or scalar subquery.
- `IN/EXISTS` occurs only in a positive filtering context. Every inner query must recursively pass the same check; correlated references may use only stable outer-row expressions.
- Derived tables must recursively pass the same check. Nesting does not relax any rule.
- Both executions use identical types, collations, SQL modes, time zones, and relevant session settings.
- `ORDER BY` is accepted only without result truncation, and verification ignores row order.
- `UPDATE` does not modify PKs. Its assignment is deterministic and row-local. Cases involving constraint conflicts, triggers, generated-column side effects, or cascades are rejected.

`COUNT`, `MAX`, and `MIN` are restricted summaries over the output of an accepted DQL query, rather than arbitrary operators inside the grammar. `MAX/MIN` are used only for nonempty, non-all-`NULL`, totally ordered scalar results.

## 2. Supported and Unsupported Constructs

| Status | Constructs | Conditions or reason |
|---|---|---|
| Supported | Deterministic selection and projection | Expressions are stable and row-local |
| Supported | Inner, cross, and self joins | Inputs pass the same check; join predicates are row-local |
| Supported | `UNION ALL` | All branches pass the same check |
| Supported | Derived tables | Inner query passes the same recursive check |
| Supported | Positive `IN/EXISTS` | Positive filtering context; inner query passes recursively |
| Supported | `DISTINCT` | Outcome is compared using set containment |
| Supported | `ORDER BY` | No `LIMIT/OFFSET`; verification ignores order |
| Restricted summary | `COUNT`, `MAX`, `MIN` | Applied only to an accepted result; invalid `MIN/MAX` inputs are skipped |
| Supported DML | Single-table `UPDATE/DELETE` | Monotone `WHERE`; stable PK; deterministic, PK-preserving update |
| Unsupported | `LIMIT`, `OFFSET`, top-k | New rows may evict old results |
| Unsupported | `NOT IN`, `NOT EXISTS`, anti-join, `EXCEPT` | New rows may remove old results |
| Unsupported | Outer joins | New matches may replace old NULL-extended rows |
| Unsupported | Scalar subqueries in expressions | Their scalar value may change after state extension |
| Unsupported | Grouping, `HAVING`, nested/other aggregates | Required group-specific relations are not implemented |
| Unsupported | Window or nondeterministic functions | Results for preserved rows may change |
| Unsupported DML | PK updates, `INSERT`, multi-table DML, triggers/cascades | Effects cannot be compared using the current PK relation |

## 3. Soundness Proof

### Theorem 1: DQL observations preserve containment

Let the original state be a tuple-induced or admissibility-induced under-approximation of the mutated state, as defined in the paper. If a DQL observation is accepted by the grammar and checks above, then every result row produced over the original state also occurs, with at least the same multiplicity, over the mutated state.

**Proof by structural induction on the height of the SQL AST.**

**Base case.** A leaf relation is either a database table or the result of a recursively checked nested query. For a base table, containment follows from tuple-based construction or admissibility-based workload validation. Therefore every original input row is preserved in the mutated state.

**Inductive hypothesis.** Assume that every child query of an AST node preserves result containment.

**Inductive cases.**

- **Selection.** For a preserved row, every row-local deterministic expression evaluates identically in both states. Hence a predicate that is `TRUE` originally remains `TRUE`. For positive `IN/EXISTS`, an original matching witness remains present by the inductive hypothesis. Thus, no originally selected row is lost.
- **Projection.** A preserved input row produces the same projected values in both states, so every original output occurrence remains.
- **Inner/cross/self join.** Every original matching tuple combination remains available. Its row-local join predicate has the same truth value, so every original joined row remains.
- **`UNION ALL`.** If each branch preserves containment, their bag union also preserves containment.
- **`DISTINCT`.** Every original distinct value retains at least one witness, so set containment is preserved.
- **Derived table.** Its inner result preserves containment by the inductive hypothesis; the enclosing operator can therefore treat it as a contained input relation.
- **Positive `IN/EXISTS`.** An original witness produced by the inner query remains by the inductive hypothesis. New rows may create new witnesses but cannot remove an existing one.
- **`ORDER BY`.** It only permutes results. Because the verifier ignores order and truncation is forbidden, containment is unchanged.

Every accepted AST node therefore preserves containment when its children do. By structural induction, the root DQL observation preserves containment at arbitrary nesting depth. This establishes the relation-preserving observation required by Definition III.5; a violation triggers the SIAMT oracle in Definition III.6.

### Corollary: Result summaries are sound

Because the mutated result contains every original result occurrence, `COUNT` cannot decrease. For a totally ordered scalar result, the original maximum remains present, so `MAX` cannot decrease; the original minimum remains present, so `MIN` cannot increase. Empty or all-`NULL` `MIN/MAX` inputs are skipped.

### Theorem 2: DML effects preserve containment

For an accepted `UPDATE/DELETE`, every PK affected in the original state is also affected in the mutated state; newly affected PKs are allowed.

**Proof.** Choose any original row with PK `k` affected by the DML observation. The row and its PK are preserved in the mutated state. By the DQL predicate argument above, adding rows cannot make its accepted `WHERE` predicate false. Therefore, `DELETE` removes row `k` in both states. For `UPDATE`, the same deterministic row-local assignment is applied to row `k` in both states. Because PK changes, conflicts, triggers, and cascades are excluded, `k` is changed in both executions. Hence every original affected PK remains in the mutated effect set.

These theorems establish soundness only for the admitted fragment. Bugs requiring unsupported constructs, and incorrect results that nevertheless preserve the checked direction, remain outside the oracle's scope.
