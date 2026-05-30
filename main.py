import time

import os
import gc
from get_seedQuery import SeedQueryGenerator
from generate_random_sql import Generate
from changeAST import MutateSolve

from data_structures.db_dialect import set_dialect, DBDialect


def log_message(message, log_file=None):
    print(message)
    if log_file:
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(message + '\n')


if __name__ == '__main__':
    ##############################################################################

    dialect_str = 'monetdb'
    use_value_mutator = True
    run_hours = 12
    is_use_database_tables = False

    # Oracle switches.
    # Default behavior now runs only VerticalOracle, while keeping SubsetOracle available.
    use_subset_oracle = True
    subset_oracle_rounds = 3
    use_vertical_oracle = False
    vertical_oracle_rounds = 3
    use_original_oracle = False

    db_config = {
        'host': '127.0.0.1',
        'port': 50000,
        'database': 'test',
        'user': 'sqlancer',
        'password': 'sqlancer',
        'schema': 'sqlancer',
        'db_type': dialect_str,
    }

    ##############################################################################

    log_dir = 'logs'
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    log_filename = f"execution_log_{time.strftime('%Y%m%d_%H%M%S')}.txt"
    log_file_path = os.path.join(log_dir, log_filename)

    start_time = time.time()
    log_message(
        f"Program started execution, time: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(start_time))}",
        log_file_path,
    )

    try:
        set_dialect(dialect_str)
        log_message(f"Database dialect set to: {dialect_str}", log_file_path)
        log_message(f"Use extension: {use_value_mutator}", log_file_path)

        total_seconds = run_hours * 3600
        cycle_count = 0

        log_message(f"Starting loop execution, estimated running time: {run_hours} hours")
        log_message(f"Starting loop execution, estimated running time: {run_hours} hours", log_file_path)

        cycle_start_time = time.time()

        from oracle.subset_oracle import SubsetOracle
        from oracle.vertical_oracle import VerticalOracle

        subset_log_file = os.path.join('logs', f'subset_oracle_{time.strftime("%Y%m%d_%H%M%S")}.log')
        subset = (
            SubsetOracle(db_config=db_config, verbose=False, log_file=subset_log_file)
            if use_subset_oracle else None
        )

        vertical_log_file = os.path.join('logs', f'vertical_oracle_{time.strftime("%Y%m%d_%H%M%S")}.log')
        vertical = (
            VerticalOracle(db_config=db_config, verbose=False, log_file=vertical_log_file)
            if use_vertical_oracle else None
        )

        while time.time() - cycle_start_time < total_seconds:
            cycle_count += 1
            log_message(f"\n===== Starting cycle {cycle_count} =====")
            log_message(f"\n===== Starting cycle {cycle_count} =====", log_file_path)

            try:
                if use_original_oracle:
                    log_message("Starting to generate SQL...", log_file_path)
                    generate_start = time.time()
                    Generate(
                        subquery_depth=2,
                        total_insert_statements=40,
                        num_queries=1000,
                        query_type='default',
                        use_database_tables=is_use_database_tables,
                        db_config=db_config,
                    )
                    generate_end = time.time()
                    log_message(
                        f"SQL generation completed, time taken: {generate_end - generate_start:.2f} seconds",
                        log_file_path,
                    )

                    log_message("Starting to generate seed queries...", log_file_path)
                    seed_start = time.time()
                    seed_query_generator = SeedQueryGenerator(db_config=db_config)
                    seed_query_generator.get_seedQuery()
                    seed_end = time.time()
                    log_message(
                        f"Seed query generation completed, time taken: {seed_end - seed_start:.2f} seconds",
                        log_file_path,
                    )

                    log_message("Starting to execute presolve...", log_file_path)
                    presolve_start = time.time()
                    presolve = MutateSolve(extension=use_value_mutator)
                    presolve.mutate_main()
                    presolve_end = time.time()
                    log_message(
                        f"Presolve execution completed, time taken: {presolve_end - presolve_start:.2f} seconds",
                        log_file_path,
                    )

                if use_subset_oracle:
                    log_message("Starting SubsetOracle...", log_file_path)
                    subset_start = time.time()

                    for round_idx in range(subset_oracle_rounds):
                        try:
                            stats = subset.run()
                            if not stats['skipped']:
                                overall_change_rate = (
                                    stats['plan_changes'] / max(stats['queries'], 1) * 100
                                )
                                select_change_rate = (
                                    stats['select_plan_changes']
                                    / max(stats['select_queries'], 1)
                                    * 100
                                )
                                dml_change_rate = (
                                    stats['dml_plan_changes']
                                    / max(stats['dml_queries'], 1)
                                    * 100
                                )
                                log_message(
                                    f"  [SubsetOracle] round={stats['round_id']} | "
                                    f"verified={stats['queries']} "
                                    f"(select={stats['select_queries']}, dml={stats['dml_queries']}) | "
                                    f"plan_changes={stats['plan_changes']} ({overall_change_rate:.1f}%) | "
                                    f"select_plan_changes={stats['select_plan_changes']} "
                                    f"({select_change_rate:.1f}%) | "
                                    f"dml_plan_changes={stats['dml_plan_changes']} "
                                    f"({dml_change_rate:.1f}%) | "
                                    f"bugs={stats['bugs']}",
                                    log_file_path,
                                )
                        except Exception as e:
                            log_message(f"SubsetOracle round {round_idx + 1} error: {e}", log_file_path)

                    subset_end = time.time()

                    if subset.total_rounds > 0:
                        change_rate = subset.total_plan_changes / max(subset.total_queries, 1) * 100
                        select_change_rate = (
                            subset.total_select_plan_changes
                            / max(subset.total_select_queries, 1)
                            * 100
                        )
                        dml_change_rate = (
                            subset.total_dml_plan_changes
                            / max(subset.total_dml_queries, 1)
                            * 100
                        )
                        log_message(
                            f"SubsetOracle completed, time: {subset_end - subset_start:.2f}s | "
                            f"cumulative: rounds={subset.total_rounds} | "
                            f"verified={subset.total_queries} "
                            f"(select={subset.total_select_queries}, dml={subset.total_dml_queries}) | "
                            f"plan_changes={subset.total_plan_changes} ({change_rate:.1f}%) | "
                            f"select_plan_changes={subset.total_select_plan_changes} "
                            f"({select_change_rate:.1f}%) | "
                            f"dml_plan_changes={subset.total_dml_plan_changes} "
                            f"({dml_change_rate:.1f}%) | "
                            f"bugs={subset.total_bugs}",
                            log_file_path,
                        )

                if use_vertical_oracle:
                    log_message("Starting VerticalOracle...", log_file_path)
                    vertical_start = time.time()

                    for round_idx in range(vertical_oracle_rounds):
                        try:
                            stats = vertical.run()
                            if not stats['skipped']:
                                overall_change_rate = (
                                    stats['plan_changes'] / max(stats['queries'], 1) * 100
                                )
                                select_change_rate = (
                                    stats['select_plan_changes']
                                    / max(stats['select_queries'], 1)
                                    * 100
                                )
                                dml_change_rate = (
                                    stats['dml_plan_changes']
                                    / max(stats['dml_queries'], 1)
                                    * 100
                                )
                                log_message(
                                    f"  [VerticalOracle] round={stats['round_id']} | "
                                    f"verified={stats['queries']} "
                                    f"(select={stats['select_queries']}, dml={stats['dml_queries']}) | "
                                    f"plan_changes={stats['plan_changes']} ({overall_change_rate:.1f}%) | "
                                    f"select_plan_changes={stats['select_plan_changes']} "
                                    f"({select_change_rate:.1f}%) | "
                                    f"dml_plan_changes={stats['dml_plan_changes']} "
                                    f"({dml_change_rate:.1f}%) | "
                                    f"bugs={stats['bugs']}",
                                    log_file_path,
                                )
                        except Exception as e:
                            log_message(f"VerticalOracle round {round_idx + 1} error: {e}", log_file_path)

                    vertical_end = time.time()

                    if vertical.total_rounds > 0:
                        change_rate = vertical.total_plan_changes / max(vertical.total_queries, 1) * 100
                        select_change_rate = (
                            vertical.total_select_plan_changes
                            / max(vertical.total_select_queries, 1)
                            * 100
                        )
                        dml_change_rate = (
                            vertical.total_dml_plan_changes
                            / max(vertical.total_dml_queries, 1)
                            * 100
                        )
                        log_message(
                            f"VerticalOracle completed, time: {vertical_end - vertical_start:.2f}s | "
                            f"cumulative: rounds={vertical.total_rounds} | "
                            f"verified={vertical.total_queries} "
                            f"(select={vertical.total_select_queries}, dml={vertical.total_dml_queries}) | "
                            f"plan_changes={vertical.total_plan_changes} ({change_rate:.1f}%) | "
                            f"select_plan_changes={vertical.total_select_plan_changes} "
                            f"({select_change_rate:.1f}%) | "
                            f"dml_plan_changes={vertical.total_dml_plan_changes} "
                            f"({dml_change_rate:.1f}%) | "
                            f"bugs={vertical.total_bugs}",
                            log_file_path,
                        )

                if not use_original_oracle and not use_subset_oracle and not use_vertical_oracle:
                    log_message("Warning: all oracles are disabled, nothing to do.", log_file_path)

                cycle_end_time = time.time()
                cycle_duration = cycle_end_time - cycle_start_time
                remaining_time = total_seconds - cycle_duration

                log_message(
                    f"Cycle {cycle_count} completed, elapsed time: {cycle_duration:.2f} seconds, "
                    f"remaining time: {remaining_time:.2f} seconds"
                )
                log_message(
                    f"Cycle {cycle_count} completed, elapsed time: {cycle_duration:.2f} seconds, "
                    f"remaining time: {remaining_time:.2f} seconds",
                    log_file_path,
                )

            except Exception as e:
                log_message(f"Error occurred in cycle {cycle_count}: {str(e)}")
                log_message(f"Error occurred in cycle {cycle_count}: {str(e)}", log_file_path)
                continue
            finally:
                log_message("Cleaning memory...")

                if 'generate_start' in locals():
                    del generate_start
                if 'generate_end' in locals():
                    del generate_end
                if 'seed_start' in locals():
                    del seed_start
                if 'seed_end' in locals():
                    del seed_end
                if 'presolve_start' in locals():
                    del presolve_start
                if 'presolve_end' in locals():
                    del presolve_end
                if 'presolve' in locals():
                    del presolve
                if 'seed_query_generator' in locals():
                    del seed_query_generator
                if 'subset_start' in locals():
                    del subset_start
                if 'subset_end' in locals():
                    del subset_end
                if 'vertical_start' in locals():
                    del vertical_start
                if 'vertical_end' in locals():
                    del vertical_end

                gc.collect()
                log_message("Memory cleanup completed")

        end_time = time.time()
        total_time = end_time - start_time

        log_message("\n===== Execution Log =====")
        log_message("\n===== Execution Log =====", log_file_path)
        log_message(f"Start time: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(start_time))}")
        log_message(f"Start time: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(start_time))}", log_file_path)
        log_message(f"End time: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(end_time))}")
        log_message(f"End time: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(end_time))}", log_file_path)
        log_message(f"Total time: {total_time:.2f} seconds")
        log_message(f"Total time: {total_time:.2f} seconds", log_file_path)
        log_message(f"Completed cycles: {cycle_count}")
        log_message(f"Completed cycles: {cycle_count}", log_file_path)
        log_message(f"Log file saved to: {os.path.abspath(log_file_path)}")
        log_message(f"Log file saved to: {os.path.abspath(log_file_path)}", log_file_path)
        log_message("All work completed!")
        log_message("All work completed!", log_file_path)
        log_message("==================\n")
        log_message("==================\n", log_file_path)

    except Exception as e:
        error_time = time.time()
        log_message(f"\nError occurred during program execution: {e}", log_file_path)
        log_message(
            f"Error time: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(error_time))}",
            log_file_path,
        )
        log_message(f"Elapsed time: {error_time - start_time:.2f} seconds", log_file_path)
        log_message(f"Log file saved to: {os.path.abspath(log_file_path)}", log_file_path)
        raise
