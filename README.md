# SchemaMorph

SchemaMorph is a DBMS testing framework that detects logical bugs via two complementary differential oracles:

- **Tuple-based Oracle:** Generates table $S_1$, then expands it with additional data to form $S_2$ such that $S_1 \subseteq S_2$. The same queries are executed on both; results must satisfy monotonicity constraints (COUNT, MAX, MIN, row-set subset). Data distribution is deliberately shifted between $S_1$ and $S_2$ to trigger query plan changes (EXPLAIN), increasing the likelihood of exposing optimizer bugs.

- **Admissibility-based Oracle:** Creates two tables with identical columns: $S_1$ with stricter constraints (NOT NULL, UNIQUE, CHECK, FK) and $S_2$ with those constraints relaxed. The same INSERT workload is replayed into both; $S_1$ rejects some rows due to constraint violations while $S_2$ accepts all, so $S_1 \subseteq S_2$ holds. The same queries are then executed on both and the same monotonicity assertions are verified.

SchemaMorph supports **MySQL, MariaDB, Percona, OceanBase, PolarDB**.

Up to now, we have found **21** logical bugs across these systems, **18** of which have been confirmed by developers.

---

## Bug List

### MySQL

| ID | DB_Name | Issue | Status |
|----|-------|-------|--------|
| 1 | Mysql | https://bugs.mysql.com/bug.php?id=120158 | Confirmed |
| 2 | Mysql | https://bugs.mysql.com/bug.php?id=120253 | Confirmed |
| 3 | Mysql | https://bugs.mysql.com/bug.php?id=120275 | Confirmed |
| 4 | Mysql | https://bugs.mysql.com/bug.php?id=120284 | Confirmed |
| 5 | Mysql | https://bugs.mysql.com/bug.php?id=120296 | Confirmed |
| 6 | Mysql | https://bugs.mysql.com/bug.php?id=120524 | Confirmed |
| 7 | Mysql | https://bugs.mysql.com/bug.php?id=120525 | Confirmed |
| 8 | Mysql | https://bugs.mysql.com/bug.php?id=120285 | Pending |
| 9 | Mariadb | https://jira.mariadb.org/browse/MDEV-39553 | Confirmed |
| 10 | Mariadb | https://jira.mariadb.org/browse/MDEV-39717 | Pending |
| 11 | Percona | https://perconadev.atlassian.net/browse/PS-11024 | Confirmed |
| 12 | Percona | https://perconadev.atlassian.net/browse/PS-11025 | Confirmed |
| 13 | Percona | https://perconadev.atlassian.net/browse/PS-11144 | Confirmed |
| 14 | Percona | https://perconadev.atlassian.net/browse/PS-11147 | Confirmed |
| 15 | Percona | https://perconadev.atlassian.net/browse/PS-11148 | Confirmed |
| 16 | Percona | https://perconadev.atlassian.net/browse/PS-11187 | Confirmed |
| 17 | Percona | https://perconadev.atlassian.net/browse/PS-11188 | Confirmed |
| 18 | Oceanbase | https://github.com/oceanbase/oceanbase/issues/2401 | Pending |
| 19 | Polardb | https://github.com/polardb/polardbx-sql/issues/283 | Confirmed |
| 20 | Polardb | https://github.com/polardb/polardbx-sql/issues/284 | Confirmed |
| 21 | Polardb | https://github.com/polardb/polardbx-sql/issues/285 | Confirmed |


---

## Installation

```bash
pip install -r requirements.txt
```

Main dependencies:
- `sqlglot >= 18.0.0` — SQL parsing and transformation
- `pymysql >= 1.1.0` — MySQL-compatible connections

---

## Configuration and Running

Open `main.py` and configure the following variables, then run `python main.py`.

**Dialect and oracle selection:**

```python
# Choose dialect:
# 'mysql' | 'mariadb' | 'percona' | 'tidb' | 'oceanbase' | 'polardb'
dialect_str = 'mysql'

# Oracle selection
use_subset_oracle   = True    # Tuple-based Oracle
use_vertical_oracle = False   # Admissibility-based Oracle

# How long to run (hours)
run_hours = 12
```

**Database connection:**

```python
db_config = {
    'host':     '127.0.0.1',
    'port':     3306,
    'database': 'test',
    'user':     'root',
    'password': '',
    'db_type':  dialect_str,
}
```

Then run:

```bash
python main.py
```

---

## Output

| Path | Contents |
|------|----------|
| `logs/subset_oracle_<timestamp>.log` | Tuple-based Oracle run log; detected bugs recorded here |
| `logs/vertical_oracle_<timestamp>.log` | Admissibility-based Oracle run log |
| `logs/execution_log_<timestamp>.txt` | Top-level cycle log |
| `invalid_mutation/<DIALECT>/` | Suppressed / filtered mismatch records |

Each bug entry in the log contains the triggering SQL, EXPLAIN plans for both $S_1$ and $S_2$, snapshot values (COUNT, MAX, MIN, row digests), and a full SQL replay script that can reproduce the bug deterministically on a fresh database instance.

---

## Artifact Evaluation

For artifact evaluation, we recommend the following minimal campaign:

```bash
# Install dependencies
pip install -r requirements.txt

# Configure main.py: set dialect_str, db_config, run_hours = 1, then:
python main.py
```

This runs the Tuple-based Oracle against the configured DBMS for 1 hour, writing results to `logs/`. Any detected bugs appear in `logs/subset_oracle_<timestamp>.log`.

To evaluate the Admissibility-based Oracle, set `use_subset_oracle = False` and `use_vertical_oracle = True` in `main.py` before running.

### Reproducibility Notes

- SQL generation is randomized; query text will differ across runs.
- For fair comparison, keep `run_hours`, oracle type, and DBMS version consistent across runs.
