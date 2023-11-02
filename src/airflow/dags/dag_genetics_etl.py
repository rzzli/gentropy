"""Generate jinja2 template for workflow."""
from __future__ import annotations

from pathlib import Path

import yaml
from airflow.models.dag import DAG
from common_airflow import (
    create_cluster,
    delete_cluster,
    install_dependencies,
    shared_dag_args,
    shared_dag_kwargs,
    submit_step,
)

SOURCE_CONFIG_FILE_PATH = Path(__file__).parent / "configs" / "dag.yaml"
PYTHON_CLI = "cli.py"
CONFIG_NAME = "config"
CLUSTER_CONFIG_DIR = "/config"
CLUSTER_NAME = "workflow-otg-cluster"


with DAG(
    dag_id=Path(__file__).stem,
    description="Open Targets Genetics ETL workflow",
    default_args=shared_dag_args,
    **shared_dag_kwargs,
):
    assert (
        SOURCE_CONFIG_FILE_PATH.exists()
    ), f"Config path {SOURCE_CONFIG_FILE_PATH} does not exist."

    with open(SOURCE_CONFIG_FILE_PATH, "r") as config_file:
        # Parse and define all steps and their prerequisites.
        tasks = {}
        steps = yaml.safe_load(config_file)
        for step in steps:
            # Define task for the current step.
            step_id = step["id"]
            this_task = submit_step(
                cluster_name=CLUSTER_NAME,
                step_id=step_id,
            )
            # Chain prerequisites.
            tasks[step_id] = this_task
            for prerequisite in step.get("prerequisites", []):
                this_task.set_upstream(tasks[prerequisite])

        # Construct the DAG with all tasks.
        (
            create_cluster(CLUSTER_NAME)
            >> install_dependencies(CLUSTER_NAME)
            >> list(tasks.values())
            >> delete_cluster(CLUSTER_NAME)
        )
